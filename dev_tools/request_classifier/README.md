# 依頼5分類データ v1

ModernBERT-Jaで試すための日本語データです。本体には未接続で、モデル学習も未実施です。

| 分割 | 件数 | 用途 |
|---|---:|---|
| `data/train.jsonl` | 250 | 学習（各分類50件） |
| `data/dev.jsonl` | 75 | 設定・閾値選択（各分類15件） |
| `data/test.jsonl` | 75 | 設定固定後の評価（各分類15件） |
| `data/regression.jsonl` | 40 | 「いるかな」など既知の誤判定と境界の確認 |

分類は `not_request` / `enemy_information` / `equip_sword` / `recall_mount` / `other_request`。
整数IDは [labels.json](labels.json)、ラベル境界・出自・入力形式は [データ仕様](../../docs/2026-10-13-request-classifier-data.md) を参照してください。

```bash
python3 dev_tools/request_classifier/dataset.py --check-report dev_tools/request_classifier/data_report.json
python3 -m unittest discover -s dev_tools/request_classifier -p 'test_*.py' -v
```

標準ライブラリだけで検査できます。学習入力は `dataset.model_example(row)` の `text`、教師値は `labels` を使います。注釈・分類理由を入力へ混ぜないでください。
