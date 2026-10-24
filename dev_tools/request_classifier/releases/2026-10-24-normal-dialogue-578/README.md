# 通常対話用16分類・578件（2026-10-24）

[前版546件](../2026-10-24-normal-dialogue-546/README.md)へ、ユーザーが追加を指示した[道具・雑談の合成例32件](../../curation/2026-10-24-normal-dialogue-tool-contrast/materials-review.md)をtrainへ追加。**train 482 / dev 46 / test 50**。

32件の内訳は `equip_sword` 8件、`not_target` 24件。実機フィードバック15件を含む既存546件の本文・ID・順序・分割と、16分類のラベル対応を保持した。新しい32件の出自は `synthetic` のまま。

| 分類 | train | dev | test | 合計 |
|---|---:|---:|---:|---:|
| `not_target` | 82 | 6 | 8 | 96 |
| `enemy_information` | 82 | 8 | 6 | 96 |
| `equip_sword` | 75 | 5 | 6 | 86 |
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

## 条件

- 現在発話だけを入力し、contextは全件空。ラベル・理由・出自はモデル本文へ入れない。
- 元890件から川柳7分類359件を除いた531件に、実機15件と合成32件を追加した。追加47件はすべてtrain。dev/testと元の学習行は移動・書換えなし。
- 川柳用8分類と通常用16分類は別教材。掛け軸と受付状態による一方だけへの切替は本体担当で扱う。この教材は本体の状態を変更しない。
- 元のdev/testは教材設計で参照済みの診断データ。追加32件・実機15件の成績は学習内確認であり、未知の実機精度ではない。
- 学習・モデル差替え・本体接続は未実施。

[全578件の一覧](materials-review.md)・[件数と入力hash](data_report.json)・[固定条件と出典](freeze.json)・[整合性記録](integrity.json)
