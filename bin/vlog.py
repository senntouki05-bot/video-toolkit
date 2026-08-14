#!/usr/bin/env python3
"""vlog — 素材フォルダを ffprobe で総なめして一覧表にする。

出力: log.csv / log.md / log.json
素材は読むだけ（憲法 §1）。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from vcommon import (  # noqa: E402
    collect_media, duration_sec, first_stream, hhmmss, human_size, log,
    out_dir, parse_fraction, probe, report_paths, write_json,
)

COLUMNS = [
    "ファイル", "種別", "尺", "尺(秒)", "解像度", "fps", "映像コーデック",
    "ビットレート", "音声", "回転", "カラー(transfer/primaries)", "撮影日時",
    "サイズ", "パス", "備考",
]


def rotation_of(vstream: dict) -> str:
    for sd in vstream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return f"{int(float(sd['rotation']))}°"
            except (TypeError, ValueError):
                pass
    tag = (vstream.get("tags") or {}).get("rotate")
    return f"{tag}°" if tag else "0°"


def bitrate_of(info: dict) -> str:
    br = info.get("format", {}).get("bit_rate")
    try:
        return f"{float(br) / 1_000_000:.1f}Mbps"
    except (TypeError, ValueError):
        return "-"


def describe(path: Path, root: Path) -> dict:
    info = probe(path)
    rel = str(path.relative_to(root)) if root in path.parents or root == path.parent else path.name
    row = {c: "-" for c in COLUMNS}
    row["ファイル"] = path.name
    row["パス"] = str(path)
    row["サイズ"] = human_size(path.stat().st_size)

    if "error" in info:
        row["種別"] = "読めず"
        row["備考"] = info["error"]
        return row

    v = first_stream(info, "video")
    a = first_stream(info, "audio")
    dur = duration_sec(info)

    row["種別"] = "映像" if v else ("音声" if a else "不明")
    row["尺"] = hhmmss(dur)
    row["尺(秒)"] = f"{dur:.3f}" if dur is not None else "-"
    row["ビットレート"] = bitrate_of(info)
    row["撮影日時"] = (info.get("format", {}).get("tags") or {}).get("creation_time", "-")
    row["備考"] = rel if rel != path.name else ""

    if v:
        w, h = v.get("width"), v.get("height")
        row["解像度"] = f"{w}x{h}" if w and h else "-"
        fps = parse_fraction(v.get("avg_frame_rate")) or parse_fraction(v.get("r_frame_rate"))
        row["fps"] = f"{fps}" if fps else "-"
        prof = v.get("profile")
        row["映像コーデック"] = f"{v.get('codec_name', '-')}" + (f" ({prof})" if prof else "")
        row["回転"] = rotation_of(v)
        row["カラー(transfer/primaries)"] = (
            f"{v.get('color_transfer', '不明')}/{v.get('color_primaries', '不明')}"
        )
    if a:
        ch = a.get("channels", "-")
        sr = a.get("sample_rate", "-")
        row["音声"] = f"{a.get('codec_name', '-')} {ch}ch {sr}Hz"
    elif v:
        row["音声"] = "なし"
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="素材フォルダを ffprobe して一覧表にする")
    ap.add_argument("target", help="素材フォルダ または 単一ファイル")
    ap.add_argument("--out", help="出力先（既定: ~/dev/video-toolkit/out/<フォルダ名>/log）")
    ap.add_argument("--no-recursive", action="store_true", help="サブフォルダを見ない")
    args = ap.parse_args()

    target = Path(args.target).expanduser().resolve()
    files = collect_media(target, recursive=not args.no_recursive)
    if not files:
        log(f"メディアが見つかりません: {target}")
        return 1

    root = target if target.is_dir() else target.parent
    log(f"素材ログ: {len(files)}件 をスキャンします …")

    rows, errors, total_sec = [], 0, 0.0
    for i, f in enumerate(files, 1):
        row = describe(f, root)
        rows.append(row)
        if row["種別"] == "読めず":
            errors += 1
        else:
            try:
                total_sec += float(row["尺(秒)"])
            except ValueError:
                pass
        print(f"  [{i}/{len(files)}] {f.name}  {row['尺']}  {row['解像度']}", flush=True)

    d = out_dir(target, args.out, "log")
    csv_path, md_path, json_path = d / "log.csv", d / "log.md", d / "log.json"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    show = [c for c in COLUMNS if c not in ("尺(秒)", "パス")]
    md = [
        f"# 素材ログ — {target.name}",
        "",
        f"- 対象: `{target}`",
        f"- 本数: **{len(rows)}本**（読めず {errors}本）",
        f"- 合計尺: **{hhmmss(total_sec)}**",
        "",
        "| " + " | ".join(show) + " |",
        "|" + "---|" * len(show),
    ]
    md += ["| " + " | ".join(str(r[c]).replace("|", "\\|") for c in show) + " |" for r in rows]
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    write_json(json_path, {"target": str(target), "count": len(rows),
                           "errors": errors, "total_sec": round(total_sec, 3), "rows": rows})

    log(f"\n合計 {len(rows)}本 / {hhmmss(total_sec)}" + (f" / 読めず {errors}本" if errors else ""))
    report_paths([csv_path, md_path, json_path])
    return 0


if __name__ == "__main__":
    sys.exit(main())
