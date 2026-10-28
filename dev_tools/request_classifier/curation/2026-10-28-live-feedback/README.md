# 2026-10-28 分類精度向上のための収集

ユーザーが提示した計7件の原文・表示結果・正解指定を記録した。「上下も作ろっか」の指定により、上下2分類を学習用の追加案として定義した。収集段階で、学習データへの組み込み・再学習・本体反映は未実施。

| 提示時刻 | 入力原文 | 表示された分類 | 表示確率 / 推論時間 | ユーザーの正解指定 |
|---|---|---|---|---|
| 08:57:57 | 上の方にいるんじゃない? | `look_relative_back`（後ろを向く） | 62.80% / 5.2 ms | 上を向く → `look_relative_up` |
| 09:06:52 | ちょっと文字数が多い感じがするか | `senryu_line_upper`（上五の行指定） | 67.82% / 5.4 ms | 対象外 → `not_target` |
| 未記載 | に変更してください | `senryu_end`（川柳相談の終了） | 97.98% / 14.1 ms | other → `not_target` |
| 15:53:06 | 真横においでよ | `look_relative_front`（前を向く） | 72.08% / 20.9 ms | よびよせ → `dogido_recall` |
| 15:52:20 | ドギドどこ? | `not_target`（対象外・その他） | 100.00% / 5.8 ms | よびよせ → `dogido_recall` |
| 15:52:06 | とぎどおいで | `look_relative_back`（後ろを向く） | 57.75% / 7.2 ms | よびよせ → `dogido_recall` |
| 15:50:45 | どきどこっちにおいで | 未記載 | 未記載 | よびよせ → `dogido_recall` |

- [evidence.jsonl](evidence.jsonl)：7件の表示記録とユーザーの訂正。収集日は2026-10-28。ログの日付、3件目の時刻・入力元、使用モデルは提示されていないため未記載として保持する。15:50:45は入力元が `player_input` で、分類表示は未提示。
- [candidates.jsonl](candidates.jsonl)：追加候補7件。通常用は上方向1件・ドギド呼び寄せ4件、川柳用は `not_target` 2件。モードを分けて使用する。`とぎど`・`どきど` は提示された表記のまま保持する。
- [pending.jsonl](pending.jsonl)：0件。前回保留した1件目を `look_relative_up` の候補へ移し、evidenceにユーザーの上下分類追加指示を記録した。

## 上下2分類の追加案

[proposed-normal-labels.json](proposed-normal-labels.json) は現行通常16分類のIDを保ち、末尾へ2分類を追加した学習用の18分類案。本体の採用モデルは通常16分類のまま。

| 追加ID | ラベル | 意味 | 今回の実発話数 |
|---|---|---|---:|
| 16 | `look_relative_up` | ドギドが上方へ視線を向ける。移動を求める分類ではない。 | 1 |
| 17 | `look_relative_down` | ドギドが下方へ視線を向ける。移動を求める分類ではない。 | 0 |

既存の `look_direction` はプレイヤーの視線・照準に沿う分類として維持する。今回の呼び寄せ4件はドギド自身への `dogido_recall` とし、乗用動物への `recall_mount` と区別する。下方向の実発話は今後収集する。

参照した現行定義：[通常16分類](../../releases/2026-10-24-normal-dialogue-four-system/labels.json)、[川柳8分類](../../releases/2026-10-24-senryu-only-424/labels.json)、[視線実行](../../../../adapter/minecraft-fabric/src/main/java/dogido/fabric/body/BodyLookController.java)。

再推論・合成例追加・自動テストは行っていない。元の誤分類を表示の書き換えや固定語句の補正で隠す変更はしていない。
