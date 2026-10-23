# 依頼5分類データ v1.7

ModernBERT-Jaの日本語依頼5分類を試すデータと学習・評価補助です。以下はv1.7のデータ・評価記録です。後に採用されたv1.9 seed43の本体接続と現在発話だけの入力契約は[接続記録](../../docs/2026-10-15-modernbert-integration-preparation.md)を参照してください。

**v1.7：全762件を機能・受付範囲に照らして再確認し、754件へ整理済み。** 本文・履歴38件、注釈のみ42件を修正し、専用対話中の8件を除外しました。[変更内容・全件の採否](../../docs/2026-10-14-request-classifier-functional-scope-review.md)を参照してください。v1.7で既存モデルの再評価と3 seedの再学習を完了しました。testは既存86.7%に対して3 seed平均87.1%（標準偏差2.04ポイント）。[比較結果](../../docs/2026-10-14-modernbert-ja-request-classifier-v17-multiseed.md)を参照してください。

| 分割 | 件数 | 用途 |
|---|---:|---|
| `data/train.jsonl` | 566 | 学習 |
| `data/dev.jsonl` | 73 | 設定・閾値選択 |
| `data/test.jsonl` | 75 | 設定固定後の評価（各分類15件） |
| `data/regression.jsonl` | 40 | 「いるかな」など既知の誤判定と境界の確認 |

分類は `not_request` / `enemy_information` / `equip_sword` / `recall_mount` / `other_request`。
整数IDは [labels.json](labels.json)、ラベル境界・出自・入力形式は [データ仕様](../../docs/2026-10-13-request-classifier-data.md) を参照してください。

```bash
python3 dev_tools/request_classifier/dataset.py --check-report dev_tools/request_classifier/data_report.json
python3 -m unittest discover -s dev_tools/request_classifier -p 'test_*.py' -v
```

標準ライブラリだけで検査できます。学習入力は `dataset.model_example(row)` の `text`、教師値は `labels` を使います。注釈・分類理由を入力へ混ぜないでください。未対応の自然な依頼43件は`other_request`＋`unsupported_request`タグで保持し、評価では別集計します。

作成・選別記録：[ユーザー例](../../docs/2026-10-13-request-classifier-curation.md)、[Gemini・Grok](../../docs/2026-10-14-request-classifier-gemini-grok-curation.md)、[プロンプト交換分](../../docs/2026-10-14-request-classifier-cross-prompts-curation.md)、[Codexの会話例107件](../../docs/2026-10-14-request-classifier-codex-conversations.md)。v1.6までの原本・評価結果は保存しています。v1.7は評価データも変更しているため、[旧版の精度](../../docs/2026-10-14-modernbert-ja-request-classifier-retraining.md)と直接比較できません。

## 学習・評価の再実行

```bash
python3 -m venv .dogido_tools/request_classifier/.venv
.dogido_tools/request_classifier/.venv/bin/python -m dogido_server.tls_security --bootstrap
.dogido_tools/request_classifier/.venv/bin/python -m pip install -r dev_tools/request_classifier/requirements-train.txt
.dogido_tools/request_classifier/.venv/bin/python dev_tools/request_classifier/train.py \
  --output-dir .dogido_tools/request_classifier/new-run \
  --report-dir dev_tools/request_classifier/reports/new-run \
  --revision 8cb03f54cb9e30e72459e5f1cedc6d89c7d8dcb5 --device mps
```

CPUで実行する場合は`--device cpu`。出力先は未使用の名前を指定します。初回は公式モデルを取得します。devだけで選択後、test・回帰・学習例の全予測と誤分類を保存します。学習は追加ライブラリが必要ですが、データ・指標・採否台帳の21テストは標準ライブラリだけで実行できます。

複数回の比較には`--seeds 42 43 44`を指定します。各seedのdev最良epochを使い、平均devで共通の学習率を選んで全seedを評価します。`--baseline-checkpoint`で既存モデルを指定すると、同じ入力で再評価して平均・標本標準偏差・誤分類の変化も保存します。単一seedの`--seed`とは同時指定できません。
