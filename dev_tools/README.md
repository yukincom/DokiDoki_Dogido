# ドギド開発ツール

表示の確認、音声診断、対話の評価、本体に接続していない試作をまとめています。
実行例はリポジトリのルートから使います。

| ディレクトリ | 用途 | 使い方 |
|---|---|---|
| [character-placement](character-placement/README.md) | ドギド・掛け軸の配置、瞬き、考え顔のプレビュー | ルートをローカルHTTPサーバーで開き、`dev_tools/character-placement/` を表示 |
| [voice](voice/) | マイク入力の診断とAECのオフライン確認 | 下記と[音声入力の説明](../docs/voice-echo-cancellation.md)を参照 |
| [language_dialogue](language_dialogue/) | 国語対話の手動試験と検索結果の比較・集計 | [テキスト試験](../docs/language-dialogue-manual-test.md)・[音声試験](../docs/language-dialogue-voice-test.md) |
| [composite_structure_recognition](composite_structure_recognition/README.md) | 橋・飛び石・海中トンネルなどの独立認識試作 | `python -m pytest dev_tools/composite_structure_recognition/test_recognizer.py -q` |

## 音声診断

```bash
# 診断オプションの表示（マイクは起動しない）
python dev_tools/voice/diagnose_voice_capture.py --help

# 導入済みのAEC専用環境で、合成信号による確認
.dogido_tools/echo-cancel/.venv/bin/python dev_tools/voice/check_echo_offline.py

# macOSの架空TTS音声による確認（再生・録音なし）
.dogido_tools/echo-cancel/.venv/bin/python dev_tools/voice/check_echo_speech_offline.py
```

## 国語対話

```bash
# 準備確認のみ。実際の対話試験では --check を外す
zsh dev_tools/language_dialogue/start_language_web_test.command --check
zsh dev_tools/language_dialogue/start_language_voice_test.command --check
```

`compare_language_retrieval.py` と `probe_language_rule_projection.py` は旧対話仕様の評価用です。
現在の仕様に対して比較を再実行するときは、[評価条件](../docs/research/language-retrieval-comparison-2026-09-07.md)を先に確認してください。
`summarize_language_retrieval.py` は保存済みの比較結果を匿名化・集計します。

本体の起動、音声環境の導入、知識データや音声キャッシュの生成は `scripts/` にあります。
