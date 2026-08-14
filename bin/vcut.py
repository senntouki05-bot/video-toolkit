#!/usr/bin/env python3
"""vcut — カットリスト(JSON)を読んで実際に書き出す。

「編集の判断」はカットリストに書いてある人間可読なデータで、このスクリプトはそれを
忠実に実行するだけ。尺を1秒伸ばしたければ JSON の数字を1つ直して再実行する。
素材は読むだけ（憲法 §1）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from vcommon import duration_sec, hhmmss, log, probe, report_paths, run_ffmpeg  # noqa: E402


def build(cl: dict, out_path: Path, dry: bool) -> tuple[list[str], float]:
    o = cl.get("output", {})
    w, h, fps = o.get("width", 1080), o.get("height", 1920), o.get("fps", 30)
    mute = o.get("audio", "mute") == "mute"
    crf = o.get("crf", 18)

    srcs: list[str] = []
    for c in cl["clips"]:
        if c["src"] not in srcs:
            srcs.append(c["src"])

    parts, labels, total = [], [], 0.0
    for i, c in enumerate(cl["clips"]):
        idx = srcs.index(c["src"])
        dur = float(c["out"]) - float(c["in"])
        if dur <= 0:
            raise ValueError(f"カット{i + 1}: out が in 以下です（{c['in']} → {c['out']}）")
        total += dur
        parts.append(
            f"[{idx}:v]trim=start={c['in']}:end={c['out']},setpts=PTS-STARTPTS,"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps}[v{i}]"
        )
        labels.append(f"[v{i}]")
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[v]")

    args = []
    for s in srcs:
        args += ["-i", s]
    args += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    args += ["-an"] if mute else []
    args += ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(out_path)]
    if dry:
        log("（DRY_RUN）実行しない ffmpeg 引数:")
        log("  " + " ".join(args)[:2000])
    return args, total


def sheet(cl: dict, total: float) -> str:
    md = [f"# カット表 — {cl.get('name', '')}", "",
          f"- カット数 **{len(cl['clips'])}** / 合計 **{total:.1f}秒**", "",
          "| # | 素材 | IN | OUT | 尺 | 内容 |", "|---|---|---|---|---|---|"]
    t = 0.0
    for i, c in enumerate(cl["clips"], 1):
        d = float(c["out"]) - float(c["in"])
        md.append(f"| {i} | {Path(c['src']).name} | {hhmmss(float(c['in']))} | "
                  f"{hhmmss(float(c['out']))} | {d:.1f}s | {c.get('note', '')} |")
        t += d
    md += ["", "※ IN/OUT は**素材内**のタイムコード。完成尺の並びは上から順。"]
    return "\n".join(md) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="カットリストJSONを書き出す")
    ap.add_argument("cutlist", help="カットリスト JSON")
    ap.add_argument("-o", "--output", help="出力mp4（既定: カットリストと同じ場所）")
    ap.add_argument("--dry-run", action="store_true", help="検査のみ・書き出さない")
    args = ap.parse_args()

    p = Path(args.cutlist).expanduser().resolve()
    cl = json.loads(p.read_text(encoding="utf-8"))
    out_path = Path(args.output).expanduser().resolve() if args.output else p.with_suffix(".mp4")

    # 事前検査: 素材が実在し、IN/OUT が素材の尺に収まっているか
    ng = []
    for i, c in enumerate(cl["clips"], 1):
        src = Path(c["src"])
        if not src.exists():
            ng.append(f"カット{i}: 素材が無い {src}")
            continue
        d = duration_sec(probe(src))
        if d is not None and float(c["out"]) > d + 0.05:
            ng.append(f"カット{i}: OUT {c['out']}s が素材の尺 {d:.2f}s を超えている（{src.name}）")
    if ng:
        for m in ng:
            log(f"エラー: {m}")
        return 1

    ff, total = build(cl, out_path, args.dry_run)
    md_path = p.with_suffix(".md")
    md_path.write_text(sheet(cl, total), encoding="utf-8")
    log(f"カット {len(cl['clips'])}本 / 合計 {total:.1f}秒")
    if args.dry_run:
        report_paths([md_path])
        return 0

    log("書き出し中 …")
    rc, err = run_ffmpeg(ff, timeout=3600)
    if rc != 0:
        log("エラー: 書き出しに失敗")
        log((err or "").strip()[-1200:])
        return 1

    got = duration_sec(probe(out_path))
    log(f"完了: {hhmmss(got)}（想定 {total:.1f}秒）")
    if got is not None and abs(got - total) > 0.5:
        log(f"  ! 想定と {abs(got - total):.2f}秒 ずれています")
    report_paths([out_path, md_path])
    return 0


if __name__ == "__main__":
    sys.exit(main())
