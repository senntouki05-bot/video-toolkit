#!/bin/bash
# 4本すべてを合成素材で実走し、出力が実際に生まれたかを確認する。
# 「動いたはず」ではなく「ファイルが在る／中身が期待通り」で判定する。
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX="$REPO/tests/fixture"
OUT="$REPO/out/_selftest"
PASS=0; FAIL=0

ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
ng()   { echo "  ❌ $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else ng "$1"; fi; }

echo "=== 0. 合成素材の用意 ==="
if [ ! -f "$FIX/clip_a.mov" ]; then
  bash "$REPO/tests/make_fixture.sh" "$FIX" || { echo "素材生成に失敗"; exit 1; }
else
  echo "  既存の $FIX を再利用"
fi

rm -rf "$OUT"

echo
echo "=== 1. log ==="
bash "$REPO/v" log "$FIX" --out "$OUT" || ng "log が異常終了"
check "log.csv が生成された"           "[ -s '$OUT/log/log.csv' ]"
check "log.md が生成された"            "[ -s '$OUT/log/log.md' ]"
check "3本すべて記録された"            "[ \$(grep -c 'clip_' '$OUT/log/log.csv') -eq 3 ]"
check "解像度が読めている"             "grep -q '1280x720' '$OUT/log/log.csv'"

echo
echo "=== 2. shots ==="
bash "$REPO/v" shots "$FIX" --out "$OUT" --max-frames 16 || ng "shots が異常終了"
check "フレームが抽出された"           "[ \$(ls '$OUT/shots/frames'/*.jpg 2>/dev/null | wc -l) -gt 3 ]"
check "コンタクトシートが出来た"       "[ \$(ls '$OUT/shots'/*_sheet_*.jpg 2>/dev/null | wc -l) -ge 1 ]"
check "clip_a でシーン検出が効いた"    "python3 -c \"import json,sys; d=json.load(open('$OUT/shots/shots.json')); r=[x for x in d['files'] if 'clip_a' in x['file']][0]; sys.exit(0 if r['mode'].startswith('シーン検出') else 1)\""
check "clip_b は等間隔にフォールバック" "python3 -c \"import json,sys; d=json.load(open('$OUT/shots/shots.json')); r=[x for x in d['files'] if 'clip_b' in x['file']][0]; sys.exit(0 if r['mode'].startswith('等間隔') else 1)\""

echo
echo "=== 3. qc ==="
bash "$REPO/v" qc "$FIX" --out "$OUT"
check "qc.json が生成された"           "[ -s '$OUT/qc/qc.json' ]"
check "clip_c を音量NGで捕まえた"      "python3 -c \"import json,sys; d=json.load(open('$OUT/qc/qc.json')); r=[x for x in d['results'] if 'clip_c' in x['file']][0]; sys.exit(0 if any('音量' in m for m in r['ng']) else 1)\""
check "clip_a の黒フレームを検出した"  "python3 -c \"import json,sys; d=json.load(open('$OUT/qc/qc.json')); r=[x for x in d['results'] if 'clip_a' in x['file']][0]; sys.exit(0 if r.get('blacks') else 1)\""
check "clip_a の音量は合格した"        "python3 -c \"import json,sys; d=json.load(open('$OUT/qc/qc.json')); r=[x for x in d['results'] if 'clip_a' in x['file']][0]; sys.exit(0 if not any('音量' in m for m in r['ng']) else 1)\""
check "無音区間を検出した"             "python3 -c \"import json,sys; d=json.load(open('$OUT/qc/qc.json')); r=[x for x in d['results'] if 'clip_a' in x['file']][0]; sys.exit(0 if r.get('silence_count',0)>0 else 1)\""

echo
echo "=== 4. transcribe（clip_a のみ・モデル読込に時間がかかります）==="
bash "$REPO/v" transcribe "$FIX/clip_a.mov" --out "$OUT" || ng "transcribe が異常終了"
check "SRT が生成された"               "[ -s '$OUT/transcribe/01_clip_a.srt' ]"
check "言語が ja と判定された"         "grep -q '\"language\": \"ja\"' '$OUT/transcribe/transcribe.json'"
check "『リビング』を聞き取れた"       "grep -q 'リビング' '$OUT/transcribe/01_clip_a.txt'"
check "『内見』を聞き取れた"           "grep -q '内見' '$OUT/transcribe/01_clip_a.txt'"
check "SRTのタイムコード書式が正しい"  "grep -qE '^[0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3} --> ' '$OUT/transcribe/01_clip_a.srt'"

echo
echo "==================== 結果 ===================="
echo "  合格 $PASS / 不合格 $FAIL"
echo "  出力: $OUT"
[ "$FAIL" -eq 0 ] || exit 1
