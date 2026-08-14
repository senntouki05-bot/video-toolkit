#!/bin/bash
# video-toolkit — 入口。どこから叩いても動く（cd 不要）。
#   bash ~/dev/video-toolkit/v <サブコマンド> <素材フォルダ or ファイル> [オプション]
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="/opt/homebrew/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

usage() {
  cat <<'EOS'
video-toolkit — 撮影素材の機械作業をClaude Codeから叩く

使い方:
  bash ~/dev/video-toolkit/v <サブコマンド> <素材フォルダ or ファイル> [オプション]

サブコマンド:
  log         素材を全部 ffprobe → 一覧表（CSV/Markdown/JSON）
  shots       シーン検出＋代表フレームJPEG抽出＋コンタクトシート
  transcribe  文字起こし → SRT / VTT / TXT / 単語タイムコードJSON（完全ローカル）
  qc          納品QC（尺・解像度・fps・LUFS音量・黒フレーム・無音）
  test        合成クリップを作って4本すべてを自己テスト（引数不要）

出力先: ~/dev/video-toolkit/out/<素材フォルダ名>/
素材フォルダには一切書き込みません。

例:
  bash ~/dev/video-toolkit/v log "/Volumes/SSD/2026-08-15 物件A"
  bash ~/dev/video-toolkit/v qc ~/Movies/納品.mov --expect-res 3840x2160
  bash ~/dev/video-toolkit/v transcribe ~/Movies/インタビュー.mov
EOS
}

cmd="${1:-}"; shift || true
case "$cmd" in
  log)        exec "$PY" "$REPO/bin/vlog.py" "$@" ;;
  shots)      exec "$PY" "$REPO/bin/vshots.py" "$@" ;;
  transcribe) exec "$PY" "$REPO/bin/vtranscribe.py" "$@" ;;
  qc)         exec "$PY" "$REPO/bin/vqc.py" "$@" ;;
  cut)        exec "$PY" "$REPO/bin/vcut.py" "$@" ;;
  test)       exec bash "$REPO/tests/run.sh" "$@" ;;
  ""|-h|--help|help) usage; exit 0 ;;
  *) echo "不明なサブコマンド: $cmd" >&2; echo >&2; usage >&2; exit 2 ;;
esac
