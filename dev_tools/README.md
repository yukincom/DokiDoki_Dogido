# ドギド開発ツール

表示の確認、音声診断、対話の評価、本体に接続していない試作をまとめています。
実行例はリポジトリのルートから使います。

| ディレクトリ | 用途 | 使い方 |
|---|---|---|
| [character-placement](character-placement/README.md) | ドギド・掛け軸の配置、瞬き、考え顔のプレビュー | ルートをローカルHTTPサーバーで開き、`dev_tools/character-placement/` を表示 |
| [voice](voice/) | AECのオフライン確認 | 下記と[音声入力の説明](../docs/voice-echo-cancellation.md)を参照 |
| `catalog_tools/` | 資料整備用の検索・カタログCLI/API | [国語・詩形](../reference/language_education_and_poetry/README.md)・[Minecraft資料](../reference/minecraft_technical/README.md)を参照 |
| [prompt_lab](prompt_lab/README.md) | Rust本番経路の反応確認と、比較用のプロンプト候補 | 本体の正本へ自動反映しない。試験条件は各READMEを参照 |
| [composite_structure_recognition](composite_structure_recognition/README.md) | 橋・飛び石・海中トンネルなどの独立認識試作 | `python -m pytest dev_tools/composite_structure_recognition/test_recognizer.py -q` |

## 音声診断

旧 `diagnose_voice_capture.py` によるPython音声入力の診断は終了しました。現行の音声設定と補助ファイルは、準備済みのPython環境でRust起動の非録音チェックを使います。

```bash
python dogido-rust/scripts/launch_dialogue.py --settings-dir . --voice --check
```

このチェックは録音・音声配送の成功までは確かめません。実マイク・自己音・割り込みの確認は [音声入力の実機チェック](../dogido-rust/manual-dialogue-check.md#rust音声入力の確認) を使います。

AECのオフライン確認は継続します。

```bash

# 導入済みのAEC専用環境で、合成信号による確認
.dogido_tools/echo-cancel/.venv/bin/python dev_tools/voice/check_echo_offline.py

# macOSの架空TTS音声による確認（再生・録音なし）
.dogido_tools/echo-cancel/.venv/bin/python dev_tools/voice/check_echo_speech_offline.py
```

## 旧国語試験の運用終了

旧Pythonの独立テキスト／音声対話と検索比較の実行経路は終了しました。[旧テキスト台本](../docs/language-dialogue-manual-test.md)・[旧音声結果](../docs/language-dialogue-voice-test.md)・[検索比較の記録](../docs/research/language-retrieval-comparison-2026-09-07.md) は過去の結果として保持します。現行の国語・Webは [Rust本体](../dogido-rust/README.md) と [実機チェック](../dogido-rust/manual-dialogue-check.md) で確認します。

データ整備用のPython補助は `dev_tools/catalog_tools/` にまとめ、本体の設定・SDK接続補助とは分離します。本体の起動は `dogido-rust/`、音声環境の導入・知識データの準備・家庭用配布は `scripts/` にあります。
