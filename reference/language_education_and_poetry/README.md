# 詩の基礎知識

ドギドが明示された詩の質問に答えるための、出典付きの用語・詩形資料です。実行時はRustの `dogido-rust/src/knowledge/` が読み取り、外部通信や学習対話を開始しません。

| ファイル | 内容 |
|---|---|
| `makurakotoba.json` | 枕詞などの基礎用語と範囲 |
| `japanese_poetry_forms.json` | 日本語の詩形・音数の基礎 |
| `world_poetry.json` | 世界の詩形・分類 |
| `sources.json` | 上記が参照する出典36件・機関23件 |
| `index.json` | 59件の知識と出典の検索索引・SHA-256 |
| `schema.json` | 収録形式と出典境界の検証 |

説明・分類の編集上の整理は `editorial_synthesis` 等で区別し、各事実の典拠と所在は `source_refs` に保持します。詩本文の転載は含みません。出典の権利・利用範囲は `sources.json` の各項目を参照してください。

資料整備用の検索と検証は、リポジトリのルートから実行します。

```sh
python -m dev_tools.catalog_tools.reference_catalog 枕詞
python scripts/build_reference_index.py --check
python scripts/validate_reference_catalog.py
```

2026-10-21に、別リポジトリで保持する旧learnの一般文法・漢字・語彙データと専用検索処理を本体から削除しました。[変更範囲](../../docs/2026-10-21-generation-and-knowledge-scope.md)。
