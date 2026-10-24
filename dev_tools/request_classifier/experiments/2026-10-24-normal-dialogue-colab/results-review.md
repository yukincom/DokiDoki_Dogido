# 通常対話16分類・578件のColab学習結果（2026-10-24）

ユーザーが返却した `modernbert-normal578-results.zip` を受領。予定した16分類、train 482 / dev 46 / test 50、seed42・43・44の3試行が完了している。GPUはTesla T4。各試行10 epochからdevで選んだモデルを保存している。

## 成績

| seed | 選択epoch | train | dev | dev macro F1 | test | test macro F1 |
|---:|---:|---:|---:|---:|---:|---:|
| 42 | 10 | 482/482 | 38/46 | 0.8704 | 38/50 | 0.6358 |
| 43 | 9 | 482/482 | 39/46 | 0.8930 | 41/50 | 0.7106 |
| 44 | 7 | 481/482 | 39/46 | 0.8823 | 38/50 | 0.6762 |

test正解率の平均は **78.00%**、シード間の標本標準偏差は **3.46ポイント**。test macro F1は **0.6742 ± 0.0374**。

学習例には高く適合している一方、対象外の発話、視線方向、呼び出しとの区別に誤りが残る。seed43はdev macro F1と今回のtest正解数が最も高い。本体へ採用するモデルはまだ指定・変更していない。

## 追加した行の学習内確認

| seed | 実機フィードバック15件 | 合成例32件 |
|---:|---:|---:|
| 42 | 15/15 | 32/32 |
| 43 | 15/15 | 32/32 |
| 44 | 15/15 | 31/32 |

実機15件は全シードで正解。合成32件ではseed44だけ「ここ、どっちから来たのか分からなくなったよ。」を `not_target` から `enemy_information` へ誤分類した（softmax 85.9%）。全47件はtrainに含むため、この成績は追加例への適合確認である。

## testの分類別正解数

| 正解分類 | 対象数 | seed42 | seed43 | seed44 |
|---|---:|---:|---:|---:|
| `not_target` | 8 | 3 | 6 | 4 |
| `enemy_information` | 6 | 5 | 5 | 5 |
| `equip_sword` | 6 | 6 | 6 | 6 |
| `recall_mount` | 7 | 7 | 7 | 6 |
| `dogido_recall` | 3 | 3 | 2 | 2 |
| `look_direction` | 5 | 4 | 5 | 5 |
| `look_relative_right` | 2 | 2 | 2 | 2 |
| `look_target` | 2 | 2 | 1 | 0 |
| `look_player` | 4 | 3 | 3 | 3 |
| `look_relative_front` | 1 | 0 | 0 | 0 |
| `look_relative_back` | 1 | 0 | 0 | 1 |
| `look_relative_left` | 1 | 0 | 0 | 0 |
| `look_relative_front_left` | 1 | 0 | 1 | 1 |
| `look_relative_front_right` | 1 | 1 | 1 | 1 |
| `look_relative_back_left` | 1 | 1 | 1 | 1 |
| `look_relative_back_right` | 1 | 1 | 1 | 1 |

剣依頼は全シードで6/6正解。一方、対象外8件は3/8・6/8・4/8に留まる。前・左などの方向分類にはtestが1件しかなく、その1件の誤りで再現率が0になる。各方向を一般に理解できていないと断定する材料にはしない。

## testで誤った発話

いずれかのシードが誤った18件を示す。「正解」はそのシードが正解ラベルを出したことを表す。ラベルは固定教材のものを使い、今回の結果で変更していない。

| 原文 | 正解ラベル | seed42 | seed43 | seed44 |
|---|---|---|---|---|
| 剣にはもう持ち替えたよ。 | `not_target` | `equip_sword` | 正解 | 正解 |
| 馬はさっき自分で連れてきた。 | `not_target` | `dogido_recall` | `recall_mount` | `recall_mount` |
| 敵が三体見えたから逃げたよ。 | `not_target` | `enemy_information` | `enemy_information` | `enemy_information` |
| 近くのモンスターを数えて。 | `enemy_information` | `dogido_recall` | 正解 | 正解 |
| 一緒に空を見ようよ！ | `look_direction` | `recall_mount` | 正解 | 正解 |
| 顔だけこちらへ向けてもらえるかな。 | `look_player` | `dogido_recall` | `dogido_recall` | `look_relative_back` |
| 私はもう少し近くまで行ってみるね。 | `not_target` | `dogido_recall` | 正解 | `dogido_recall` |
| 「右を向いて」と書かれた看板があったよ。 | `not_target` | `look_relative_right` | 正解 | 正解 |
| 何かの気配を前から感じるような気がする | `look_relative_front` | `enemy_information` | `look_direction` | `look_direction` |
| 何かの気配を後ろから感じるような気がする | `look_relative_back` | `enemy_information` | `look_direction` | 正解 |
| 何かの気配を左から感じるような気がする | `look_relative_left` | `enemy_information` | `look_direction` | `look_relative_back` |
| 何かの気配を左斜め前から感じるような気がする | `look_relative_front_left` | `enemy_information` | 正解 | 正解 |
| 今は替えなくていい。それより敵の数を教えて。 | `enemy_information` | 正解 | `not_target` | `not_target` |
| このブロックは何でできているんだろう？ | `look_target` | 正解 | `not_target` | `enemy_information` |
| 私が立っているところへ、もう少し近寄ってもらえる？ | `dogido_recall` | 正解 | `look_player` | `look_player` |
| 手持ちの剣が壊れちゃった。 | `not_target` | 正解 | 正解 | `equip_sword` |
| ドギドウズを呼んでください | `recall_mount` | 正解 | 正解 | `dogido_recall` |
| おっ！いいもの見つけた！ | `look_target` | 正解 | 正解 | `look_direction` |

3シード共通で、過去の報告「馬はさっき自分で連れてきた。」「敵が三体見えたから逃げたよ。」を依頼へ誤分類している。「顔だけこちらへ向けてもらえるかな。」と、前・左からの気配を伝える発話も全シードで誤った。devにも「ゾンビ出たら泣いちゃう？」をモブ情報へ分類する誤りが共通して残る。

全誤分類の原文・確率は [seed42](artifacts/colab-results/reports/seed-42/misclassifications.md)・[seed43](artifacts/colab-results/reports/seed-43/misclassifications.md)・[seed44](artifacts/colab-results/reports/seed-44/misclassifications.md) に保持した。softmaxは未校正であり、誤りに高い値が付いている場合もある。

## 解釈の範囲

- dev/testは教材設計で参照済みの診断データ。未知の実機精度ではなく、標準偏差もその信頼区間ではない。
- 追加32件の効果は、同じ16分類・同じ分割で追加前後を学習した対照結果がないため、この結果だけでは分離できない。
- 川柳専用版は8分類・別教材であり、今回の16分類と成績を直接比較しない。実行GPUも前回のA100からT4に変わったが、これを誤分類の原因とは判定していない。
- 現時点では結果の受領・照合まで。データ追加、ラベル変更、再学習、本体モデルの差替えは行っていない。

## 受領時の照合

- ZIP内の127ファイルを同梱SHA-256と照合。準備時入力58ファイルがバイト単位で一致。
- 全3シード・全分割の1,734予測について、本文・正解・予測・確率の対応、正解数、分類別指標、混同行列、出典別成績を再集計し、保存結果との一致を確認。
- 3シードの平均・標本標準偏差を再計算。10 epochのdev履歴から選択条件を再適用し、保存されたepoch 10 / 9 / 7と一致。全選択を固定した時刻も照合。
- 学習コード・教材・条件、3モデルの重みhash、16分類のラベル対応、分類head形状、float32形式、共通tokenizerを確認。
- 同梱コードは実行せず、ローカルでのモデル推論・追加学習・実装テストは行っていない。

[照合記録](results-review.json)に全数値と重みhashを保存。元ZIPはDownloadsに残し、この実験フォルダにも同名でコピーした。展開先は `artifacts/colab-results/`。ZIP・展開物は既存のignore対象で、Gitには確認記録だけを保存する。

- ZIP SHA-256: `78a76e3e344351b98c76b81e91d8047c67991776ec0c1d391e5222ebb86b0a40`
- [Colabの結果概要](artifacts/colab-results/reports/seed-summary.md)
