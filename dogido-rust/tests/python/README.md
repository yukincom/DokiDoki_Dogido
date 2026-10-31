# Pythonテスト

本体の起動には使わないテスト・模擬worker・fixtureを置きます。実行用のPython補助は `../../scripts/` にあります。

- `test_*.py`: pytestによるPython補助の単体テスト。
- `check_*.py`: HTTP等の結合テスト。実モデルや音声を使うものはファイル冒頭に条件を記載しています。
- `*_fixture.py`・`test_support.py`・`fixtures/`: 固定入力や模擬応答。BERT・LLMの解釈精度を証明するものではありません。

リポジトリのルートから実行します。

```sh
python -m pytest -q tests dogido-rust/tests/python/test_*.py
python dogido-rust/tests/python/check_workshop_interpretation.py
```

HTTPテストには準備済みのRustバイナリが必要です。既存の実行条件・CLI引数は各ファイルを参照してください。
