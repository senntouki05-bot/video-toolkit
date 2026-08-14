#!/usr/bin/env python3
"""vtranscribe — 完全ローカルの文字起こし → SRT / VTT / TXT / 単語タイムコードJSON。

依存は既存の whisper-tool venv（faster-whisper 1.2.1 / Python 3.12）を再利用する。
新規インストールは行わない（憲法 §3）。モデルは既にキャッシュ済みの
large-v3-turbo を既定にする（憲法 §4）。
音声は素材から一時WAVに抽出してから渡す（素材は読むだけ・憲法 §1）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV_PY = Path(os.environ.get("WHISPER_VENV_PY", str(Path.home() / "whisper-venv" / "bin" / "python3")))

# --- faster-whisper が入っている venv へ入り直す（whisper-tool と同じ作法） ---
if "faster_whisper" not in sys.modules:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        if not VENV_PY.exists():
            print(f"エラー: whisper-tool の venv が見つかりません: {VENV_PY}\n"
                  "  文字起こしはこの venv の faster-whisper を再利用しています。",
                  file=sys.stderr)
            sys.exit(1)
        os.execv(str(VENV_PY), [str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

import argparse  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

sys.path.insert(0, str(REPO / "lib"))
from vcommon import (  # noqa: E402
    collect_media, hhmmss, log, out_dir, report_paths, run_ffmpeg, srt_time, write_json,
)

# 和文字幕の目安（日本語字幕の一般的な運用値。要件があれば --max-chars で上書き）
DEFAULT_MAX_CHARS = 20
DEFAULT_MAX_DUR = 6.0


def extract_wav(src: Path) -> Path:
    """16kHz モノラル WAV に変換。Whisper の入力仕様に合わせる。"""
    tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
    rc, err = run_ffmpeg([
        "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", "-y", str(tmp),
    ], timeout=1800)
    if rc != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError((err.strip().splitlines() or ["ffmpeg failed"])[-1][:300])
    return tmp


SENTENCE_END = "。！？!?"
SOFT_BREAK = "、,」）)"
MIN_TAIL = 4  # これ以下の文字数で1枚になる「はぐれ字幕」は前に戻す


def to_cues(segments: list[dict], max_chars: int, max_dur: float) -> list[dict]:
    """単語タイムコードを使って、字幕として読める長さに割り直す。

    割る優先順位: ①句点で必ず割る ②長すぎる時は読点で割る ③それも無ければ文字数で割る。
    文の途中で機械的に切ると読めない字幕になるため（例:「南向きで日当たりが／良好です。」）。
    """
    cues: list[dict] = []

    def flush(buf, start, end):
        if buf.strip():
            cues.append({"start": start, "end": end, "text": buf.strip()})

    for seg in segments:
        words = [w for w in (seg.get("words") or []) if (w.get("word") or "").strip()]
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if not words:  # 単語タイムコードが無い場合はセグメントのまま
            cues.append({"start": seg["start"], "end": seg["end"], "text": text})
            continue

        buf, start, prev_end = "", words[0]["start"], words[0]["start"]
        # 各文字がどの単語の終端に属するかを持ち、読点まで巻き戻して割れるようにする
        char_end: list[float] = []
        for w in words:
            token = w["word"].strip()
            over_len = len(buf) + len(token) > max_chars
            over_dur = (w["end"] - start) > max_dur
            if buf and (over_len or over_dur):
                cut = -1
                for i in range(len(buf) - 1, max(len(buf) // 3, 0) - 1, -1):
                    if buf[i] in SOFT_BREAK:
                        cut = i + 1
                        break
                if cut > 0 and cut < len(buf):
                    flush(buf[:cut], start, char_end[cut - 1])
                    buf, start = buf[cut:], char_end[cut - 1]
                    char_end = char_end[cut:]
                else:
                    flush(buf, start, prev_end)
                    buf, start, char_end = "", w["start"], []
            buf += token
            char_end += [w["end"]] * len(token)
            prev_end = w["end"]
            if token and token[-1] in SENTENCE_END:  # 句点は必ず区切る
                flush(buf, start, prev_end)
                buf, start, char_end = "", prev_end, []
        flush(buf, start, prev_end)

    # はぐれ字幕を前へ戻す＋重なり/尺ゼロを潰す
    merged: list[dict] = []
    for c in cues:
        if (merged and len(c["text"]) <= MIN_TAIL
                and len(merged[-1]["text"]) + len(c["text"]) <= max_chars
                and c["start"] - merged[-1]["end"] < 0.5):
            merged[-1]["text"] += c["text"]
            merged[-1]["end"] = c["end"]
        else:
            merged.append(c)

    clean: list[dict] = []
    for c in merged:
        if c["end"] <= c["start"]:
            c["end"] = c["start"] + 0.4
        if clean and c["start"] < clean[-1]["end"]:
            c["start"] = clean[-1]["end"]
            if c["end"] <= c["start"]:
                continue
        clean.append(c)
    return clean


def write_srt(path: Path, cues: list[dict]) -> None:
    out = []
    for i, c in enumerate(cues, 1):
        out += [str(i), f"{srt_time(c['start'])} --> {srt_time(c['end'])}", c["text"], ""]
    path.write_text("\n".join(out), encoding="utf-8")


def write_vtt(path: Path, cues: list[dict]) -> None:
    out = ["WEBVTT", ""]
    for c in cues:
        a, b = srt_time(c["start"]).replace(",", "."), srt_time(c["end"]).replace(",", ".")
        out += [f"{a} --> {b}", c["text"], ""]
    path.write_text("\n".join(out), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="ローカル文字起こし（faster-whisper）")
    ap.add_argument("target", help="素材フォルダ または 単一ファイル")
    ap.add_argument("--out", help="出力先（既定: ~/dev/video-toolkit/out/<名前>/transcribe）")
    ap.add_argument("--model", default="large-v3-turbo", help="既定 large-v3-turbo（キャッシュ済）")
    ap.add_argument("--lang", default="ja", help="言語（既定 ja / 自動判定は auto）")
    ap.add_argument("--compute-type", default="int8", help="既定 int8（M1 16GB向け）")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="字幕1行の最大文字数")
    ap.add_argument("--max-dur", type=float, default=DEFAULT_MAX_DUR, help="字幕1枚の最大秒数")
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    target = Path(args.target).expanduser().resolve()
    files = collect_media(target)
    if not files:
        log(f"メディアが見つかりません: {target}")
        return 1

    d = out_dir(target, args.out, "transcribe")
    log(f"モデル {args.model} を読み込み中（初回は数十秒）…")
    t0 = time.time()
    model = WhisperModel(args.model, device="cpu", compute_type=args.compute_type)
    log(f"  読み込み {time.time() - t0:.1f}秒")

    written, summary = [], []
    for i, f in enumerate(files, 1):
        log(f"\n[{i}/{len(files)}] {f.name}")
        try:
            wav = extract_wav(f)
        except RuntimeError as e:
            log(f"  ! 音声抽出に失敗: {e}")
            summary.append({"file": str(f), "error": str(e)})
            continue
        try:
            t1 = time.time()
            seg_iter, info = model.transcribe(
                str(wav),
                language=None if args.lang == "auto" else args.lang,
                word_timestamps=True,
                vad_filter=True,
            )
            segments = []
            for s in seg_iter:
                segments.append({
                    "start": s.start, "end": s.end, "text": s.text,
                    "words": [{"word": w.word, "start": w.start, "end": w.end}
                              for w in (s.words or [])],
                })
                print(f"  {hhmmss(s.start)} {s.text.strip()[:60]}", flush=True)
            elapsed = time.time() - t1
        finally:
            wav.unlink(missing_ok=True)

        cues = to_cues(segments, args.max_chars, args.max_dur)
        stem = f"{i:02d}_{f.stem}"[:60].replace("/", "_")
        srt, vtt, txt, js = (d / f"{stem}.srt", d / f"{stem}.vtt",
                            d / f"{stem}.txt", d / f"{stem}.words.json")
        write_srt(srt, cues)
        write_vtt(vtt, cues)
        txt.write_text("".join(s["text"] for s in segments).strip() + "\n", encoding="utf-8")
        write_json(js, {"file": str(f), "language": info.language,
                        "duration_sec": info.duration, "segments": segments})
        written += [srt, vtt, txt, js]

        speed = (info.duration / elapsed) if elapsed > 0 else 0
        log(f"  → {len(cues)}枚の字幕 / 判定言語 {info.language} / "
            f"処理 {elapsed:.1f}秒（実時間の {speed:.1f}倍速）")
        summary.append({"file": str(f), "language": info.language,
                        "duration_sec": info.duration, "elapsed_sec": round(elapsed, 2),
                        "cues": len(cues), "srt": str(srt)})

    write_json(d / "transcribe.json", {"target": str(target), "model": args.model,
                                       "results": summary})
    report_paths(written[:8] + [d / "transcribe.json"])
    if len(written) > 8:
        log(f"  （ほか {len(written) - 8}ファイル）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
