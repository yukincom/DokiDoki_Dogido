# 通常対話・4系統＋共通otherの学習教材（2026-10-24）

**train 818件 / dev 46件 / test 50件。分類は従来の16分類。** 既存train 482件に、[対照教材336件](../../curation/2026-10-24-four-system-contrast/materials-review.md)を追加した。内訳は新作328件・旧教材の再利用8件。dev/test・ラベルIDは578件版と同一。

[教材ZIP](four-system-training-data.zip)に4系統の学習ファイル、共通教材、出典、説明をまとめた。

## 4系統の学習ファイル

どの系統も818件すべてを学ぶ。そのうえで、担当分類と関連する対照例を1回追加し、同じepoch内で計2回提示する。その他は1回。全件を連結してからseedで通常の学習shuffleを行う。**繰り返しは新しい例数に数えない。** 2倍は初期比較の設定であり、最適な割合を確認した結果ではない。

| 系統 | 担当分類の異なる例数 | 1 epochの全提示回数 | 学習ファイル |
|---|---:|---:|---|
| 視線11種＋ドギド接近 | 310 | 1,184 | [spatial](experts/spatial/train.jsonl) |
| 動物呼び出し | 113 | 945 | [mount](experts/mount/train.jsonl) |
| 剣への持ち替え | 99 | 937 | [sword](experts/sword/train.jsonl) |
| モブ情報 | 106 | 942 | [mob](experts/mob/train.jsonl) |

共通otherは**190種類**で、すべての系統へ入る。担当外の操作例も元の正解ラベルを保つ。otherに付け替えない。[分類別の提示回数](expert-inventory.json)。

## seedと使用方法

- 学習seedは **42・43・44・45・46・47・48** の7通り。4系統×7＝28学習の[準備一覧](training-jobs.json)。今回は実行していない。
- 分割・教材は全seed共通。前回の共通seed43モデルを出発点として、4系統とも分類headを含む同一重みから開始する計画。seedは学習中の乱数に使う。7種類の独立したheadを作って合成する計画ではない。
- ルートの [train.jsonl](train.jsonl) は重複のない818件で、既存の `--material-release` に対応する。
- `experts/*/train.jsonl` は反復込みの学習ストリーム。同一IDの反復を意図的に含む。前回専門学習pilotと同じ `EncodedRows` への行連結で読む。`replay.jsonl` は内訳であり、学習ストリームに重ねて追加しない。
- [training-plan.json](training-plan.json) に初期モデルのhash、選択・比較条件を保存した。学習率・epoch・合成候補の決定と実行は次の作業。

## 確認範囲

元482件の原文・順序・ID・分割を保持。共有818件の正規化入力重複と、dev/testとのgroup・追加出典の混入は0件。全文と境界はCodexで読み取りレビューした。保存済みtokenizerで全914件は16〜36 tokenに収まり、128 token超過は0件。追加は合成例なので実発話の多様さを保証しない。元dev/testは既知の診断用で、未知の実機精度は未評価。

[全件数](data_report.json)・[準備時の照合記録](preparation-review.json)・[詳しい記録](../../../../docs/2026-10-24-four-system-contrast-materials.md)。
