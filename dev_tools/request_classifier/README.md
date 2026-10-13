# 依頼5分類データ v1.6

ModernBERT-Jaの日本語依頼5分類を独立して試すデータと学習・評価補助です。本体には未接続です。

v1.3（train 288件）の30M初回評価はtest **66/75（88.0%）**、回帰 **30/40（75.0%）**。[結果と誤分類](../../docs/2026-10-13-modernbert-ja-request-classifier-evaluation.md)を参照してください。v1.4〜v1.6の追加計284件は未学習です。

| 分割 | 件数 | 用途 |
|---|---:|---|
| `data/train.jsonl` | 572 | 学習（初版250件＋ユーザー例等38件＋生成例284件） |
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

作成・選別記録：[ユーザー例](../../docs/2026-10-13-request-classifier-curation.md)、[Gemini・Grok](../../docs/2026-10-14-request-classifier-gemini-grok-curation.md)、[プロンプト交換分](../../docs/2026-10-14-request-classifier-cross-prompts-curation.md)、[Codexの会話例107件](../../docs/2026-10-14-request-classifier-codex-conversations.md)。dev/test/regressionは初版から変更していません。

## 学習・評価の再実行

```bash
python3 -m venv .dogido_tools/request_classifier/.venv
.dogido_tools/request_classifier/.venv/bin/python -m pip install -r dev_tools/request_classifier/requirements-train.txt
.dogido_tools/request_classifier/.venv/bin/python dev_tools/request_classifier/train.py \
  --output-dir .dogido_tools/request_classifier/new-run \
  --report-dir dev_tools/request_classifier/reports/new-run \
  --revision 8cb03f54cb9e30e72459e5f1cedc6d89c7d8dcb5 --device mps
```

CPUで実行する場合は`--device cpu`。出力先は未使用の名前を指定します。初回は公式モデルを取得します。devだけで選択後、test・回帰・学習例の全予測と誤分類を保存します。学習は追加ライブラリが必要ですが、データ・指標の11テストは標準ライブラリだけで実行できます。
