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


# カラーグレーディングのプリセット。
# ⚠️ 不動産映像では内装（白壁・木目）の色を壊してはいけない。青は「シャドウと空」に置き、
#    中間調はできるだけ保つ。強い青被りは物件の色が伝わらなくなり本末転倒。
GRADES = {
    "none": "",
    # 青春ブルー: シャドウに青、ハイライトをわずかに冷やす。空の抜けを作り彩度を少し上げる。
    # 青春ブルー（2026-08-15 実測で確定）。
    # ⚠️ 旧版は bh=-0.06 / rh=0.03 でハイライトを暖色に振っていたため、白壁の多い室内では
    #    全体が黄色く転んだ（実測: 平均B-R が 元-11 → -13 と悪化）。ハイライトには触らない。
    # 実測（平均RGB・B-R）: LDK -11→+15 / バルコニー +13→+54 / 共用部の木 -46→-29（暖色を保持）
    # gamma=1.10 は colortemperature が落とす明度の戻し（LDK 明133→138）。
    "seishun-blue": (
        "colortemperature=temperature=8200,"
        "colorbalance=bs=0.22:bm=0.06,"
        "curves=m='0/0.05 0.22/0.24 0.8/0.86 1/1',"
        "eq=contrast=1.10:saturation=1.18:gamma=1.10,"
        "vibrance=intensity=0.18"
    ),
    # 控えめ版（内装の色再現を最優先したいとき）
    "seishun-blue-soft": (
        "colortemperature=temperature=7400,"
        "colorbalance=bs=0.16:bm=0.04,"
        "curves=m='0/0.04 0.22/0.23 0.8/0.85 1/1',"
        "eq=contrast=1.06:saturation=1.12:gamma=1.06,"
        "vibrance=intensity=0.12"
    ),
}


def grade_chain(name: str) -> str:
    if name in GRADES:
        return GRADES[name]
    return name  # プリセット名でなければ生のフィルタ文字列として扱う


def build(cl: dict, out_path: Path, dry: bool) -> tuple[list[str], float]:
    o = cl.get("output", {})
    w, h, fps = o.get("width", 1080), o.get("height", 1920), o.get("fps", 30)
    crf = o.get("crf", 18)
    grade = grade_chain(o.get("grade", "none"))
    music = o.get("music")
    sfx = o.get("sfx", [])
    lufs = o.get("target_lufs", -14)  # 記録用（実際の正規化は vmusic 側＋書き出し後の vqc 実測）

    srcs: list[str] = []
    for c in cl["clips"]:
        if c["src"] not in srcs:
            srcs.append(c["src"])

    parts, labels, total = [], [], 0.0
    for i, c in enumerate(cl["clips"]):
        idx = srcs.index(c["src"])
        raw = float(c["out"]) - float(c["in"])
        if raw <= 0:
            raise ValueError(f"カット{i + 1}: out が in 以下です（{c['in']} → {c['out']}）")
        speed = float(c.get("speed", 1.0))
        if speed <= 0:
            raise ValueError(f"カット{i + 1}: speed は正の数（{speed}）")
        total += raw / speed
        f = (f"[{idx}:v]trim=start={c['in']}:end={c['out']},"
             f"setpts=(PTS-STARTPTS)/{speed}")
        # スローは素材のコマが足りなくなるので、必要なら中間フレームを作る
        interp = c.get("interp", "none")
        if speed < 1.0 and interp in ("blend", "mci"):
            f += f",minterpolate=fps={fps}:mi_mode={interp}"
        f += (f",scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps}[v{i}]")
        parts.append(f)
        labels.append(f"[v{i}]")

    vlabel = "[vcat]"
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0{vlabel}")
    if grade:
        parts.append(f"{vlabel}{grade}[vout]")
        vlabel = "[vout]"

    args = []
    for s in srcs:
        args += ["-i", s]

    # --- 音声: BGM と効果音を混ぜる ---
    ai = len(srcs)
    alabels = []
    if music:
        args += ["-stream_loop", "-1", "-i", music["src"]]
        fo = float(music.get("fade_out", 3.0))
        parts.append(
            f"[{ai}:a]atrim=0:{total:.3f},asetpts=N/SR/TB,"
            f"volume={float(music.get('gain_db', 0))}dB,"
            f"afade=t=in:st=0:d={float(music.get('fade_in', 1.0))},"
            f"afade=t=out:st={max(0.0, total - fo):.3f}:d={fo}[am]")
        alabels.append("[am]")
        ai += 1
    for n, s in enumerate(sfx):
        args += ["-i", s["src"]]
        at = int(float(s["at"]) * 1000)
        parts.append(f"[{ai}:a]volume={float(s.get('gain_db', -12))}dB,"
                     f"adelay={at}|{at}[s{n}]")
        alabels.append(f"[s{n}]")
        ai += 1

    # ⚠️ ここで loudnorm を1パスで掛けない（BGMの起伏が潰れる。vmusic側で正規化済み）。
    #    クリップ防止のリミッタと固定ゲインだけ。最終音量は書き出し後に vqc で実測して詰める。
    master = float(o.get("master_gain_db", 0))
    args += ["-filter_complex", ";".join(parts) if not alabels else
             ";".join(parts + ["".join(alabels) +
                               f"amix=inputs={len(alabels)}:normalize=0:dropout_transition=0,"
                               f"atrim=0:{total:.3f},volume={master}dB,"
                               f"alimiter=limit=0.841:level=false[aout]"])]
    args += ["-map", vlabel]
    if alabels:
        args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-an"]
    args += ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(out_path)]
    if dry:
        log("（DRY_RUN）実行しない ffmpeg 引数:")
        log("  " + " ".join(args)[:2000])
    return args, total


def sheet(cl: dict, total: float) -> str:
    o = cl.get("output", {})
    md = [f"# カット表 — {cl.get('name', '')}", "",
          f"- カット数 **{len(cl['clips'])}** / 完成尺 **{total:.1f}秒**",
          f"- 色 `{o.get('grade', 'none')}` / BGM `{Path(o['music']['src']).name if o.get('music') else 'なし'}`"
          f" / 効果音 {len(o.get('sfx', []))}箇所", "",
          "| # | 完成尺の位置 | 素材 | IN | OUT | 速度 | 実尺 | 内容 |",
          "|---|---|---|---|---|---|---|---|"]
    t = 0.0
    for i, c in enumerate(cl["clips"], 1):
        sp = float(c.get("speed", 1.0))
        d = (float(c["out"]) - float(c["in"])) / sp
        tag = "等速" if sp == 1.0 else (f"{sp:g}倍速" if sp > 1 else f"{sp:g}倍スロー")
        md.append(f"| {i} | {hhmmss(t)} | {Path(c['src']).name} | {hhmmss(float(c['in']))} | "
                  f"{hhmmss(float(c['out']))} | {tag} | {d:.1f}s | {c.get('note', '')} |")
        t += d
    if o.get("sfx"):
        md += ["", "## 効果音", "", "| 位置 | 音 | 音量 | 狙い |", "|---|---|---|---|"]
        md += [f"| {hhmmss(float(s['at']))} | {Path(s['src']).name} | {s.get('gain_db', -12)}dB "
               f"| {s.get('note', '')} |" for s in o["sfx"]]
    md += ["", "※ IN/OUT は**素材内**のタイムコード。完成尺の並びは上から順。",
           "※ Resolveで組み直すときは「完成尺の位置」を並び順の目安に使う。"]
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
