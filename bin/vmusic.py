#!/usr/bin/env python3
"""vmusic — Apple Loops のステムを重ねて BGM を1本に組み上げる。

なぜループを1本そのまま使わないか:
Apple のソフトウェア使用許諾は「ループを素材として**自分のオリジナル楽曲を構成する**」
使い方を許諾している（ループ単体をそのまま楽曲として配布する使い方ではない）。
複数レイヤーを構成して1曲にするのが、権利上も音楽的にも正しい。

⚠️ テンポの合わないループを混ぜない。同じ尺（＝同じBPMの同じ小節数）のものだけを重ねる。
   例) 8.727秒 = 110BPM 4小節 ／ 4.364秒 = 110BPM 2小節。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from vcommon import duration_sec, log, probe, report_paths, run_ffmpeg  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Apple Loops から BGM を組む")
    ap.add_argument("spec", help="曲の設計 JSON")
    ap.add_argument("-o", "--output", help="出力 wav（既定: specと同じ場所）")
    args = ap.parse_args()

    p = Path(args.spec).expanduser().resolve()
    s = json.loads(p.read_text(encoding="utf-8"))
    out = Path(args.output).expanduser().resolve() if args.output else p.with_suffix(".wav")
    total = float(s["duration"])
    layers = s["layers"]

    # 事前検査: 実在と、テンポ整合（尺が基準ループの整数倍/約数か）
    base = None
    ng = []
    for i, L in enumerate(layers, 1):
        src = Path(L["src"])
        if not src.exists():
            ng.append(f"レイヤー{i}: 見つからない {src}")
            continue
        d = duration_sec(probe(src))
        L["_dur"] = d
        if d is None:
            ng.append(f"レイヤー{i}: 尺が読めない {src.name}")
            continue
        if base is None:
            base = d
        else:
            r = base / d if d < base else d / base
            if abs(r - round(r)) > 0.02:
                ng.append(f"レイヤー{i}: テンポ不整合 {src.name} = {d:.3f}s（基準 {base:.3f}s の整数比でない）")
    if ng:
        for m in ng:
            log(f"エラー: {m}")
        return 1

    ins, parts, labels = [], [], []
    for i, L in enumerate(layers):
        st, en = float(L.get("start", 0)), float(L.get("end", total))
        seg = max(0.0, min(en, total) - st)
        if seg <= 0:
            continue
        ins += ["-stream_loop", "-1", "-i", L["src"]]
        g = float(L.get("gain_db", 0))
        fi, fo = float(L.get("fade_in", 0.5)), float(L.get("fade_out", 1.0))
        f = (f"[{len(labels)}:a]atrim=0:{seg:.3f},asetpts=N/SR/TB,"
             f"volume={g}dB")
        if fi > 0:
            f += f",afade=t=in:st=0:d={fi}"
        if fo > 0:
            f += f",afade=t=out:st={max(0.0, seg - fo):.3f}:d={fo}"
        if st > 0:
            f += f",adelay={int(st * 1000)}|{int(st * 1000)}"
        f += f"[a{len(labels)}]"
        parts.append(f)
        labels.append(f"[a{len(labels)}]")

    if not labels:
        log("エラー: 有効なレイヤーがありません")
        return 1

    lufs = s.get("target_lufs", -16)
    tail = float(s.get("tail_fade", 3.0))
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[mixed]")
    parts.append(f"[mixed]atrim=0:{total:.3f},"
                 f"afade=t=out:st={max(0.0, total - tail):.3f}:d={tail}[body]")

    def render(extra: str, dest: Path) -> tuple[int, str]:
        chain = ";".join(parts + ([f"[body]{extra}[out]"] if extra else []))
        lbl = "[out]" if extra else "[body]"
        return run_ffmpeg(ins + ["-filter_complex", chain, "-map", lbl,
                                 "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
                                 "-y", str(dest)], timeout=900)

    # ⚠️ loudnorm を使わない。1パスは動的圧縮で編曲の起伏が消え、2パス linear=true も
    #    実測では動的モードに落ちて LRA 6.6→2.2 に潰れた（2026-08-15 実測）。
    #    「測って固定ゲインを掛ける」だけなら、構造上ダイナミクスは絶対に変わらない。
    log(f"{len(labels)}レイヤー / {total:.1f}秒 — 音量を測定中 …")
    rc, err = run_ffmpeg(ins + ["-filter_complex",
                                ";".join(parts + ["[body]ebur128=peak=true[eb]"]),
                                "-map", "[eb]", "-f", "null", "-"], timeout=900)
    import re as _re
    mi = _re.search(r"^\s*I:\s*(-?[\d.]+)\s*LUFS", err, _re.M)
    mp = _re.search(r"^\s*Peak:\s*(-?[\d.]+)\s*dBFS", err, _re.M)
    if rc == 0 and mi:
        cur = float(mi.group(1))
        gain = lufs - cur
        peak = float(mp.group(1)) if mp else None
        if peak is not None and peak + gain > -1.5:  # リミッタに頼らず先に頭を下げる
            gain = -1.5 - peak
            log(f"  ピーク {peak}dBFS のためゲインを {gain:+.2f}dB に制限（目標音量に届かない）")
        log(f"  実測 {cur} LUFS → {gain:+.2f}dB の固定ゲイン")
        extra = f"volume={gain:.2f}dB"
    else:
        extra = "alimiter=limit=0.841:level=false"
        log("  ! 測定に失敗したのでリミッタのみ（音量は目標に届かない可能性あり）")
    rc, err = render(extra, out)
    if rc != 0:
        log("エラー: 合成に失敗")
        log((err or "").strip()[-1200:])
        return 1

    got = duration_sec(probe(out))
    log(f"完了: {got:.2f}秒")
    report_paths([out])
    return 0


if __name__ == "__main__":
    sys.exit(main())
