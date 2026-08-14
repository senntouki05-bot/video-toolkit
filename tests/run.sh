#!/bin/bash
# 全ツールを合成素材で実走し、出力が実際に生まれたかを確認する。
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
echo "=== 4. style（clip_a を参考動画に見立てる）==="
bash "$REPO/v" style "$FIX/clip_a.mov" --out "$OUT" || ng "style が異常終了"
check "style.json が生成された"        "[ -s '$OUT/style/style.json' ]"
check "style.md が生成された"          "[ -s '$OUT/style/style.md' ]"
check "3ショット以上を検出した"        "python3 -c \"import json,sys; s=json.load(open('$OUT/style/style.json')); sys.exit(0 if s['rhythm']['shot_count']>=3 else 1)\""
check "色の百分位が採れた"             "python3 -c \"import json,sys; s=json.load(open('$OUT/style/style.json')); sys.exit(0 if len(s['color']['percentiles']['r'])==7 else 1)\""
check "音量が実測された"               "python3 -c \"import json,sys; s=json.load(open('$OUT/style/style.json')); sys.exit(0 if isinstance(s['audio']['lufs'],(int,float)) else 1)\""

echo
echo "=== 5. apply（fixture 3本へ転写 → BGM → 書き出しまで）==="
# BGM用の疑似 Apple Loops（120BPM 2小節 = きっかり4.0秒 × 2本）
mkdir -p "$OUT/_loops"
ffmpeg -hide_banner -loglevel error -y -f lavfi -t 4 -i "sine=frequency=220:sample_rate=48000" \
  -c:a pcm_s16le "$OUT/_loops/loop_low.wav"
ffmpeg -hide_banner -loglevel error -y -f lavfi -t 4 -i "sine=frequency=330:sample_rate=48000" \
  -c:a pcm_s16le "$OUT/_loops/loop_high.wav"
bash "$REPO/v" apply "$FIX" --style "$OUT/style/style.json" --out "$OUT" \
  --music --loops-dir "$OUT/_loops" --bpm 120 --render || ng "apply が異常終了"
check "cutlist.json が生成された"      "[ -s '$OUT/apply/cutlist.json' ]"
check "2カット以上が割り当てられた"    "python3 -c \"import json,sys; c=json.load(open('$OUT/apply/cutlist.json')); sys.exit(0 if len(c['clips'])>=2 else 1)\""
check "色補正(curves)が生成された"     "python3 -c \"import json,sys; c=json.load(open('$OUT/apply/cutlist.json')); sys.exit(0 if 'curves' in c['output']['grade'] else 1)\""
check "BGMが合成された"                "[ -s '$OUT/apply/bgm.wav' ]"
check "BGMがカットリストに載った"      "python3 -c \"import json,sys; c=json.load(open('$OUT/apply/cutlist.json')); sys.exit(0 if 'bgm.wav' in c['output'].get('music',{}).get('src','') else 1)\""
check "対応表 apply.md が出来た"       "[ -s '$OUT/apply/apply.md' ]"
check "mp4 が書き出された"             "[ -s '$OUT/apply/cutlist.mp4' ]"
check "完成尺が参考±1.5秒に収まった"   "python3 -c \"
import json, subprocess, sys
ref = json.load(open('$OUT/style/style.json'))['format']['duration_sec']
got = float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                            '-of','csv=p=0','$OUT/apply/cutlist.mp4'],
                           capture_output=True,text=True).stdout.strip())
sys.exit(0 if abs(got-ref) <= 1.5 else 1)\""
check "同一区間の使い回しがない"       "python3 -c \"
import json, sys
c = json.load(open('$OUT/apply/cutlist.json'))['clips']
seen = set()
for x in c:
    key = (x['src'], round(x['in'],1), round(x['out'],1))
    if key in seen: sys.exit(1)
    seen.add(key)
sys.exit(0)\""

echo
echo "=== 6. transcribe（clip_a のみ・モデル読込に時間がかかります）==="
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
