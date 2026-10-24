# 川柳専用の学習データ（2026-10-24）

前回の890件版（`v7-verse-variety-890`）から、川柳7分類359件と既存の対象外58件を抽出した **8分類・417件**。新しい例文やラベル修正は加えていない。

**利用条件：画面に川柳が表示されている間、川柳用の分類器へ切り替える。** これは今回指定された接続方針であり、表示判定・切替処理・学習・モデル差替えは未実施。

| 分類 | train | dev | test | 合計 |
|---|---:|---:|---:|---:|
| 対象外 (`not_target`) | 44 | 6 | 8 | 58 |
| 上五の相談・行指定 (`senryu_line_upper`) | 48 | 6 | 3 | 57 |
| 中七の相談・行指定 (`senryu_line_middle`) | 48 | 6 | 3 | 57 |
| 下五の相談・行指定 (`senryu_line_lower`) | 56 | 8 | 5 | 69 |
| 上五の具体変更 (`senryu_change_upper`) | 42 | 3 | 6 | 51 |
| 中七の具体変更 (`senryu_change_middle`) | 42 | 3 | 4 | 49 |
| 下五の具体変更 (`senryu_change_lower`) | 48 | 4 | 4 | 56 |
| 川柳相談の終了 (`senryu_end`) | 14 | 3 | 3 | 20 |
| **合計** | **342** | **39** | **36** | **417** |

## 使うファイル

- [train.jsonl](train.jsonl)、[dev.jsonl](dev.jsonl)、[test.jsonl](test.jsonl)：元の分割・順序・JSONL行をそのまま保持。
- [labels.json](labels.json)：今回の8分類の整数ID（0–7）。元23分類のIDとの対応は [freeze.json](freeze.json) に保存。
- [materials-review.md](materials-review.md)：全417件を分類別に読むための一覧。
- [split-assignments.json](split-assignments.json)、[data_report.json](data_report.json)、[freeze.json](freeze.json)：group分割、件数、出典、SHA-256。

## 引き継ぐ意味と条件

- `senryu_line_*` は対象行への相談・感想・変更要求で、変更後の具体的な語句を指定していない。行指定だけを編集実行の許可にしない。
- `senryu_change_*` は対象行と変更後の具体的な語句を提示した例。候補・希望・提案も含む。元の注釈を保持し、命令形に書き換えていない。
- `senryu_end` は相談の終了。未採用案の採否は既存処理で別に扱う。
- `not_target` は元データにあった挨拶・感想・一般質問など58件。川柳表示中にも対象外の発話を区別するため保持した。剣・呼び出し・視線・モブ照会の正例は含めていない。
- 現在の発話だけを入力とし、`context` は全件空。`rationale`、`source_keys`、ID、タグ、表示状態の説明をモデル入力に混ぜない。
- 本文、分類名、ID、group、注釈、出典、元のtrain/dev/testを保持。8分類の整数IDだけを連番に振り直した。元23分類の重み・設定へそのまま差し替えるためのファイルではない。
- dev/testは前回の教材設計・比較で参照済みの診断用。未知の実機発話に対する評価ではない。対象外の追加や、相談中の世界操作をどう扱うかはこの抽出では変更していない。

元資料：[2026-10-24公開アーカイブ](https://github.com/yukincom/AI-Curious-Community/releases/tag/modernbert-research-2026-10-24) 内の `dev_tools/request_classifier/releases/v7-verse-variety-890/`。取得した教材は当時の学習成果物内のコピーとも一致。今日の新しい実機ログは追加していない。
