# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## test：20 / 50件

### legacy__train-011

- 発話: 剣にはもう持ち替えたよ。
- 正解: `not_target`
- 予測: `equip_sword`（100.0%）

### legacy__train-012

- 発話: 馬はさっき自分で連れてきた。
- 正解: `not_target`
- 予測: `enemy_information`（84.7%）

### legacy__train-013

- 発話: 敵が三体見えたから逃げたよ。
- 正解: `not_target`
- 予測: `enemy_information`（100.0%）

### legacy__train-018

- 発話: 手持ちの剣が壊れちゃった。
- 正解: `not_target`
- 予測: `equip_sword`（99.0%）

### live__short_runtime_046

- 発話: ドギドウズを呼んでください
- 正解: `recall_mount`
- 予測: `dogido_recall`（76.7%）

### user_addition__user_20261022_008

- 発話: あの溶岩が流れているとこ行ってみようか
- 正解: `look_direction`
- 予測: `enemy_information`（95.4%）

### user_addition__user_20261022_064

- 発話: おっ！いいもの見つけた！
- 正解: `look_target`
- 予測: `enemy_information`（99.7%）

### user_addition__user_20261022_066

- 発話: このブロックは何でできているんだろう？
- 正解: `look_target`
- 予測: `enemy_information`（99.4%）

### contrast__contrast_029

- 発話: 顔だけこちらへ向けてもらえるかな。
- 正解: `look_player`
- 予測: `enemy_information`（83.3%）

### contrast__contrast_030

- 発話: 私が立っているところへ、もう少し近寄ってもらえる？
- 正解: `dogido_recall`
- 予測: `enemy_information`（88.4%）

### contrast__contrast_032

- 発話: 私はもう少し近くまで行ってみるね。
- 正解: `not_target`
- 予測: `look_relative_right`（89.5%）

### contrast__contrast_046

- 発話: 右は向かなくていいから、私のそばに来て。
- 正解: `dogido_recall`
- 予測: `look_relative_right`（94.3%）

### contrast__contrast_048

- 発話: 「右を向いて」と書かれた看板があったよ。
- 正解: `not_target`
- 予測: `look_relative_right`（99.8%）

### gaze_variant__gaze_direction_10_front

- 発話: 何かの気配を前から感じるような気がする
- 正解: `look_relative_front`
- 予測: `enemy_information`（100.0%）

### gaze_variant__gaze_direction_10_back

- 発話: 何かの気配を後ろから感じるような気がする
- 正解: `look_relative_back`
- 予測: `look_relative_back_right`（50.2%）

### gaze_variant__gaze_direction_10_left

- 発話: 何かの気配を左から感じるような気がする
- 正解: `look_relative_left`
- 予測: `enemy_information`（83.0%）

### gaze_variant__gaze_direction_10_front_right

- 発話: 何かの気配を右斜め前から感じるような気がする
- 正解: `look_relative_front_right`
- 予測: `look_relative_right`（55.5%）

### gaze_variant__gaze_far_sky_shared_01

- 発話: ドギド、夕焼けがきれいだよ。一緒に見よう。
- 正解: `look_direction`
- 予測: `enemy_information`（98.7%）

### gaze_variant__gaze_far_sky_shared_02

- 発話: 今夜は星がよく見えるね。空を見上げてみようよ。
- 正解: `look_direction`
- 予測: `enemy_information`（99.0%）

### gaze_variant__gaze_far_sky_shared_03

- 発話: あの雲の形、おもしろいね。ドギドにも見える？
- 正解: `look_direction`
- 予測: `enemy_information`（89.0%）

## dev：19 / 46件

### live__short_runtime_014

- 発話: ゾンビ出たら泣いちゃう？
- 正解: `not_target`
- 予測: `enemy_information`（99.8%）

### live__short_runtime_033

- 発話: ドギドー何してるの?
- 正解: `not_target`
- 予測: `enemy_information`（45.6%）

### live__short_runtime_040

- 発話: こっちにおいで
- 正解: `dogido_recall`
- 予測: `look_relative_right`（52.7%）

### live__short_runtime_041

- 発話: どぎど私の隣においで
- 正解: `dogido_recall`
- 予測: `enemy_information`（85.7%）

### live__short_runtime_045

- 発話: こっちで一緒にしようよ
- 正解: `dogido_recall`
- 予測: `look_player`（86.2%）

### contrast__contrast_011

- 発話: ドギド、あっちのクリーパーのほうをみて！
- 正解: `look_direction`
- 予測: `enemy_information`（67.7%）

### contrast__contrast_037

- 発話: 頭上の明るいところ、見上げてみて。
- 正解: `look_direction`
- 予測: `equip_sword`（96.9%）

### contrast__contrast_038

- 発話: 後ろを振り向いて、私が見てる方を見て。
- 正解: `look_direction`
- 予測: `look_relative_back_right`（49.0%）

### contrast__contrast_040

- 発話: 私はさっき後ろを振り向いたよ。
- 正解: `not_target`
- 予測: `look_relative_back_right`（99.6%）

### contrast__contrast_042

- 発話: この松明を壁に設置してくれる？
- 正解: `not_target`
- 予測: `look_relative_back_right`（42.5%）

### contrast__contrast_044

- 発話: ラクダを呼ぶつもりだったけど、今日は歩くことにした。
- 正解: `not_target`
- 予測: `recall_mount`（99.9%）

### gaze_variant__gaze_direction_07_front

- 発話: 正面を確認して！
- 正解: `look_relative_front`
- 予測: `look_relative_right`（93.5%）

### gaze_variant__gaze_direction_07_back

- 発話: 後ろを確認して！
- 正解: `look_relative_back`
- 予測: `look_relative_back_right`（55.5%）

### gaze_variant__gaze_direction_07_left

- 発話: 左手を確認して！
- 正解: `look_relative_left`
- 予測: `look_relative_front_left`（46.3%）

### gaze_variant__gaze_direction_07_front_right

- 発話: 右斜め前を確認して！
- 正解: `look_relative_front_right`
- 予測: `look_relative_right`（43.3%）

### gaze_variant__gaze_player_03_variant_02

- 発話: ねえねえ、私を見てよー。
- 正解: `look_player`
- 予測: `enemy_information`（70.6%）

### gaze_variant__gaze_far_align_view_01

- 発話: 今わたしが見てる方、ドギドも見てみて。
- 正解: `look_direction`
- 予測: `enemy_information`（91.1%）

### gaze_variant__gaze_far_align_view_02

- 発話: 私と同じ方を向いてみて。遠くにおもしろいものがあるよ。
- 正解: `look_direction`
- 予測: `enemy_information`（52.1%）

### gaze_variant__gaze_far_align_view_03

- 発話: ほら、私が眺めてるあたり。あそこに何か見えるでしょ？
- 正解: `look_direction`
- 予測: `enemy_information`（96.0%）

## train_additions：4 / 47件

### live_20261024_150724

- 発話: 松松がいっぱいだね
- 正解: `not_target`
- 予測: `look_relative_back_right`（93.1%）

### live_20261024_154537

- 発話: いやめっちゃ迷子だよ
- 正解: `not_target`
- 予測: `enemy_information`（98.2%）

### normal_tool_contrast_20261024_026

- 発話: 持ち替えるなら剣かなあ、まだ迷ってる。
- 正解: `not_target`
- 予測: `equip_sword`（51.2%）

### normal_tool_contrast_20261024_030

- 発話: ここ、どっちから来たのか分からなくなったよ。
- 正解: `not_target`
- 予測: `enemy_information`（99.5%）
