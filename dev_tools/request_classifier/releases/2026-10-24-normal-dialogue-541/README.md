# 通常対話用16分類・541件（2026-10-24）

前回の890件版から川柳7分類359件を除いた531件へ、今回の実機発話10件をtrainに追加。**train 445 / dev 46 / test 50**。

川柳が画面に表示されていない間に使う分類器の教材。川柳の表示中は別の川柳用分類器へ切り替える方針で、本体の切替・学習・モデル差替えは未実施。

| 分類 | train | dev | test | 合計 |
|---|---:|---:|---:|---:|
| `not_target` | 53 | 6 | 8 | 67 |
| `enemy_information` | 82 | 8 | 6 | 96 |
| `equip_sword` | 67 | 5 | 6 | 78 |
| `recall_mount` | 97 | 5 | 7 | 109 |
| `dogido_recall` | 47 | 3 | 3 | 53 |
| `look_direction` | 25 | 6 | 5 | 36 |
| `look_relative_right` | 7 | 1 | 2 | 10 |
| `look_target` | 8 | 2 | 2 | 12 |
| `look_player` | 15 | 3 | 4 | 22 |
| `look_relative_front` | 6 | 1 | 1 | 8 |
| `look_relative_back` | 7 | 1 | 1 | 9 |
| `look_relative_left` | 7 | 1 | 1 | 9 |
| `look_relative_front_left` | 6 | 1 | 1 | 8 |
| `look_relative_front_right` | 6 | 1 | 1 | 8 |
| `look_relative_back_left` | 6 | 1 | 1 | 8 |
| `look_relative_back_right` | 6 | 1 | 1 | 8 |

## 追加した10件

| 本文 | 正解ラベル |
|---|---|
| ここ水が流れてるよ川がある | `look_direction` |
| ここで終わりだ | `not_target` |
| さらに地下に戻ってきました | `not_target` |
| たいまついっぱいつけたからね | `not_target` |
| いや私はちょっとだからこと近いんじゃないの? | `not_target` |
| 行き当たりだ | `not_target` |
| 松明つってどうなんだろう | `not_target` |
| 松松がいっぱいだね | `not_target` |
| よしよしされてうれしかったの? | `not_target` |
| ちょっと文字数が多いので | `not_target` |

## 保持した条件

- 川柳の行指定3・具体変更3・終了のラベルを除外。残す16分類の本文・ラベル名・ID・group・注釈・順序・元の分割を保持。共通の対象外例は保持する。
- 整数ラベルだけ0–15へ振り直した。旧23分類との対応、元資料・追加記録のSHA-256は [freeze.json](freeze.json) に記録。
- 今回の10件はすべてtrain。dev/testは元資料から通常用ラベルを抽出しただけで、行の移動・追加・書換えはない。
- 現在発話だけを入力し、contextは全件空。注釈・ID・タグ・表示状態の説明をモデル入力へ入れない。
- 元のdev/testは教材設計で参照済みの診断データ。新しい16分類の成績を、元23分類の成績とそのまま比較しない。
- 学習・推論・本体変更は行っていない。川柳用424件とColabノートブックも変更していない。

[全541件の一覧](materials-review.md)・[追加10件の表示／正解指定](../../curation/2026-10-24-normal-dialogue-feedback/README.md)・[件数と入力hash](data_report.json)・[整合性記録](integrity.json)

元資料：[2026-10-24公開保存版](https://github.com/yukincom/AI-Curious-Community/releases/tag/modernbert-research-2026-10-24) の `dev_tools/request_classifier/releases/v7-verse-variety-890/`。
