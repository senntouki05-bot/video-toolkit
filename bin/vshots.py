#!/usr/bin/env python3
"""vshots — シーン検出＋代表フレーム抽出。

Claude が「実際に映っているもの」を目で見るための道具。
検出は縮小してから行い（高速）、抽出は元素材から行う。
出力: frames/*.jpg / sheet_*.jpg（コンタクトシート）/ shots.json / shots.md
素材は読むだけ（憲法 §1）。
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from vcommon import (  # noqa: E402
    collect_media, duration_sec, first_stream, hhmmss, log, out_dir, probe,
    report_paths, run_ffmpeg, write_json,
)

PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def detect_scenes(path: Path, threshold: float, detect_width: int) -> tuple[list[float], str]:
    """シーン変化の秒数リストを返す。失敗時は ([], 理由)。"""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
        meta = Path(tf.name)
    try:
        vf = (
            f"scale={detect_width}:-2,"
            f"select='gt(scene,{threshold})',"
            f"metadata=print:file={meta}"
        )
        rc, err = run_ffmpeg(["-i", str(path), "-vf", vf, "-an", "-f", "null", "-"])
        if rc != 0:
            return [], (err.strip().splitlines() or ["ffmpeg failed"])[-1][:200]
        times = [float(m) for m in PTS_RE.findall(meta.read_text(encoding="utf-8", errors="replace"))]
        return sorted(set(times)), ""
    finally:
        meta.unlink(missing_ok=True)


def periodic(dur: float, every: float) -> list[float]:
    """シーン変化が乏しい素材（1カット長回し）用の等間隔サンプリング。"""
    if dur <= 0:
        return [0.0]
    t, out = 0.0, []
    while t < dur:
        out.append(round(t, 3))
        t += every
    return out


def grab(path: Path, t: float, dest: Path, width: int) -> bool:
    rc, _ = run_ffmpeg([
        "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
        "-vf", f"scale={width}:-2", "-q:v", "3", "-y", str(dest),
    ], timeout=180)
    return rc == 0 and dest.exists() and dest.stat().st_size > 0


def contact_sheet(frames: list[Path], dest_dir: Path, stem: str, cols: int, rows: int) -> list[Path]:
    """抽出済みフレームを tile で1枚絵にまとめる（Claudeが一目で全体を見るため）。"""
    if not frames:
        return []
    # 枚数が少ないときは格子を詰める（4枚を4x4に置くと12マスが黒く無駄になる）
    n = len(frames)
    if n < cols * rows:
        cols = min(cols, n)
        rows = max(1, -(-n // cols))
    seq = dest_dir / "_seq"
    seq.mkdir(exist_ok=True)
    for i, f in enumerate(frames):
        link = seq / f"{i:05d}.jpg"
        link.unlink(missing_ok=True)
        link.write_bytes(f.read_bytes())
    pattern = str(seq / "%05d.jpg")
    out_pat = dest_dir / f"{stem}_sheet_%03d.jpg"
    rc, err = run_ffmpeg([
        "-framerate", "1", "-start_number", "0", "-i", pattern,
        "-vf", f"scale=320:-2,tile={cols}x{rows}:padding=4:margin=4:color=black",
        "-q:v", "4", "-y", str(out_pat),
    ], timeout=300)
    for f in seq.glob("*.jpg"):
        f.unlink()
    seq.rmdir()
    if rc != 0:
        log(f"  ! コンタクトシート失敗: {err.strip().splitlines()[-1][:160] if err.strip() else rc}")
        return []
    return sorted(dest_dir.glob(f"{stem}_sheet_*.jpg"))


def main() -> int:
    ap = argparse.ArgumentParser(description="シーン検出＋代表フレーム抽出")
    ap.add_argument("target", help="素材フォルダ または 単一ファイル")
    ap.add_argument("--out", help="出力先（既定: ~/dev/video-toolkit/out/<名前>/shots）")
    ap.add_argument("--threshold", type=float, default=0.3, help="シーン変化のしきい値 0-1（既定 0.3）")
    ap.add_argument("--every", type=float, default=5.0, help="変化が乏しい時の等間隔サンプル秒（既定 5）")
    ap.add_argument("--max-frames", type=int, default=60, help="1本あたりの最大抽出枚数（既定 60）")
    ap.add_argument("--width", type=int, default=640, help="抽出フレームの幅px（既定 640）")
    ap.add_argument("--detect-width", type=int, default=320, help="検出時の縮小幅px（既定 320）")
    ap.add_argument("--tile", default="4x4", help="コンタクトシートの並び（既定 4x4）")
    args = ap.parse_args()

    try:
        cols, rows = (int(x) for x in args.tile.lower().split("x", 1))
    except ValueError:
        log(f"--tile の書式が不正です: {args.tile}（例: 4x4）")
        return 1

    target = Path(args.target).expanduser().resolve()
    files = [f for f in collect_media(target) if first_stream(probe(f), "video")]
    if not files:
        log(f"映像ファイルが見つかりません: {target}")
        return 1

    d = out_dir(target, args.out, "shots")
    frames_dir = d / "frames"
    frames_dir.mkdir(exist_ok=True)

    report, all_sheets = [], []
    for idx, f in enumerate(files, 1):
        info = probe(f)
        dur = duration_sec(info) or 0.0
        log(f"[{idx}/{len(files)}] {f.name}  ({hhmmss(dur)}) シーン検出中 …")

        times, err = detect_scenes(f, args.threshold, args.detect_width)
        mode = "シーン検出"
        if err:
            log(f"  ! 検出失敗のため等間隔に切替: {err}")
        # 先頭は必ず1枚。変化が少なければ等間隔で補う。
        if len(times) < 2:
            times = periodic(dur, args.every)
            mode = "等間隔" if not err else "等間隔(検出失敗)"
        else:
            times = [0.0] + times
        times = sorted(set(round(t, 3) for t in times if t < max(dur - 0.05, 0.0) or dur == 0))

        truncated = len(times) > args.max_frames
        if truncated:
            step = len(times) / args.max_frames
            times = [times[int(i * step)] for i in range(args.max_frames)]

        stem = f"{idx:02d}_{f.stem}"[:60].replace("/", "_")
        got: list[Path] = []
        for n, t in enumerate(times, 1):
            dest = frames_dir / f"{stem}_{n:03d}_{t:09.3f}s.jpg"
            if grab(f, t, dest, args.width):
                got.append(dest)
        sheets = contact_sheet(got, d, stem, cols, rows)
        all_sheets += sheets

        log(f"  {mode}: {len(got)}枚抽出" + ("（上限で間引き）" if truncated else "")
            + (f" / シート{len(sheets)}枚" if sheets else ""))
        report.append({
            "file": str(f), "duration_sec": dur, "mode": mode,
            "threshold": args.threshold, "truncated": truncated,
            "shots": [{"t_sec": t, "tc": hhmmss(t), "frame": str(p)} for t, p in zip(times, got)],
            "sheets": [str(s) for s in sheets],
            "detect_error": err or None,
        })

    write_json(d / "shots.json", {"target": str(target), "files": report})
    md = [f"# ショット一覧 — {target.name}", ""]
    for r in report:
        md += [f"## {Path(r['file']).name}",
               f"- 尺 {hhmmss(r['duration_sec'])} / 方式 {r['mode']} / {len(r['shots'])}枚", ""]
        md += [f"- `{s['tc']}` {Path(s['frame']).name}" for s in r["shots"]] + [""]
    (d / "shots.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    log(f"\n合計 {sum(len(r['shots']) for r in report)}枚")
    report_paths([frames_dir, d / "shots.json", d / "shots.md"] + all_sheets[:3])
    if len(all_sheets) > 3:
        log(f"  （ほかシート {len(all_sheets) - 3}枚）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
