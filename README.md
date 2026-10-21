# video-toolkit

撮影した動画素材の「機械でできる作業」をまとめた、ローカル実行のツール束です。ffmpeg と faster-whisper を使い、素材をネットに出さずに処理します。

## 主な機能

| サブコマンド | 何をするか | 出力 |
|---|---|---|
| `log` | 素材を全部 ffprobe にかけて一覧にします | `log.csv` / `log.md` / `log.json` |
| `shots` | シーン検出＋代表フレーム抽出＋コンタクトシート | `frames/*.jpg` / `*_sheet_*.jpg` / `shots.json` |
| `transcribe` | 文字起こし → 字幕 | `.srt` / `.vtt` / `.txt` / `.words.json` |
| `qc` | 納品前の検査（尺・解像度・fps・LUFS・黒フレーム・無音） | `qc.md` / `qc.json` |
| `style` / `apply` | 参考動画の編集スタイル（リズム・色・音）を数値にして、素材に当てはめます | `style.json` / カットリスト |
| `lut` | 参考動画から `.cube` の3D LUT を作ります（Apple Log / Log 2 の素材にも対応） | `.cube` |
| `cut` | カットリスト JSON を実際に書き出します（速度・色・BGM・効果音込み） | `.mp4` |
| `music` | Apple Loops のステムを重ねて BGM を1本に組みます | `.wav` |
| `test` | 合成クリップで自己テスト | — |

- **素材のフォルダには一切書き込みません。** 出力は `out/` の下（`--out` で変更できます）。
- コンタクトシートの JPEG は、AI に画像として読ませて「この素材でリビングが映っているカットはどれか」と聞く使い方を想定しています。

## 使い方

```bash
bash v log   /path/to/footage
bash v shots /path/to/footage
bash v transcribe interview.mov
bash v qc    final.mov --expect-res 3840x2160 --expect-fps 30
bash v test
```

必要なもの：macOS、ffmpeg / ffprobe（Homebrew）、Python 3。
文字起こしだけ faster-whisper が入った Python が必要です。環境変数 `WHISPER_VENV_PY` にその Python のパスを指定します（既定は `~/whisper-venv/bin/python3`）。

## 実測（2026年8月・M1 Pro 16GB）

- 文字起こしは実時間の約3倍速でした（19秒の素材を約6秒・`large-v3-turbo` を int8 で CPU 実行）。
- いずれも合成音声（macOS の `say`）での実測です。現場の音や環境ノイズが入った実素材では、精度も速度も落ちます。実素材での実測はまだ行っていません。

## やらないこと

カットの良し悪しの判断、ベストテイクの選定、音楽へのカット合わせ、DaVinci Resolve の直接操作は行いません。

## 開発について

AIを使い、1人で開発しました。

## このリポジトリについて

- 作者が実際に使っている道具のソースコードを、閲覧用に公開しています。
- ライセンスは付けていません。著作権は作者に帰属します。
