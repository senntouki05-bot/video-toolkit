#!/usr/bin/env python3
"""vapply — style.json（参考動画の採寸表）を素材フォルダに転写し、カットリストを自動生成する。

やること:
  1. 参考のショット尺の並びを完成尺に合わせてスケールし、カット割りの設計図にする
  2. 素材を時系列順に走査し、各カットに「動きの多い区間」を割り当てる
     （素材内の自然なショット境界をまたがない窓を優先＝中途半端なジャンプカットを避ける）
  3. 素材の色分布を参考の色分布へ寄せるフィルタを生成（curves 百分位マッチ）
  4. （--music 時）参考のBPMに合う Apple Loops を探して vmusic でBGMを組む
  5. v cut 互換の cutlist.json と対応表 apply.md を出す。--render で書き出しまで実行

これは「たたき台」を作る道具。カットの入れ替え・尺の調整は cutlist.json の数字を
直して v cut を再実行する（最終判断は人間＝憲法のスコープ外を維持）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from vcommon import (  # noqa: E402
    collect_media, duration_sec, first_stream, hhmmss, log, out_dir, probe,
    report_paths, write_json,
)
from vanalyze import (  # noqa: E402
    MotionIndex, color_stats, grade_from_stats, pick_evenly, sample_rgb,
    scene_scores, shots_from_scores,
)

REPO = Path(__file__).resolve().parent.parent
MIN_CUT = 0.5  # これ未満のカットは作らない


# ---------------------------------------------------------------- 素材の走査

def load_clips(files: list[Path], threshold: float) -> list[dict]:
    """各素材のシーンスコア・ショット境界・動き指標を前計算する。壊れ素材は記録して続行。"""
    clips = []
    for i, f in enumerate(files, 1):
        info = probe(f)
        dur = duration_sec(info)
        if "error" in info or not first_stream(info, "video") or not dur:
            log(f"[{i}/{len(files)}] {f.name}  ! 読めないため除外")
            continue
        log(f"[{i}/{len(files)}] {f.name}  ({hhmmss(dur)}) 解析中 …")
        scores, err = scene_scores(f)
        if err:
            log(f"  ! シーンスコア取得失敗（等価値として扱う）: {err}")
        clips.append({
            "path": f, "dur": dur,
            "motion": MotionIndex(scores),
            "shots": shots_from_scores(scores, dur, threshold),
            "used": [],  # 既に使った区間 [(a,b), ...]
        })
    return clips


def place(clip: dict, d: float, step: float = 0.25) -> float | None:
    """クリップ内で長さ d の未使用窓を探し、最良の開始秒を返す。無ければ None。

    優先順位: (素材の1ショット内に収まる, 動きスコア) の辞書式で最大。
    """
    starts = []
    t = 0.0
    while t + d <= clip["dur"] + 1e-6:
        a, b = t, t + d
        if all(b <= ua or a >= ub for ua, ub in clip["used"]):
            inside = any(sa - 0.05 <= a and b <= sb + 0.05 for sa, sb in clip["shots"])
            starts.append((inside, clip["motion"].mean(a, b), round(t, 3)))
        t += step
    if not starts:
        return None
    return max(starts)[2]


def allocate(clips: list[dict], cut_durs: list[float]) -> list[dict]:
    """カット尺の列に素材区間を割り当てる。

    素材を時系列に並べた仮想タイムライン上で、i 番目のカットは位置 (i+0.5)/N に
    最も近いクリップから採る（＝内見の順路など素材の並びを保ちつつ全体をまんべんなく使う）。
    そのクリップに空きが無ければ後続→先行の順に探す。
    """
    total = sum(c["dur"] for c in clips)
    cum = []
    acc = 0.0
    for idx, c in enumerate(clips):
        cum.append((acc, acc + c["dur"], idx))
        acc += c["dur"]

    result = []
    n = len(cut_durs)
    for i, d in enumerate(cut_durs):
        pos = (i + 0.5) / n * total
        home = next(idx for a, b, idx in cum if a <= pos < b or (idx == len(clips) - 1))
        order = list(range(home, len(clips))) + list(range(home - 1, -1, -1))
        chosen = None
        for ci in order:
            st = place(clips[ci], d)
            if st is not None:
                chosen = (ci, st, d)
                break
        if chosen is None:  # 尺を半分に詰めてもう一周（素材が短いときの救済）
            d2 = max(MIN_CUT, round(d / 2, 2))
            for ci in order:
                st = place(clips[ci], d2)
                if st is not None:
                    chosen = (ci, st, d2)
                    break
        if chosen is None:
            result.append(None)
            continue
        ci, st, dd = chosen
        clips[ci]["used"].append((max(0.0, st - 0.2), min(clips[ci]["dur"], st + dd + 0.2)))
        result.append({"clip": ci, "in": st, "out": round(st + dd, 3), "want": d})
    return result


# ---------------------------------------------------------------- BGM

def find_loops(loops_dir: Path, bpm: float, want: int = 3, max_probe: int = 250) -> list[Path]:
    """BPM に合う（尺が「1小節×整数」に一致する）ループを探す。4/4拍子を仮定。"""
    bar = 240.0 / bpm
    exts = {".caf", ".aif", ".aiff", ".wav", ".m4a"}
    cands = sorted(p for p in loops_dir.rglob("*")
                   if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("._"))
    if len(cands) > max_probe:
        step = len(cands) / max_probe
        cands = [cands[int(i * step)] for i in range(max_probe)]
    picked: list[Path] = []
    base_dur = None
    for p in cands:
        d = duration_sec(probe(p))
        if not d:
            continue
        for k in (2, 4, 8, 16):
            if abs(d - k * bar) / (k * bar) < 0.02:
                # vmusic の整数比検査に通るよう、最初に採ったループと整数比の尺だけを重ねる
                if base_dur is None:
                    base_dur = d
                else:
                    r = base_dur / d if d < base_dur else d / base_dur
                    if abs(r - round(r)) > 0.02:
                        break
                picked.append(p)
                break
        if len(picked) >= want:
            break
    return picked


def build_music(loops: list[Path], total: float, lufs: float | None, dest_dir: Path) -> Path | None:
    """vmusic のスペックを書き、BGMを1本レンダリングする。"""
    spec = {
        "duration": round(total, 3),
        "target_lufs": round(min(-14.0, lufs) if lufs is not None else -16.0, 1),
        "tail_fade": 3.0,
        "layers": [{"src": str(p), "gain_db": g}
                   for p, g in zip(loops, (0, -4, -8))],
    }
    spec_path = dest_dir / "music_spec.json"
    write_json(spec_path, spec)
    bgm = dest_dir / "bgm.wav"
    r = subprocess.run([sys.executable, str(REPO / "bin" / "vmusic.py"),
                       str(spec_path), "-o", str(bgm)], capture_output=True, text=True)
    if r.returncode != 0 or not bgm.exists():
        log("  ! BGM合成に失敗（BGMなしで続行）")
        log("    " + (r.stdout + r.stderr).strip()[-300:])
        return None
    return bgm


# ---------------------------------------------------------------- 本体

def main() -> int:
    ap = argparse.ArgumentParser(description="style.json を素材に転写しカットリストを自動生成")
    ap.add_argument("target", help="素材フォルダ または 単一ファイル")
    ap.add_argument("--style", required=True, help="vstyle が出した style.json")
    ap.add_argument("--duration", type=float, help="完成尺 秒（既定: 参考動画と同じ）")
    ap.add_argument("--out", help="出力先（既定: ~/dev/video-toolkit/out/<名前>/apply）")
    ap.add_argument("--threshold", type=float, default=0.3, help="素材のショット境界しきい値（既定 0.3）")
    ap.add_argument("--music", action="store_true", help="参考BPMに合う Apple Loops でBGMも組む")
    ap.add_argument("--loops-dir", default="/Library/Audio/Apple Loops",
                    help="ループ置き場（既定: /Library/Audio/Apple Loops）")
    ap.add_argument("--bpm", type=float, help="BGMのBPMを手で指定（styleの推定を上書き）")
    ap.add_argument("--render", action="store_true", help="生成後そのまま v cut で書き出す")
    args = ap.parse_args()

    style_path = Path(args.style).expanduser().resolve()
    if not style_path.is_file():
        log(f"エラー: style.json が見つかりません: {style_path}")
        return 1
    style = json.loads(style_path.read_text(encoding="utf-8"))
    ref_shots = style.get("rhythm", {}).get("shots") or []
    if not ref_shots:
        log("エラー: style.json にショット情報がありません")
        return 1

    target = Path(args.target).expanduser().resolve()
    ref_src = Path(style.get("source", ""))
    files = [f for f in collect_media(target) if f != ref_src]
    if not files:
        log(f"エラー: 素材が見つかりません: {target}")
        return 1

    # --- 設計図: 参考のショット尺の並びを完成尺に合わせてスケール ---
    ref_durs = [s["dur"] for s in ref_shots]
    ref_total = sum(ref_durs)
    total = args.duration or style.get("format", {}).get("duration_sec") or ref_total
    k = total / ref_total if ref_total else 1.0
    cut_durs = [max(MIN_CUT, round(d * k, 2)) for d in ref_durs]

    log(f"設計図: {len(cut_durs)}カット / 完成尺 {total:.1f}秒（参考 {ref_total:.1f}秒 × {k:.2f}）\n")
    clips = load_clips(files, args.threshold)
    if not clips:
        log("エラー: 使える素材がありません")
        return 1
    footage_total = sum(c["dur"] for c in clips)
    if footage_total < sum(cut_durs):
        log(f"\n⚠️ 素材の総尺 {footage_total:.1f}秒 < 必要 {sum(cut_durs):.1f}秒。"
            f"再利用を避けるため一部カットが短く/欠落する可能性")

    # --- 割り当て ---
    log("\nカットを割り当て中 …")
    slots = allocate(clips, cut_durs)
    placed = [s for s in slots if s]
    dropped = len(slots) - len(placed)
    if not placed:
        log("エラー: 1カットも割り当てられませんでした（素材が短すぎる）")
        return 1
    if dropped:
        log(f"  ! {dropped}カットは置き場所がなく欠落（素材不足）")

    # --- 色: 選ばれた区間の中間フレームで実測 → 参考へ寄せる ---
    grade = "none"
    src_color = None
    if style.get("color"):
        log("色を合わせ中 …")
        mids = pick_evenly(
            [(clips[s["clip"]]["path"], (s["in"] + s["out"]) / 2) for s in placed], 24)
        frames = [f for p, t in mids if (f := sample_rgb(p, t)) is not None]
        src_color = color_stats(frames)
        if src_color:
            grade = grade_from_stats(src_color, style["color"])
            log(f"  素材: 輝度{src_color['mean_luma']}/彩度{src_color['mean_saturation']} → "
                f"参考: 輝度{style['color']['mean_luma']}/彩度{style['color']['mean_saturation']}")
        else:
            log("  ! 素材の色サンプルに失敗（色補正なし）")

    d = out_dir(target, args.out, "apply")
    actual_total = sum(s["out"] - s["in"] for s in placed)

    # --- BGM ---
    music = None
    if args.music:
        bpm = args.bpm or (style.get("audio") or {}).get("bpm")
        loops_dir = Path(args.loops_dir).expanduser()
        if not bpm:
            log("\nBGM: 参考のテンポが不明（--bpm で指定可）→ BGMなし")
        elif not loops_dir.is_dir():
            log(f"\nBGM: ループ置き場が見つからない → BGMなし: {loops_dir}")
        else:
            log(f"\nBGM: {bpm:.0f} BPM に合うループを探索中 …")
            loops = find_loops(loops_dir, bpm)
            if loops:
                for p in loops:
                    log(f"  + {p.name}")
                bgm = build_music(loops, actual_total,
                                  (style.get("audio") or {}).get("lufs"), d)
                if bgm:
                    music = {"src": str(bgm), "gain_db": 0, "fade_in": 1.0, "fade_out": 3.0}
            else:
                log("  合うループが見つからず → BGMなし")

    # --- カットリスト（v cut 互換） ---
    fmt = style.get("format", {})
    output = {
        "width": fmt.get("width", 1920), "height": fmt.get("height", 1080),
        "fps": fmt.get("fps") or 30, "crf": 18, "grade": grade,
        "target_lufs": (style.get("audio") or {}).get("lufs") or -14,
    }
    if music:
        output["music"] = music
    cutlist = {
        "name": f"{target.name} × {Path(style['source']).name} スタイル",
        "style": str(style_path),
        "output": output,
        "clips": [{
            "src": str(clips[s["clip"]]["path"]),
            "in": s["in"], "out": s["out"],
            "note": f"参考カット{i + 1}（{s['want']:.1f}秒枠）",
        } for i, s in enumerate(placed)],
    }
    cl_path = d / "cutlist.json"
    write_json(cl_path, cutlist)

    # --- 対応表 ---
    md = [f"# スタイル転写 — {target.name} × {Path(style['source']).name}", "",
          f"- 参考 {len(ref_shots)}カット/{ref_total:.1f}秒 → 生成 {len(placed)}カット/{actual_total:.1f}秒"
          + (f"（{dropped}カット欠落）" if dropped else ""),
          f"- 色補正: `{'あり（curves 百分位マッチ）' if grade != 'none' else 'なし'}`"
          f" / BGM: `{Path(music['src']).name if music else 'なし'}`", "",
          "| # | 枠(秒) | 素材 | IN | OUT | 動き |", "|---|---|---|---|---|---|"]
    for i, s in enumerate(placed, 1):
        c = clips[s["clip"]]
        md.append(f"| {i} | {s['want']:.1f} | {c['path'].name} | {hhmmss(s['in'])} | "
                  f"{hhmmss(s['out'])} | {c['motion'].mean(s['in'], s['out']):.3f} |")
    md += ["", "## 直し方", "",
           "- カットの差し替え・尺調整は `cutlist.json` の数字を直して下を再実行:",
           "```", f"bash ~/dev/video-toolkit/v cut {cl_path}", "```", "",
           "※ 転写できたのはリズム・色・音量・テンポまで。被写体の選択とストーリーは要人間チェック。"]
    if (fmt.get("height") or 0) > (fmt.get("width") or 1) and clips:
        md.append("※ 参考が縦型のため、横型素材は上下黒帯でパディングされる（クロップは手動）。")
    (d / "apply.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    log(f"\n生成: {len(placed)}カット / {actual_total:.1f}秒 / 色補正 {'あり' if grade != 'none' else 'なし'}"
        f" / BGM {'あり' if music else 'なし'}")

    outputs = [cl_path, d / "apply.md"]
    if args.render:
        log("\n書き出し中 …")
        r = subprocess.run([sys.executable, str(REPO / "bin" / "vcut.py"), str(cl_path)],
                           capture_output=True, text=True)
        print(r.stdout, end="")
        if r.returncode != 0:
            log("エラー: 書き出しに失敗")
            log((r.stderr or r.stdout).strip()[-800:])
            return 1
        outputs.insert(0, cl_path.with_suffix(".mp4"))
    else:
        log(f"書き出し: bash ~/dev/video-toolkit/v cut {cl_path}")

    report_paths(outputs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
