#!/usr/bin/env python3
"""vqc — 納品前の機械チェック。測るだけで、素材も書き出しも一切いじらない。

見るもの: 尺 / 解像度 / fps / 音量(LUFS・トゥルーピーク) / 黒フレーム / 長い無音。
判定の基準値は「規格」ではなく「既定の運用値」。案件ごとに --target-lufs 等で上書きする。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from vcommon import (  # noqa: E402
    collect_media, duration_sec, first_stream, hhmmss, log, out_dir,
    parse_fraction, probe, report_paths, run_ffmpeg, write_json,
)

RE_I = re.compile(r"^\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", re.M)
RE_LRA = re.compile(r"^\s*LRA:\s*(-?[\d.]+)\s*LU", re.M)
RE_PEAK = re.compile(r"^\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", re.M)
RE_BLACK = re.compile(r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)")
RE_SIL_S = re.compile(r"silence_start:\s*(-?[\d.]+)")
RE_SIL_E = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


def to_float(v: str | None) -> float | None:
    if v is None:
        return None
    if v == "-inf":
        return float("-inf")
    try:
        return float(v)
    except ValueError:
        return None


def analyze(path: Path, args) -> dict:
    info = probe(path)
    if "error" in info:
        return {"file": str(path), "error": info["error"], "ng": ["読めない"], "warn": []}

    v, a = first_stream(info, "video"), first_stream(info, "audio")
    dur = duration_sec(info)
    res = {
        "file": str(path),
        "name": path.name,
        "duration_sec": dur,
        "duration": hhmmss(dur),
        "resolution": f"{v.get('width')}x{v.get('height')}" if v else None,
        "fps": parse_fraction(v.get("avg_frame_rate")) if v else None,
        "vcodec": v.get("codec_name") if v else None,
        "acodec": a.get("codec_name") if a else None,
        "ng": [], "warn": [],
    }

    filters = []
    if a:
        filters.append(("-af",
                        f"ebur128=peak=true,silencedetect=n={args.silence_db}dB:d={args.silence_sec}"))
    if v:
        filters.append(("-vf", f"blackdetect=d={args.black_sec}:pix_th=0.10"))
    if not filters:
        res["warn"].append("映像も音声も無い")
        return res

    cmd = ["-i", str(path)]
    for flag, val in filters:
        cmd += [flag, val]
    cmd += ["-f", "null", "-"]
    rc, err = run_ffmpeg(cmd, timeout=3600)
    if rc != 0:
        res["error"] = (err.strip().splitlines() or ["ffmpeg failed"])[-1][:300]
        res["ng"].append("解析に失敗")
        return res

    # --- 音量 ---
    if a:
        i_lufs = to_float(m.group(1) if (m := RE_I.search(err)) else None)
        lra = to_float(m.group(1) if (m := RE_LRA.search(err)) else None)
        peak = to_float(m.group(1) if (m := RE_PEAK.search(err)) else None)
        res["lufs_integrated"] = i_lufs
        res["lra"] = lra
        res["true_peak_dbfs"] = peak
        if i_lufs is None:
            res["warn"].append("音量を測れなかった")
        elif i_lufs == float("-inf"):
            res["ng"].append("音声トラックが無音")
        else:
            diff = i_lufs - args.target_lufs
            if abs(diff) > args.lufs_tol:
                res["ng"].append(
                    f"音量が目標から{diff:+.1f}LU ずれ（実測 {i_lufs:.1f} / 目標 {args.target_lufs:.1f} LUFS）")
        if peak is not None and peak != float("-inf") and peak > args.max_peak:
            res["ng"].append(f"トゥルーピーク超過 {peak:.1f} dBFS（上限 {args.max_peak:.1f}）")

        sil_pairs = RE_SIL_E.findall(err)
        starts = RE_SIL_S.findall(err)
        res["silences"] = [{"end": float(e), "duration": float(dsec)} for e, dsec in sil_pairs]
        res["silence_count"] = max(len(sil_pairs), len(starts))
        long_sil = [s for s in res["silences"] if s["duration"] >= args.silence_warn]
        if long_sil:
            worst = max(long_sil, key=lambda s: s["duration"])
            res["warn"].append(
                f"{args.silence_warn}秒以上の無音が{len(long_sil)}箇所（最長 {worst['duration']:.1f}秒）")
    else:
        res["warn"].append("音声トラックが無い")

    # --- 黒フレーム ---
    if v:
        blacks = [{"start": float(s), "end": float(e), "duration": float(dd)}
                  for s, e, dd in RE_BLACK.findall(err)]
        res["blacks"] = blacks
        if blacks:
            head = [b for b in blacks if b["start"] <= 0.5]
            tail = [b for b in blacks if dur and b["end"] >= dur - 0.5]
            mid = [b for b in blacks if b not in head and b not in tail]
            if mid:
                worst = max(mid, key=lambda b: b["duration"])
                res["ng"].append(
                    f"本編中に黒フレーム{len(mid)}箇所（最長 {worst['duration']:.1f}秒 @ {hhmmss(worst['start'])}）")
            if head or tail:
                res["warn"].append(f"頭/尻の黒 {len(head) + len(tail)}箇所（意図的なら問題なし）")
        if res["fps"] and args.expect_fps and abs(res["fps"] - args.expect_fps) > 0.05:
            res["ng"].append(f"fps不一致 実測 {res['fps']} / 期待 {args.expect_fps}")
        if args.expect_res and res["resolution"] != args.expect_res:
            res["ng"].append(f"解像度不一致 実測 {res['resolution']} / 期待 {args.expect_res}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="納品前QC（測るだけ・書き換えない）")
    ap.add_argument("target", help="素材フォルダ または 単一ファイル")
    ap.add_argument("--out", help="出力先（既定: ~/dev/video-toolkit/out/<名前>/qc）")
    ap.add_argument("--target-lufs", type=float, default=-14.0,
                    help="目標ラウドネス（既定 -14 LUFS＝Web/SNS配信の一般的な運用値）")
    ap.add_argument("--lufs-tol", type=float, default=2.0, help="許容ずれ LU（既定 2.0）")
    ap.add_argument("--max-peak", type=float, default=-1.0, help="トゥルーピーク上限 dBFS（既定 -1.0）")
    ap.add_argument("--black-sec", type=float, default=0.5, help="黒と見なす最短秒（既定 0.5）")
    ap.add_argument("--silence-db", type=float, default=-50.0, help="無音のしきい値 dB（既定 -50）")
    ap.add_argument("--silence-sec", type=float, default=2.0, help="無音と見なす最短秒（既定 2.0）")
    ap.add_argument("--silence-warn", type=float, default=3.0, help="警告する無音の長さ秒（既定 3.0）")
    ap.add_argument("--expect-fps", type=float, help="期待fps（指定時のみ照合）")
    ap.add_argument("--expect-res", help="期待解像度 例 3840x2160（指定時のみ照合）")
    args = ap.parse_args()

    target = Path(args.target).expanduser().resolve()
    files = collect_media(target)
    if not files:
        log(f"メディアが見つかりません: {target}")
        return 1

    log(f"QC: {len(files)}件（目標 {args.target_lufs}±{args.lufs_tol} LUFS / "
        f"ピーク上限 {args.max_peak} dBFS）\n")
    results = []
    for i, f in enumerate(files, 1):
        log(f"[{i}/{len(files)}] {f.name} を解析中 …")
        r = analyze(f, args)
        results.append(r)
        mark = "NG" if r["ng"] else ("注意" if r["warn"] else "OK")
        lufs = r.get("lufs_integrated")
        lufs_s = f"{lufs:.1f}LUFS" if isinstance(lufs, float) and lufs != float("-inf") else "-"
        log(f"  [{mark}] {r.get('duration', '-')} / {r.get('resolution') or '-'} / "
            f"{r.get('fps') or '-'}fps / {lufs_s}")
        for m in r["ng"]:
            log(f"    NG: {m}")
        for m in r["warn"]:
            log(f"    注意: {m}")

    ng = [r for r in results if r["ng"]]
    d = out_dir(target, args.out, "qc")
    write_json(d / "qc.json", {"target": str(target),
                               "criteria": {k: v for k, v in vars(args).items() if k != "out"},
                               "results": results})
    md = [f"# 納品QC — {target.name}", "",
          f"- 判定: **{'NG ' + str(len(ng)) + '本' if ng else '全' + str(len(results)) + '本 合格'}**",
          f"- 基準: 目標 {args.target_lufs}±{args.lufs_tol} LUFS / ピーク上限 {args.max_peak} dBFS "
          f"/ 黒 {args.black_sec}秒以上 / 無音 {args.silence_warn}秒以上",
          "", "| ファイル | 判定 | 尺 | 解像度 | fps | LUFS | ピーク | 指摘 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lufs = r.get("lufs_integrated")
        pk = r.get("true_peak_dbfs")
        md.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r.get("name", "-"), "NG" if r["ng"] else ("注意" if r["warn"] else "OK"),
            r.get("duration", "-"), r.get("resolution") or "-", r.get("fps") or "-",
            f"{lufs:.1f}" if isinstance(lufs, float) and lufs != float("-inf") else "-",
            f"{pk:.1f}" if isinstance(pk, float) and pk != float("-inf") else "-",
            "<br>".join(r["ng"] + r["warn"]) or "—"))
    (d / "qc.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    log(f"\n判定: {'NG ' + str(len(ng)) + '本' if ng else '全' + str(len(results)) + '本 合格'}")
    report_paths([d / "qc.json", d / "qc.md"])
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
