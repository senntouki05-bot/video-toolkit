#!/usr/bin/env python3
"""vlut — 参考動画から .cube 3D LUT を自動生成する（Resolve / FCP / ffmpeg 互換）。

2モード:
  v lut <参考動画> --src <素材>   … マッチLUT（素材の色分布→参考の色分布。こちらが本命）
  v lut <参考動画>                … ルックLUT（「素材が標準的な露出・ニュートラル」という
                                     仮定つき。log素材やWBが大きく外れた素材には合わない）

参考には動画ファイルのほか、vstyle が出した style.json も渡せる（再解析なしで速い）。
--src 指定時は生成後に LUT を実際に当てて色距離の前後を実測する（数字で効きを示す）。
素材は読むだけ（憲法 §1）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from vcommon import (  # noqa: E402
    collect_media, duration_sec, first_stream, log, out_dir, probe,
    report_paths,
)
from vanalyze import (  # noqa: E402
    color_distance, color_stats, cube_from_stats, pick_evenly, run_ffmpeg_bytes,
    sample_rgb, sat_ratio,
)


def sample_video_stats(files: list[Path], n_frames: int) -> dict | None:
    """複数ファイルから等間隔にフレームを採って色統計を取る。"""
    plan: list[tuple[Path, float]] = []
    for f in files:
        dur = duration_sec(probe(f)) or 0.0
        if dur <= 0:
            continue
        per = max(3, n_frames // len(files))
        plan += [(f, dur * (i + 0.5) / per) for i in range(per)]
    plan = pick_evenly(plan, n_frames)
    frames = [fr for p, t in plan if (fr := sample_rgb(p, t)) is not None]
    return color_stats(frames)


def sample_rgb_lut(path: Path, t: float, cube: Path, w: int = 64, h: int = 36) -> bytes | None:
    """LUT を当てた状態のフレームを採る（前後比較の実測用）。"""
    rc, data, _ = run_ffmpeg_bytes([
        "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
        "-vf", f"lut3d='{cube}',scale={w}:{h}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], timeout=180)
    if rc != 0 or len(data) < w * h * 3:
        return None
    return bytes(data[: w * h * 3])


def main() -> int:
    ap = argparse.ArgumentParser(description="参考動画から .cube 3D LUT を自動生成")
    ap.add_argument("reference", help="参考動画 または vstyle の style.json")
    ap.add_argument("--src", help="素材フォルダ/ファイル（指定すると本命のマッチLUTになる）")
    ap.add_argument("--out", help="出力先（既定: ~/dev/video-toolkit/out/<名前>/lut）")
    ap.add_argument("--size", type=int, default=33, choices=(17, 33, 65),
                    help="LUTの格子サイズ（既定 33）")
    ap.add_argument("--frames", type=int, default=24, help="色サンプルの最大フレーム数（既定 24）")
    ap.add_argument("--no-sat", action="store_true", help="彩度補正を入れない（色相/明暗のみ）")
    args = ap.parse_args()

    ref_path = Path(args.reference).expanduser().resolve()
    if not ref_path.exists():
        log(f"エラー: 見つかりません: {ref_path}")
        return 1

    # --- 参考の色統計 ---
    if ref_path.suffix.lower() == ".json":
        style = json.loads(ref_path.read_text(encoding="utf-8"))
        ref_color = style.get("color")
        ref_name = Path(style.get("source", ref_path)).name
        if not ref_color or "hist" not in ref_color:
            log("エラー: この style.json は旧版でヒストグラムを持ちません。"
                "`v style` を再実行してから使ってください。")
            return 1
        log(f"参考: {ref_name}（style.json から読込）")
    else:
        if not first_stream(probe(ref_path), "video"):
            log("エラー: 参考に映像ストリームがありません")
            return 1
        ref_name = ref_path.name
        log(f"参考: {ref_name} を採寸中 …")
        ref_color = sample_video_stats([ref_path], args.frames)
        if not ref_color:
            log("エラー: 参考の色サンプルに失敗")
            return 1
    log(f"  参考の色: 輝度{ref_color['mean_luma']}/255・彩度{ref_color['mean_saturation']}")

    # --- 素材の色統計（マッチLUTのとき） ---
    src_color = None
    src_files: list[Path] = []
    if args.src:
        src_target = Path(args.src).expanduser().resolve()
        src_files = [f for f in collect_media(src_target)
                     if f != ref_path and first_stream(probe(f), "video")]
        if not src_files:
            log(f"エラー: 素材が見つかりません: {src_target}")
            return 1
        log(f"素材: {len(src_files)}本を採寸中 …")
        src_color = sample_video_stats(src_files, args.frames)
        if not src_color:
            log("エラー: 素材の色サンプルに失敗")
            return 1
        log(f"  素材の色: 輝度{src_color['mean_luma']}/255・彩度{src_color['mean_saturation']}")

    # --- LUT生成 ---
    kind = "match" if src_color else "look"
    title = f"video-toolkit {kind} LUT <- {ref_name}"
    text = cube_from_stats(src_color, ref_color, size=args.size,
                           with_sat=not args.no_sat, title=title)
    if text is None:
        log("エラー: LUTを作れませんでした（色統計にヒストグラムが無い）")
        return 1

    base = Path(args.src).expanduser().resolve() if args.src else ref_path
    d = out_dir(base, args.out, "lut")
    cube = d / f"{kind}.cube"
    cube.write_text(text, encoding="utf-8")
    log(f"\nLUT生成: {kind}.cube（{args.size}³ 格子"
        + (f" / 彩度×{sat_ratio(src_color, ref_color):.2f}" if src_color and not args.no_sat else "")
        + "）")

    # --- 実測: LUTを当てて色距離が縮んだかを数字で確認（マッチLUTのみ） ---
    verify = None
    if src_color:
        log("実測中: LUT適用後の色距離 …")
        plan = []
        for f in src_files[:3]:
            dur = duration_sec(probe(f)) or 0.0
            plan += [(f, dur * q) for q in (0.25, 0.5, 0.75) if dur > 0]
        after_frames = [fr for p, t in plan if (fr := sample_rgb_lut(p, t, cube)) is not None]
        after = color_stats(after_frames)
        if after:
            before_d = color_distance(src_color, ref_color)
            after_d = color_distance(after, ref_color)
            verify = {"before": before_d, "after": after_d}
            mark = "改善" if after_d < before_d else "⚠️ 改善せず（素材と参考の色が既に近い可能性）"
            log(f"  参考との色距離: {before_d} → {after_d}（{mark}）")
        else:
            log("  ! 適用後サンプルの取得に失敗（LUT自体は生成済み）")

    # --- 使い方メモ ---
    md = [f"# 自動生成LUT — {kind}.cube", "",
          f"- 参考: `{ref_name}` / 格子 {args.size}³"
          + (f" / 素材 {len(src_files)}本からのマッチLUT" if src_color
             else " / **ルックLUT（素材が標準露出という仮定つき）**"), ""]
    if verify:
        md += [f"- 実測: 参考との色距離 **{verify['before']} → {verify['after']}**（0-255の平均絶対差）", ""]
    md += ["## 使い方", "",
           "**DaVinci Resolve**: カラーページ → ノードを右クリック → LUT → このファイルを選択",
           "（LUTブラウザに出すには 環境設定 → カラーマネジメント → LUTフォルダへコピー）", "",
           "**ffmpeg / v cut**: cutlist.json の `output.grade` に下を入れる:",
           "```", f"lut3d='{cube}'", "```", "",
           "## 注意", "",
           "- ヒストグラムマッチングの近似＝「参考の方向へ寄せる」補正。完全一致ではない。",
           "- log収録素材（Apple Log等）には**先にカメラの変換LUTを当ててから**これを使う。",
           "- 屋内外が混在する素材では、シーンごとに `--src` を分けて作る方が精度が出る。"]
    if not src_color:
        md += ["- ルックLUTは素材の露出/WBが標準から外れているほど破綻する。"
               "本番では `--src 素材フォルダ` のマッチLUTを推奨。"]
    (d / "lut.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    report_paths([cube, d / "lut.md"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
