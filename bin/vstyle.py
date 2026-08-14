#!/usr/bin/env python3
"""vstyle — 参考動画の「編集スタイル」を数値化して style.json に固める。

数値化できるもの: カットのリズム（ショット数・尺の分布・序盤/終盤の緩急）、
色（RGB百分位・彩度・輝度）、音（LUFS・LRA・BPM推定）、フォーマット（解像度/fps）。
数値化できないもの: 被写体の選び方・ストーリー構成・カメラワーク・テロップ。
→ style.json は「参考に寄せる採寸表」であって、参考の完全な複製ではない。

参考動画も顧客素材と同じ扱い（読むだけ・ネットに出さない）。
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from vcommon import (  # noqa: E402
    duration_sec, first_stream, hhmmss, log, out_dir, parse_fraction, probe,
    report_paths, write_json,
)
from vanalyze import (  # noqa: E402
    color_stats, estimate_bpm, measure_audio, pick_evenly, sample_rgb,
    scene_scores, shots_from_scores,
)

BPM_MIN_CONF = 1.3  # これ未満は「テンポ不明」として扱う


def pacing_quarters(shots: list[tuple[float, float]], duration: float) -> list[float | None]:
    """完成尺を4等分し、各区間に始点があるショットの平均尺。緩急（序盤ゆっくり→終盤畳み掛け等）を写す。"""
    if not duration:
        return [None] * 4
    buckets: list[list[float]] = [[], [], [], []]
    for a, b in shots:
        q = min(3, int(a / duration * 4))
        buckets[q].append(b - a)
    return [round(statistics.mean(bk), 2) if bk else None for bk in buckets]


def main() -> int:
    ap = argparse.ArgumentParser(description="参考動画の編集スタイルを数値化する")
    ap.add_argument("reference", help="参考動画（単一ファイル）")
    ap.add_argument("--out", help="出力先（既定: ~/dev/video-toolkit/out/<名前>/style）")
    ap.add_argument("--threshold", type=float, default=0.3, help="カット検出のしきい値 0-1（既定 0.3）")
    ap.add_argument("--min-shot", type=float, default=0.4, help="ショットと見なす最短秒（既定 0.4）")
    ap.add_argument("--max-color-frames", type=int, default=24, help="色サンプルの最大フレーム数（既定 24）")
    args = ap.parse_args()

    ref = Path(args.reference).expanduser().resolve()
    if not ref.is_file():
        log(f"エラー: 参考動画が見つかりません: {ref}")
        return 1
    info = probe(ref)
    if "error" in info:
        log(f"エラー: 読めません: {info['error']}")
        return 1
    v = first_stream(info, "video")
    if not v:
        log("エラー: 映像ストリームがありません")
        return 1
    a = first_stream(info, "audio")
    dur = duration_sec(info) or 0.0

    log(f"参考動画: {ref.name}  ({hhmmss(dur)} / {v.get('width')}x{v.get('height')})")

    # --- カットのリズム ---
    log("カット検出中 …")
    scores, err = scene_scores(ref)
    if err:
        log(f"エラー: シーンスコアが取れません: {err}")
        return 1
    shots = shots_from_scores(scores, dur, args.threshold, args.min_shot)
    durs = [b - a2 for a2, b in shots]
    log(f"  {len(shots)}ショット / 平均 {statistics.mean(durs):.2f}秒" if durs else "  ショットなし")

    # --- 色（各ショットの中間フレームから採る。カット境界のブレを避ける） ---
    log("色を採寸中 …")
    mids = pick_evenly([(sa + sb) / 2 for sa, sb in shots], args.max_color_frames)
    frames = [f for t in mids if (f := sample_rgb(ref, t)) is not None]
    color = color_stats(frames)
    if color:
        log(f"  {color['n_frames']}フレーム / 平均輝度 {color['mean_luma']} / 平均彩度 {color['mean_saturation']}")
    else:
        log("  ! 色サンプル取得に失敗（apply では色補正なしになる）")

    # --- 音 ---
    audio = None
    if a:
        log("音を測定中 …")
        loud = measure_audio(ref)
        bpm = estimate_bpm(ref)
        bpm_ok = bool(bpm and bpm["confidence"] >= BPM_MIN_CONF)
        audio = {
            "lufs": loud["lufs"] if loud else None,
            "lra": loud["lra"] if loud else None,
            "true_peak_dbfs": loud["true_peak_dbfs"] if loud else None,
            "bpm": bpm["bpm"] if bpm_ok else None,
            "bpm_confidence": bpm["confidence"] if bpm else None,
        }
        if loud:
            log(f"  {loud['lufs']} LUFS / LRA {loud['lra']}")
        log(f"  テンポ推定: {bpm['bpm']} BPM (確度 {bpm['confidence']})" if bpm_ok
            else "  テンポ推定: 不明（打楽器が弱い/ナレーション主体）")
    else:
        log("音声トラックなし")

    d = out_dir(ref, args.out, "style")
    style = {
        "version": 1,
        "source": str(ref),
        "format": {
            "width": v.get("width"), "height": v.get("height"),
            "fps": parse_fraction(v.get("avg_frame_rate")),
            "duration_sec": dur,
        },
        "rhythm": {
            "threshold": args.threshold,
            "shot_count": len(shots),
            "mean_shot_sec": round(statistics.mean(durs), 3) if durs else None,
            "median_shot_sec": round(statistics.median(durs), 3) if durs else None,
            "min_shot_sec": round(min(durs), 3) if durs else None,
            "max_shot_sec": round(max(durs), 3) if durs else None,
            "pacing_quarters": pacing_quarters(shots, dur),
            "shots": [{"start": round(sa, 3), "end": round(sb, 3), "dur": round(sb - sa, 3)}
                      for sa, sb in shots],
        },
        "color": color,
        "audio": audio,
    }
    write_json(d / "style.json", style)

    r = style["rhythm"]
    pq = r["pacing_quarters"]
    warm = ""
    if color:
        p = color["percentiles"]
        mid = len(p["r"]) // 2
        diff = p["r"][mid] - p["b"][mid]
        warm = "暖色寄り" if diff > 8 else ("寒色寄り" if diff < -8 else "ニュートラル")
    md = [
        f"# 編集スタイル分析 — {ref.name}", "",
        f"- 完成尺 **{hhmmss(dur)}** / {style['format']['width']}x{style['format']['height']}"
        f" / {style['format']['fps']}fps",
        f"- カット **{r['shot_count']}本** / 平均 **{r['mean_shot_sec']}秒**"
        f"（中央値 {r['median_shot_sec']} / 最短 {r['min_shot_sec']} / 最長 {r['max_shot_sec']}）",
        f"- 緩急（4等分の平均ショット長）: {' → '.join(str(x) if x else '-' for x in pq)} 秒",
    ]
    if color:
        md.append(f"- 色: 平均輝度 {color['mean_luma']}/255・平均彩度 {color['mean_saturation']}・{warm}")
    if audio:
        md.append(f"- 音: {audio['lufs']} LUFS / LRA {audio['lra']}"
                  + (f" / 推定 {audio['bpm']} BPM" if audio["bpm"] else " / テンポ不明"))
    md += ["",
           "## 次の一手", "",
           "```",
           f"bash ~/dev/video-toolkit/v apply <素材フォルダ> --style {d / 'style.json'}",
           "```", "",
           "※ このファイルが写せるのは **リズム・色・音量・テンポ** まで。",
           "  被写体の選び方・ストーリー構成・カメラワークは写せない（applyのたたき台を人間が直す）。"]
    (d / "style.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    report_paths([d / "style.json", d / "style.md"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
