#!/bin/bash
# 合成テスト素材を作る。実素材が無くても4本すべてを実走できるようにするため。
# 作るもの:
#   clip_a.mov  … 3シーン切替 + 途中に黒2秒。音声は日本語ナレーション（macOS say / Kyoko）
#   clip_b.mp4  … 1カット長回し（シーン検出が空振りし等間隔にフォールバックする経路の確認）
#   clip_c.mov  … 音声が極端に小さい（QCが音量NGを出す経路の確認）
set -euo pipefail

DEST="${1:?出力先ディレクトリを指定してください}"
mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 既知の正解テキスト。文字起こしの検証に使う。
LINE1="これから物件の内見映像をご案内します。"
LINE2="こちらがリビングです。南向きで日当たりが良好です。"
LINE3="以上で内見は終わりです。ありがとうございました。"

echo "[fixture] ナレーション合成 (say / Kyoko)"
say -v Kyoko -o "$TMP/n1.aiff" "$LINE1"
say -v Kyoko -o "$TMP/n2.aiff" "$LINE2"
say -v Kyoko -o "$TMP/n3.aiff" "$LINE3"

# 無音を挟んで1本の音声に（無音検出のテストも兼ねる）
ffmpeg -hide_banner -loglevel error -y \
  -i "$TMP/n1.aiff" -f lavfi -t 4 -i anullsrc=r=48000:cl=mono \
  -i "$TMP/n2.aiff" -f lavfi -t 4 -i anullsrc=r=48000:cl=mono \
  -i "$TMP/n3.aiff" \
  -filter_complex "[0:a][1:a][2:a][3:a][4:a]concat=n=5:v=0:a=1[a]" \
  -map "[a]" -ac 1 -ar 48000 -c:a pcm_s16le "$TMP/voice.wav"

DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$TMP/voice.wav")
echo "[fixture] ナレーション長: ${DUR}s"

# 映像: 3つの見た目が違うシーン + 黒2秒（黒フレーム検出のテスト）
SEG=$(python3 -c "print(max(3.0,(float('$DUR')-2)/3))")
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -t "$SEG" -i "testsrc2=size=1280x720:rate=30" \
  -f lavfi -t 2    -i "color=c=black:size=1280x720:rate=30" \
  -f lavfi -t "$SEG" -i "smptebars=size=1280x720:rate=30" \
  -f lavfi -t "$SEG" -i "testsrc=size=1280x720:rate=30" \
  -filter_complex "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]" \
  -map "[v]" -pix_fmt yuv420p -c:v libx264 -preset ultrafast "$TMP/video.mp4"

echo "[fixture] clip_a.mov（3シーン+黒+ナレーション）"
ffmpeg -hide_banner -loglevel error -y \
  -i "$TMP/video.mp4" -i "$TMP/voice.wav" \
  -map 0:v -map 1:a -shortest \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
  -af "loudnorm=I=-14:TP=-1.5:LRA=11" -c:a aac -b:a 192k \
  "$DEST/clip_a.mov"

echo "[fixture] clip_b.mp4（1カット長回し・無音）"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -t 20 -i "color=c=0x2b5f8a:size=1280x720:rate=30" \
  -f lavfi -t 20 -i "sine=frequency=220:sample_rate=48000" \
  -map 0:v -map 1:a -pix_fmt yuv420p -c:v libx264 -preset ultrafast \
  -af "volume=-20dB" -c:a aac -b:a 128k "$DEST/clip_b.mp4"

echo "[fixture] clip_c.mov（音量が小さすぎる＝QCがNGを出すはず）"
ffmpeg -hide_banner -loglevel error -y \
  -i "$DEST/clip_a.mov" -c:v copy -af "volume=-25dB" -c:a aac -b:a 192k \
  "$DEST/clip_c.mov"

echo "[fixture] 完了: $DEST"
ls -la "$DEST"
