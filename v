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
  style       参考動画の編集スタイルを数値化 → style.json（リズム・色・音）
  apply       style.json を素材に転写しカットリスト自動生成（--render で書き出しまで）
  cut         カットリストJSONを読んで実際に書き出す
  music       Apple Loops のステムを重ねてBGMを1本に組む
  test        合成クリップを作って全ツールを自己テスト（引数不要）

出力先: ~/dev/video-toolkit/out/<素材フォルダ名>/
素材フォルダには一切書き込みません。

例:
  bash ~/dev/video-toolkit/v log "/Volumes/SSD/2026-08-15 物件A"
  bash ~/dev/video-toolkit/v qc ~/Movies/納品.mov --expect-res 3840x2160
  bash ~/dev/video-toolkit/v style ~/Movies/参考にしたい完成品.mp4
  bash ~/dev/video-toolkit/v apply "/Volumes/SSD/物件A" \
       --style ~/dev/video-toolkit/out/参考にしたい完成品/style/style.json --music --render
EOS
}

cmd="${1:-}"; shift || true
case "$cmd" in
  log)        exec "$PY" "$REPO/bin/vlog.py" "$@" ;;
  shots)      exec "$PY" "$REPO/bin/vshots.py" "$@" ;;
  transcribe) exec "$PY" "$REPO/bin/vtranscribe.py" "$@" ;;
  qc)         exec "$PY" "$REPO/bin/vqc.py" "$@" ;;
  style)      exec "$PY" "$REPO/bin/vstyle.py" "$@" ;;
  apply)      exec "$PY" "$REPO/bin/vapply.py" "$@" ;;
  cut)        exec "$PY" "$REPO/bin/vcut.py" "$@" ;;
  music)      exec "$PY" "$REPO/bin/vmusic.py" "$@" ;;
  test)       exec bash "$REPO/tests/run.sh" "$@" ;;
  ""|-h|--help|help) usage; exit 0 ;;
  *) echo "不明なサブコマンド: $cmd" >&2; echo >&2; usage >&2; exit 2 ;;
esac
