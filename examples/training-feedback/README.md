# 学習候補パイプラインの架空データ例

このディレクトリは、評価協力版が扱う JSONL の形を確認するための例です。
登場する会話、ID、時刻、Minecraft の状況はすべて架空で、実際のプレイログや個人情報は含みません。

- `inbox/evaluation_flags.example.jsonl`: プレイ中に `↑` / `↓` で付けた評価の例
- `reviews/annotations.example.jsonl`: 人が候補を確認した結果の例

実際のデータは Git 管理外の `.dogido_training/` に作られます。この例をコピーして使う必要はありません。
評価データを外部へ自動送信する機能もありません。

候補の作成、確認、承認方法は [学習データ計画](../../docs/training-data-plan.md) を参照してください。
