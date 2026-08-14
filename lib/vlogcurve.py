#!/usr/bin/env python3
"""vlogcurve — Apple Log / Apple Log 2 の復号と Rec.709 への正規化。標準ライブラリのみ。

出典（推測で定数を書かない・変更時は必ず一次資料と突合すること）:
- Apple Log Profile White Paper (2023-09): カーブ定数 R0/Rt/c/β/γ/δ・色域 Rec.2020
- Apple Log 2 White Paper (2025-09): **カーブは Apple Log と同一**・色域は Apple Wide Gamut。
  公表アンカー（10bit full range）: 0%反射→code 154 / 18%→500 / 90%→697 / 1200%→1023
  → tests/run.sh がこのアンカーで定数を機械検証している（定数をいじると落ちる）
- AWG→AP0(ACES2065-1) 行列: OpenColorIO-Config-ACES issue #163（白書からの引用値）
- AP0→Rec.709(D65) 行列: ACES 標準（Bradford 適応）
- BT.2020→BT.709 行列: ITU-R BT.2087

トーンマップは Extended Reinhard（白点4.0）＝**実用近似**。Apple/Resolve 公式の
変換と完全一致ではない（マッチLUT用途では後段のヒストマッチが吸収する）。
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- カーブ（Log 1/2 共通）

R0 = -0.05641088
RT = 0.01
C = 47.28711236
BETA = 0.00964052
GAMMA = 0.08550479
DELTA = 0.69336945
PT = C * (RT - R0) ** 2  # 符号化側しきい値（≈0.1508）


def log_encode(R: float) -> float:
    """シーンリニア → Apple Log 符号値 [0,1]。テスト・疑似log素材生成用。"""
    if R >= RT:
        return GAMMA * math.log2(R + BETA) + DELTA
    if R >= R0:
        return C * (R - R0) ** 2
    return 0.0


def log_decode(P: float) -> float:
    """Apple Log 符号値 → シーンリニア（18%グレー=0.18）。"""
    if P >= PT:
        return 2.0 ** ((P - DELTA) / GAMMA) - BETA
    if P <= 0.0:
        return R0
    return math.sqrt(P / C) + R0


# ---------------------------------------------------------------- 行列

AWG_TO_AP0 = [
    [0.694961049318096, 0.241405268785364, 0.06363368189654],
    [0.0473627464149325, 1.00429592505428, -0.0516586714692158],
    [-0.021989789359883, -0.0289891049714743, 1.05097889433136],
]
AP0_TO_709 = [
    [2.52168618674388, -1.13413098823972, -0.387555198504164],
    [-0.276479914229922, 1.37271908766826, -0.096239173832339],
    [-0.0153780649660342, -0.152975335867399, 1.16835340083344],
]
BT2020_TO_709 = [
    [1.660491, -0.587641, -0.072850],
    [-0.124550, 1.132900, -0.008349],
    [-0.018151, -0.100579, 1.118730],
]


def matmul3(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def invert3(m: list[list[float]]) -> list[list[float]]:
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise ValueError("行列が特異です")
    adj = [
        [e * i - f * h, c * h - b * i, b * f - c * e],
        [f * g - d * i, a * i - c * g, c * d - a * f],
        [d * h - e * g, b * g - a * h, a * e - b * d],
    ]
    return [[v / det for v in row] for row in adj]


# 収録色域 → Rec.709 リニアの合成行列
MATS = {
    "apple-log2": matmul3(AP0_TO_709, AWG_TO_AP0),   # AWG → AP0 → Rec.709
    "apple-log": BT2020_TO_709,                       # Rec.2020 → Rec.709
}
LOG_KINDS = tuple(MATS.keys())

# ---------------------------------------------------------------- トーンマップ・OETF

TONE_W = 4.0  # 白点: リニア4.0(400%)→表示1.0。Apple Logの上限1200%はソフトに漸近


def tonemap(L: float) -> float:
    L = max(0.0, L)
    return L * (1.0 + L / (TONE_W * TONE_W)) / (1.0 + L)


def tonemap_inv(y: float) -> float:
    y = min(max(y, 0.0), 0.999)
    a = 1.0 / (TONE_W * TONE_W)
    b = 1.0 - y
    return (-b + math.sqrt(b * b + 4.0 * a * y)) / (2.0 * a)


def oetf709(L: float) -> float:
    L = min(max(L, 0.0), 1.0)
    return 4.5 * L if L < 0.018 else 1.099 * (L ** 0.45) - 0.099


def eotf709(V: float) -> float:
    V = min(max(V, 0.0), 1.0)
    return V / 4.5 if V < 0.081 else ((V + 0.099) / 1.099) ** (1.0 / 0.45)


# ---------------------------------------------------------------- 正規化パイプライン

def normalize_fn(kind: str):
    """log符号値RGB [0,1]³ → Rec.709表示RGB [0,1]³ の関数を返す。"""
    M = MATS[kind]

    def fn(r: float, g: float, b: float) -> tuple[float, float, float]:
        lr, lg, lb = log_decode(r), log_decode(g), log_decode(b)
        out = []
        for row in M:
            x = row[0] * lr + row[1] * lg + row[2] * lb
            out.append(oetf709(tonemap(x)))
        return out[0], out[1], out[2]

    return fn


def logify_fn(kind: str):
    """逆変換（Rec.709表示 → log符号値）。疑似log素材の生成＝テスト専用。"""
    Minv = invert3(MATS[kind])

    def fn(r: float, g: float, b: float) -> tuple[float, float, float]:
        L = [tonemap_inv(eotf709(v)) for v in (r, g, b)]
        out = []
        for row in Minv:
            x = row[0] * L[0] + row[1] * L[1] + row[2] * L[2]
            out.append(min(1.0, max(0.0, log_encode(x))))
        return out[0], out[1], out[2]

    return fn


def transform_frames(frames: list[bytes], fn) -> list[bytes]:
    """rgb24フレーム群に画素単位で fn を適用（色統計を「正規化後」で測るため）。"""
    out = []
    for data in frames:
        ba = bytearray(len(data))
        for i in range(0, len(data) - 2, 3):
            r, g, b = fn(data[i] / 255.0, data[i + 1] / 255.0, data[i + 2] / 255.0)
            ba[i] = min(255, max(0, int(r * 255.0 + 0.5)))
            ba[i + 1] = min(255, max(0, int(g * 255.0 + 0.5)))
            ba[i + 2] = min(255, max(0, int(b * 255.0 + 0.5)))
        out.append(bytes(ba))
    return out


def looks_like_log(info: dict) -> bool:
    """ffprobe結果から log 収録の可能性を推定（確定ではない＝警告表示用）。"""
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    return v.get("color_primaries") in ("bt2020", "bt2020nc", "bt2020c") or \
        v.get("color_transfer") in ("arib-std-b67", "smpte2084")
