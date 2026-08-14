#!/usr/bin/env python3
"""video-toolkit 共通部品。標準ライブラリのみ（憲法 §3）。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_ROOT = REPO / "out"

# 扱う拡張子。iPhone(.mov/.mp4) / ProRes(.mov) / ドローン(.mp4) / 音声単体も通す。
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mts", ".mxf", ".avi", ".mkv", ".insv", ".braw"}
AUDIO_EXT = {".wav", ".m4a", ".mp3", ".aac", ".aif", ".aiff", ".flac"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT


def die(msg: str) -> None:
    print(f"エラー: {msg}", file=sys.stderr)
    sys.exit(1)


def need(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        die(f"{binary} が見つかりません。`brew install ffmpeg` を実行してください。")
    return path


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 入力の解決

def collect_media(target: Path, recursive: bool = True) -> list[Path]:
    """ファイルなら1件、フォルダなら配下のメディアを列挙（自分の出力は除外）。"""
    if target.is_file():
        return [target]
    if not target.is_dir():
        die(f"見つかりません: {target}")
    it = target.rglob("*") if recursive else target.glob("*")
    found = [
        p
        for p in it
        if p.is_file()
        and p.suffix.lower() in MEDIA_EXT
        and not p.name.startswith("._")  # macOS の AppleDouble
        and OUT_ROOT not in p.parents
    ]
    return sorted(found)


def slugify(target: Path) -> str:
    base = target.name if target.is_dir() else target.stem
    base = unicodedata.normalize("NFC", base)
    base = re.sub(r"[^\w぀-ヿ一-鿿.-]+", "_", base).strip("_")
    return base or "untitled"


def out_dir(target: Path, override: str | None, kind: str) -> Path:
    root = Path(override).expanduser().resolve() if override else OUT_ROOT / slugify(target)
    d = root / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- ffprobe

def probe(path: Path) -> dict:
    """ffprobe の生JSON。失敗しても例外を投げず {'error': ...} を返す（憲法 §設計）。"""
    cmd = [
        need("ffprobe"), "-v", "error", "-hide_banner",
        "-print_format", "json", "-show_format", "-show_streams", str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"error": "ffprobe timeout (120s)"}
    if r.returncode != 0:
        return {"error": (r.stderr or "ffprobe failed").strip().splitlines()[-1][:300]}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        return {"error": f"ffprobe JSON parse failed: {e}"}


def run_ffmpeg(args: list[str], timeout: int = 3600) -> tuple[int, str]:
    """ffmpeg を実行し (returncode, stderr) を返す。フィルタのログはstderrに出る。"""
    cmd = [need("ffmpeg"), "-hide_banner", "-nostdin", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"ffmpeg timeout ({timeout}s)"
    return r.returncode, r.stderr or ""


# ---------------------------------------------------------------- 値の整形

def first_stream(info: dict, kind: str) -> dict:
    for s in info.get("streams", []):
        if s.get("codec_type") == kind:
            return s
    return {}


def parse_fraction(value) -> float | None:
    """'30000/1001' → 29.97。0除算・不正値は None。"""
    if not value:
        return None
    try:
        if "/" in str(value):
            num, den = str(value).split("/", 1)
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else None
        return round(float(value), 3)
    except (ValueError, ZeroDivisionError):
        return None


def duration_sec(info: dict) -> float | None:
    d = info.get("format", {}).get("duration")
    try:
        return round(float(d), 3)
    except (TypeError, ValueError):
        return None


def hhmmss(sec: float | None) -> str:
    if sec is None:
        return "-"
    sec = max(0.0, float(sec))
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def srt_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:  # 丸め上がり
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def human_size(num_bytes) -> str:
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return "-"


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def report_paths(paths: list[Path]) -> None:
    """Finderに貼れる絶対パスで出力先を知らせる（憲法 §設計）。"""
    print("\n出力:")
    for p in paths:
        print(f"  {p}")
