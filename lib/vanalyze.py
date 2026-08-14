#!/usr/bin/env python3
"""vanalyze — スタイル解析の共通部品（vstyle / vapply が共用）。

標準ライブラリのみ（憲法 §3）。重い処理は ffmpeg に委譲し、Python側は
数値の集計だけを行う。素材は読むだけ（憲法 §1）。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from array import array
from bisect import bisect_left, bisect_right
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vcommon import need, run_ffmpeg  # noqa: E402

# 色マッチングに使う百分位。端(0.02/0.98)はノイズ・黒フレームの影響を避けるため 0/1 にしない。
COLOR_QS = [0.02, 0.10, 0.25, 0.50, 0.75, 0.90, 0.98]

RE_I = re.compile(r"^\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", re.M)
RE_LRA = re.compile(r"^\s*LRA:\s*(-?[\d.]+)\s*LU", re.M)
RE_PEAK = re.compile(r"^\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", re.M)


def run_ffmpeg_bytes(args: list[str], timeout: int = 600) -> tuple[int, bytes, str]:
    """stdout をバイナリで受ける版（rawvideo / PCM の取り出し用）。"""
    cmd = [need("ffmpeg"), "-hide_banner", "-nostdin", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, b"", f"ffmpeg timeout ({timeout}s)"
    return r.returncode, r.stdout, (r.stderr or b"").decode("utf-8", "replace")


# ---------------------------------------------------------------- シーンスコア

def scene_scores(path: Path, detect_width: int = 320) -> tuple[list[tuple[float, float]], str]:
    """全フレームの (秒, scene_score) を返す。動き・画変わりの量の代理指標。

    vshots の検出と同じく縮小してから測る（高速化）。失敗時は ([], 理由)。
    """
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
        meta = Path(tf.name)
    try:
        vf = (f"scale={detect_width}:-2,"
              f"select='gte(scene,0)',"
              f"metadata=print:file={meta}")
        rc, err = run_ffmpeg(["-i", str(path), "-vf", vf, "-an", "-f", "null", "-"])
        if rc != 0:
            return [], (err.strip().splitlines() or ["ffmpeg failed"])[-1][:200]
        out: list[tuple[float, float]] = []
        t: float | None = None
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"pts_time:([0-9.]+)", line)
            if m:
                t = float(m.group(1))
                continue
            m = re.search(r"lavfi\.scene_score=([0-9.]+)", line)
            if m and t is not None:
                out.append((t, float(m.group(1))))
                t = None
        return out, ""
    finally:
        meta.unlink(missing_ok=True)


def shots_from_scores(scores: list[tuple[float, float]], duration: float,
                      threshold: float = 0.3, min_shot: float = 0.4) -> list[tuple[float, float]]:
    """シーンスコア列 → ショット区間 [(start, end), ...]。カットが無ければ1本まるごと1ショット。"""
    bounds = [0.0]
    for t in sorted(t for t, s in scores if s >= threshold and t > 0.0):
        if t - bounds[-1] >= min_shot:
            bounds.append(round(t, 3))
    if duration and duration > bounds[-1]:
        bounds.append(round(duration, 3))
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a >= 0.05]


class MotionIndex:
    """1本ぶんのスコア列を前計算し、任意の窓 [a,b) の平均スコアを速く引く。

    カット境界のスパイク（scene_score≈1.0）が「動きが多い」と誤読されないよう 0.3 で頭打ちにする。
    """

    def __init__(self, scores: list[tuple[float, float]], cap: float = 0.3):
        self.ts = [t for t, _ in scores]
        pre = [0.0]
        for _, s in scores:
            pre.append(pre[-1] + min(s, cap))
        self.pre = pre

    def mean(self, a: float, b: float) -> float:
        i = bisect_left(self.ts, a)
        j = bisect_right(self.ts, b)
        if j <= i:
            return 0.0
        return (self.pre[j] - self.pre[i]) / (j - i)


# ---------------------------------------------------------------- 色

def sample_rgb(path: Path, t: float, w: int = 64, h: int = 36) -> bytes | None:
    """時刻 t のフレームを縮小 rgb24 生データで取り出す（1枚 約7KB）。"""
    rc, data, _ = run_ffmpeg_bytes([
        "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
        "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], timeout=180)
    if rc != 0 or len(data) < w * h * 3:
        return None
    return bytes(data[: w * h * 3])


def color_stats(frames: list[bytes]) -> dict | None:
    """rgb24 フレーム群 → チャンネル別百分位・平均彩度・平均輝度。"""
    if not frames:
        return None
    hist = [[0] * 256 for _ in range(3)]
    sat_sum = 0
    luma_sum = 0
    n_px = 0
    for data in frames:
        for i in range(0, len(data) - 2, 3):
            r, g, b = data[i], data[i + 1], data[i + 2]
            hist[0][r] += 1
            hist[1][g] += 1
            hist[2][b] += 1
            mx = r if r >= g else g
            if b > mx:
                mx = b
            mn = r if r <= g else g
            if b < mn:
                mn = b
            sat_sum += mx - mn
            luma_sum += (r * 299 + g * 587 + b * 114) // 1000
            n_px += 1
    if not n_px:
        return None

    def pct(h: list[int], q: float) -> int:
        total = sum(h)
        target = q * total
        c = 0
        for v, cnt in enumerate(h):
            c += cnt
            if c >= target:
                return v
        return 255

    return {
        "qs": COLOR_QS,
        "percentiles": {ch: [pct(hist[k], q) for q in COLOR_QS]
                        for k, ch in enumerate(("r", "g", "b"))},
        "hist": {ch: hist[k] for k, ch in enumerate(("r", "g", "b"))},  # LUT生成に使う全256bin
        "mean_saturation": round(sat_sum / n_px, 1),
        "mean_luma": round(luma_sum / n_px, 1),
        "n_frames": len(frames),
    }


def sat_ratio(src: dict, ref: dict) -> float:
    """彩度の補正係数。0.7〜1.4 に制限（内装の色を壊さない＝GRADESと同じ思想）。"""
    s, r = src.get("mean_saturation", 0), ref.get("mean_saturation", 0)
    if not s or not r:
        return 1.0
    return max(0.7, min(1.4, r / s))


def color_distance(a: dict, b: dict) -> float:
    """百分位どうしの平均絶対差（0-255）。LUT適用の前後比較＝実測の物差し。"""
    total = 0
    n = 0
    for ch in ("r", "g", "b"):
        for x, y in zip(a["percentiles"][ch], b["percentiles"][ch]):
            total += abs(x - y)
            n += 1
    return round(total / n, 2) if n else 0.0


def grade_from_stats(src: dict, ref: dict) -> str:
    """素材の色分布を参考の色分布へ寄せる ffmpeg フィルタ列を生成する。

    百分位どうしの対応点を curves の制御点にする（ヒストグラムマッチングの近似）。
    完全一致ではない＝「参考の方向へ寄せる」補正。強すぎる彩度変化は 0.7〜1.4 に制限。
    """
    chans = []
    for ch in ("r", "g", "b"):
        pts: list[tuple[float, float]] = [(0.0, 0.0)]
        for sv, rv in zip(src["percentiles"][ch], ref["percentiles"][ch]):
            x, y = sv / 255.0, rv / 255.0
            lx, ly = pts[-1]
            if x <= lx + 0.02:  # xは単調増加でなければ curves が拒否する
                continue
            y = min(1.0, max(y, ly + 0.001))
            pts.append((round(x, 3), round(y, 3)))
        if pts[-1][0] < 0.995:
            pts.append((1.0, 1.0))
        chans.append(" ".join(f"{x:g}/{y:g}" for x, y in pts))
    parts = [f"curves=r='{chans[0]}':g='{chans[1]}':b='{chans[2]}'"]

    ratio = sat_ratio(src, ref)
    if abs(ratio - 1.0) > 0.03:
        parts.append(f"eq=saturation={ratio:.2f}")
    return ",".join(parts)


# ---------------------------------------------------------------- LUT (.cube)

def cdf_from_hist(hist: list[int]) -> list[float]:
    """256binヒストグラム → 累積分布 0..1。空なら一様分布（＝恒等写像になる）。"""
    total = sum(hist)
    if not total:
        return [(v + 0.5) / 256 for v in range(256)]
    out = []
    acc = 0
    for c in hist:
        acc += c
        out.append(acc / total)
    return out


IDENTITY_CDF = [(v + 0.5) / 256 for v in range(256)]


def match_map(src_cdf: list[float], ref_cdf: list[float]) -> list[int]:
    """ヒストグラムマッチング: 素材の値 v → 参考で同じ累積位置になる値。単調非減少。"""
    out = []
    j = 0
    for v in range(256):
        q = src_cdf[v]
        while j < 255 and ref_cdf[j] < q:
            j += 1
        out.append(j)
    return out


def build_cube_fn(fn, size: int = 33, title: str = "video-toolkit LUT") -> str:
    """任意の変換 fn(r,g,b)→(r,g,b)（各値 0..1）から .cube テキストを組む。

    行の並びは規格通り R が最速で回る（Resolve/FCP/ffmpeg lut3d 互換）。
    """
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}",
             "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    n = size - 1
    for b in range(size):
        for g in range(size):
            for r in range(size):
                rr, gg, bb = fn(r / n, g / n, b / n)
                lines.append(f"{min(1.0, max(0.0, rr)):.6f} "
                             f"{min(1.0, max(0.0, gg)):.6f} "
                             f"{min(1.0, max(0.0, bb)):.6f}")
    return "\n".join(lines) + "\n"


def _match_fn(maps: list[list[int]], sat: float):
    """RGB各256マップ＋彩度係数の変換関数。彩度は Rec.709 輝度を軸に回す（3D LUTである意味）。"""
    def m1(mp: list[int], x: float) -> float:
        v = x * 255.0
        i = int(v)
        if i >= 255:
            return mp[255] / 255.0
        f = v - i
        return (mp[i] * (1.0 - f) + mp[i + 1] * f) / 255.0

    def fn(r: float, g: float, b: float) -> tuple[float, float, float]:
        rr, gg, bb = m1(maps[0], r), m1(maps[1], g), m1(maps[2], b)
        if sat != 1.0:
            y = 0.2126 * rr + 0.7152 * gg + 0.0722 * bb
            rr, gg, bb = y + sat * (rr - y), y + sat * (gg - y), y + sat * (bb - y)
        return rr, gg, bb

    return fn


def cube_from_stats(src: dict | None, ref: dict, size: int = 33,
                    with_sat: bool = True, title: str = "video-toolkit LUT",
                    pre_fn=None) -> str | None:
    """色統計から .cube を作る。src=None は「素材が標準露出」という仮定の恒等入力。

    pre_fn を渡すと各格子点にまず pre_fn（例: Apple Log→709 正規化）を適用してから
    マッチングを掛ける。**その場合 src の統計は pre_fn 適用後のフレームで測ってあること**
    （呼び出し側の責任）。ref に hist（全256bin）が無い旧 style.json では None を返す。
    """
    if "hist" not in ref:
        return None
    if src is not None and "hist" not in src:
        return None
    maps = []
    for ch in ("r", "g", "b"):
        s_cdf = cdf_from_hist(src["hist"][ch]) if src is not None else IDENTITY_CDF
        maps.append(match_map(s_cdf, cdf_from_hist(ref["hist"][ch])))
    sat = sat_ratio(src, ref) if (src is not None and with_sat) else 1.0
    match = _match_fn(maps, sat)
    fn = match if pre_fn is None else (lambda r, g, b: match(*pre_fn(r, g, b)))
    return build_cube_fn(fn, size=size, title=title)


# ---------------------------------------------------------------- 音

def measure_audio(path: Path) -> dict | None:
    """ラウドネス実測（vqc と同じ ebur128）。音声が無い/測れない場合 None。"""
    rc, err = run_ffmpeg(["-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         timeout=1800)
    if rc != 0:
        return None
    mi = RE_I.search(err)
    if not mi or mi.group(1) == "-inf":
        return None
    lra = RE_LRA.search(err)
    pk = RE_PEAK.search(err)
    return {
        "lufs": float(mi.group(1)),
        "lra": float(lra.group(1)) if lra else None,
        "true_peak_dbfs": float(pk.group(1)) if pk and pk.group(1) != "-inf" else None,
    }


def estimate_bpm(path: Path, seconds: int = 90) -> dict | None:
    """BGMテンポの推定（オンセット包絡の自己相関）。

    打楽器の弱い曲・ナレーションのみの音声では外れるため、confidence を必ず併記する。
    利用側は confidence < 1.3 を「推定失敗」として扱うこと。
    """
    rc, data, _ = run_ffmpeg_bytes(["-i", str(path), "-t", str(seconds), "-vn",
                                    "-ac", "1", "-ar", "8000", "-f", "s16le", "-"])
    if rc != 0 or len(data) < 8000 * 2 * 5:  # 5秒未満は諦める
        return None
    pcm = array("h")
    pcm.frombytes(data[: len(data) // 2 * 2])
    hop = 80  # 10ms @ 8kHz
    env: list[float] = []
    for i in range(0, len(pcm) - hop, hop):
        acc = 0
        for v in pcm[i:i + hop]:
            acc += v * v
        env.append((acc / hop) ** 0.5)
    onset = [b - a for a, b in zip(env, env[1:]) if True]
    onset = [o if o > 0 else 0.0 for o in onset]
    mean_o = sum(onset) / len(onset) if onset else 0.0
    if mean_o <= 1e-9:
        return None
    onset = [o / mean_o for o in onset]
    n = len(onset)
    scores: list[tuple[int, float]] = []
    for lag in range(30, 101):  # 200〜60 BPM（10ms刻み）
        cnt = n - lag
        if cnt < 100:
            break
        s = 0.0
        for i in range(cnt):
            s += onset[i] * onset[i + lag]
        scores.append((lag, s / cnt))
    if not scores:
        return None
    avg = sum(s for _, s in scores) / len(scores)
    lag, peak = max(scores, key=lambda x: x[1])
    conf = peak / avg if avg > 0 else 0.0
    bpm = 6000.0 / lag
    while bpm < 70:  # 常用域 70〜160 に折りたたむ（倍・半分の曖昧さは残る）
        bpm *= 2
    while bpm > 160:
        bpm /= 2
    return {"bpm": round(bpm, 1), "confidence": round(conf, 2)}


def pick_evenly(seq: list, k: int) -> list:
    """列から k 個を等間隔に選ぶ（全部は要らないが偏りたくないとき用）。"""
    if len(seq) <= k:
        return list(seq)
    step = len(seq) / k
    return [seq[int(i * step)] for i in range(k)]
