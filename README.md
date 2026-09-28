# video-toolkit

撮影素材の**機械作業**をClaude Codeから叩くツール束。ffmpeg + faster-whisper、**全部ローカル実行**。
（調査メモの正本は Obsidian `QUIRIO/20_映像・SNS事業/13_社内動画編集ツール/Claude Codeで動画編集_リサーチ_2026-08-15.md`）

## 使い方

```
bash ~/dev/video-toolkit/v <サブコマンド> <素材フォルダ or ファイル>
```

`cd` は不要。どこから叩いても動く。

| サブコマンド | 何をする | 出力 |
|---|---|---|
| `log` | 素材を全部 ffprobe → 一覧表 | `log.csv` / `log.md` / `log.json` |
| `shots` | シーン検出＋代表フレーム抽出＋コンタクトシート | `frames/*.jpg` / `*_sheet_*.jpg` / `shots.json` |
| `transcribe` | 文字起こし → 字幕 | `.srt` / `.vtt` / `.txt` / `.words.json` |
| `qc` | 納品QC（尺・解像度・fps・LUFS・黒フレーム・無音） | `qc.md` / `qc.json` |
| `cut` | カットリストJSONを実際に書き出す（速度・色・BGM・SFX込み） | `.mp4` / カット表 `.md` |
| `music` | Apple Loops のステムを重ねてBGMを1本に組む | `.wav` |
| `test` | 合成クリップで4本すべてを自己テスト | — |

出力先は `~/dev/video-toolkit/out/<素材フォルダ名>/`。
**素材フォルダには一切書き込まない**（`--out` で出力先変更可）。

### 例

```bash
# 素材の棚卸し
bash ~/dev/video-toolkit/v log "/Volumes/SSD/2026-08-15 物件A"

# 何が映っているかClaudeに見せる（コンタクトシートを読ませる）
bash ~/dev/video-toolkit/v shots "/Volumes/SSD/2026-08-15 物件A"

# ナレーション/インタビューの字幕
bash ~/dev/video-toolkit/v transcribe ~/Movies/インタビュー.mov

# 納品直前チェック（4K・30fps 指定で照合）
bash ~/dev/video-toolkit/v qc ~/Movies/納品.mov --expect-res 3840x2160 --expect-fps 30
```

## Claude Code から使うとき

Claude に素材フォルダを教えるだけでよい。Claude は
`shots` が吐いたコンタクトシート JPEG を**実際に目で見て**内容を判断できるので、
「この素材の中でリビングが映っているカットはどれか」といった質問に答えられる。

## 実測値（2026-08-15・M1 Pro 16GB）

| 項目 | 実測 |
|---|---|
| 文字起こし速度 | **実時間の 2.8〜3.1倍速**（19秒の素材を約6秒／`large-v3-turbo` int8 CPU） |
| モデル読み込み | 3〜4秒（2回目以降・キャッシュ済） |
| 文字起こし精度 | 合成ナレーション3文が**3文とも完全一致** |
| 自己テスト | **18項目すべて合格**（`v test`） |
| 変異テスト | 2種の欠陥を注入し、いずれもテストが検知することを確認 |

※ 合成音声（macOS `say` / Kyoko）での実測。実際の現場音・環境ノイズ入りの素材では精度も速度も落ちる。
実素材での実測はまだ行っていない。

## 前提

- `ffmpeg` / `ffprobe`（Homebrew・実測 8.1.2）
- Python 3.14（システム）＋標準ライブラリのみ
- 文字起こしのみ `~/Claud_and_VScode/whisper-tool/venv` の faster-whisper 1.2.1 を再利用
  （**新規インストール不要**。`large-v3-turbo` は `~/.cache/huggingface` にキャッシュ済）

## やらないこと

カットの良し悪しの判断・ベストテイク選定・音楽へのカット合わせ・Resolveの直接操作。
規律の正本は [CLAUDE.md](CLAUDE.md)。
