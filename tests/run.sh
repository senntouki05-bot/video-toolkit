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
check "LUT用の256binヒストを保持"      "python3 -c \"import json,sys; s=json.load(open('$OUT/style/style.json')); sys.exit(0 if len(s['color']['hist']['r'])==256 else 1)\""

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
echo "=== 6. lut（色をずらした素材を参考へ引き戻せるか）==="
# 暖色・低彩度・明るめにわざと崩した素材を作る → マッチLUTが参考(clip_a)へ寄せ戻すはず
mkdir -p "$OUT/_lutsrc"
ffmpeg -hide_banner -loglevel error -y -i "$FIX/clip_a.mov" \
  -vf "eq=brightness=0.12:saturation=0.5,colortemperature=temperature=3500" \
  -an -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$OUT/_lutsrc/clip_warm.mp4"
bash "$REPO/v" lut "$OUT/style/style.json" --src "$OUT/_lutsrc/clip_warm.mp4" --out "$OUT" || ng "lut が異常終了"
check "match.cube が生成された"        "[ -s '$OUT/lut/match.cube' ]"
check "cubeヘッダが規格通り"           "grep -q '^LUT_3D_SIZE 33' '$OUT/lut/match.cube'"
check "格子点数が 33³=35937"           "[ \$(grep -cE '^[01]\.[0-9]{6} ' '$OUT/lut/match.cube') -eq 35937 ]"
check "使い方メモ lut.md が出来た"     "[ -s '$OUT/lut/lut.md' ]"
check "LUT適用で参考との色距離が縮んだ（独立再計測）" "python3 -c \"
import json, sys
sys.path.insert(0, '$REPO/lib')
from vanalyze import sample_rgb, color_stats, color_distance, run_ffmpeg_bytes
ref = json.load(open('$OUT/style/style.json'))['color']
src, cube = '$OUT/_lutsrc/clip_warm.mp4', '$OUT/lut/match.cube'
times = [3.0, 11.0, 16.0]
bef = color_stats([f for t in times if (f := sample_rgb(src, t))])
def lutf(t):
    rc, d, _ = run_ffmpeg_bytes(['-ss', str(t), '-i', src, '-frames:v', '1',
        '-vf', 'lut3d=' + cube + ',scale=64:36',
        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    return bytes(d[:64*36*3]) if rc == 0 and len(d) >= 64*36*3 else None
aft = color_stats([f for t in times if (f := lutf(t))])
db, da = color_distance(bef, ref), color_distance(aft, ref)
print(f'    色距離 {db} → {da}', file=sys.stderr)
# 絶対差-2では彩度補正だけの改善(2.4)が素通しした（2026-08-15変異テストで実測）→ 相対40%改善を要求
sys.exit(0 if da < db * 0.6 else 1)\""
check "apply も match.cube を出した"   "[ -s '$OUT/apply/match.cube' ]"

echo
echo "=== 6b. Apple Log 2（白書アンカー検証 → 疑似log素材の往復 → マッチLUT）==="
check "カーブ定数=白書アンカー4点と一致" "python3 -c \"
import sys
sys.path.insert(0, '$REPO/lib')
from vlogcurve import log_encode
# Apple Log 2 White Paper 公表値（10bit full range）: 0%→154 / 18%→500 / 90%→697 / 1200%→1023
anchors = [(0.0, 154), (0.18, 500), (0.9, 697), (12.0, 1023)]
sys.exit(0 if all(abs(log_encode(r)*1023 - c) <= 1.0 for r, c in anchors) else 1)\""
check "AWG→709合成行列の健全性"        "python3 -c \"
import sys
sys.path.insert(0, '$REPO/lib')
from vlogcurve import MATS
M = MATS['apple-log2']
sys.exit(0 if max(abs(sum(r)-1) for r in M) < 0.02 and 1.4 < M[0][0] < 2.0 else 1)\""
# 疑似 Apple Log 2 素材を生成（lib自身の逆変換で clip_a を log化）
python3 -c "
import sys; sys.path.insert(0, '$REPO/lib')
from vlogcurve import logify_fn
from vanalyze import build_cube_fn
open('$OUT/_lutsrc/logify.cube', 'w').write(build_cube_fn(logify_fn('apple-log2'), title='test logify'))
"
ffmpeg -hide_banner -loglevel error -y -i "$FIX/clip_a.mov" -vf "lut3d=$OUT/_lutsrc/logify.cube" \
  -an -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$OUT/_lutsrc/clip_log2.mp4"
bash "$REPO/v" lut apple-log2 --out "$OUT" || ng "変換LUT生成が異常終了"
check "変換LUT(apple-log2→709)が出来た" "grep -q '^LUT_3D_SIZE 33' '$OUT/lut/apple-log2-to-rec709.cube'"
check "疑似log素材を変換LUT一発で復元"  "python3 -c \"
import sys
sys.path.insert(0, '$REPO/lib')
from vanalyze import sample_rgb, color_stats, color_distance, run_ffmpeg_bytes
orig, src = '$FIX/clip_a.mov', '$OUT/_lutsrc/clip_log2.mp4'
cube = '$OUT/lut/apple-log2-to-rec709.cube'
times = [3.0, 11.0, 16.0]
base = color_stats([f for t in times if (f := sample_rgb(orig, t))])
raw  = color_stats([f for t in times if (f := sample_rgb(src, t))])
def lutf(t):
    rc, d, _ = run_ffmpeg_bytes(['-ss', str(t), '-i', src, '-frames:v', '1',
        '-vf', 'lut3d=' + cube + ',scale=64:36', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    return bytes(d[:64*36*3]) if rc == 0 and len(d) >= 64*36*3 else None
back = color_stats([f for t in times if (f := lutf(t))])
db, da = color_distance(raw, base), color_distance(back, base)
print(f'    元との色距離: log素材 {db} → 変換後 {da}', file=sys.stderr)
sys.exit(0 if db > 20 and da < 8 and da < db * 0.4 else 1)\""
bash "$REPO/v" lut "$OUT/style/style.json" --src "$OUT/_lutsrc/clip_log2.mp4" \
  --src-log apple-log2 --out "$OUT" || ng "log用マッチLUTが異常終了"
check "マッチLUTにlog前段が内蔵された"  "grep -q 'input: apple-log2' '$OUT/lut/match.cube'"
check "log素材に直接当てて参考へ寄った" "python3 -c \"
import json, sys
sys.path.insert(0, '$REPO/lib')
from vanalyze import sample_rgb, color_stats, color_distance, run_ffmpeg_bytes
ref = json.load(open('$OUT/style/style.json'))['color']
src, cube = '$OUT/_lutsrc/clip_log2.mp4', '$OUT/lut/match.cube'
times = [3.0, 11.0, 16.0]
raw = color_stats([f for t in times if (f := sample_rgb(src, t))])
def lutf(t):
    rc, d, _ = run_ffmpeg_bytes(['-ss', str(t), '-i', src, '-frames:v', '1',
        '-vf', 'lut3d=' + cube + ',scale=64:36', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    return bytes(d[:64*36*3]) if rc == 0 and len(d) >= 64*36*3 else None
aft = color_stats([f for t in times if (f := lutf(t))])
db, da = color_distance(raw, ref), color_distance(aft, ref)
print(f'    参考との色距離: {db} → {da}', file=sys.stderr)
sys.exit(0 if da < db * 0.5 else 1)\""
# apply の --src-log 経路（logのままカット生成→lut3d gradeで書き出しまで）
# ⚠️ out/ 配下は collect_media が素材として拾わない仕様なので、素材は out/ の外に置く
LOGSRC="$(mktemp -d)"
trap 'rm -rf "$LOGSRC"' EXIT
cp "$OUT/_lutsrc/clip_log2.mp4" "$LOGSRC/"
bash "$REPO/v" apply "$LOGSRC" --style "$OUT/style/style.json" --out "$OUT/log_apply" \
  --src-log apple-log2 --render || ng "apply --src-log が異常終了"
check "applyのgradeがlog対応LUTになった" "python3 -c \"import json,sys; c=json.load(open('$OUT/log_apply/apply/cutlist.json')); sys.exit(0 if c['output']['grade'].startswith('lut3d=') else 1)\""
check "log素材のまま書き出しまで通った"  "[ -s '$OUT/log_apply/apply/cutlist.mp4' ]"

echo
echo "=== 7. transcribe（clip_a のみ・モデル読込に時間がかかります）==="
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
