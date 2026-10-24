# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## test：23 / 50件

### legacy__train-012

- 発話: 馬はさっき自分で連れてきた。
- 正解: `not_target`
- 予測: `recall_mount`（85.4%）

### legacy__train-090

- 発話: つるぎ装備、お願い。
- 正解: `equip_sword`
- 予測: `not_target`（54.4%）

### legacy__train-185

- 発話: ドギド、僕のところへ来て。
- 正解: `dogido_recall`
- 予測: `not_target`（66.4%）

### legacy__train-205

- 発話: 今は替えなくていい。それより敵の数を教えて。
- 正解: `enemy_information`
- 予測: `not_target`（99.9%）

### user_addition__user_20261022_048

- 発話: 一緒に空を見ようよ！
- 正解: `look_direction`
- 予測: `not_target`（99.9%）

### user_addition__user_20261022_058

- 発話: 何かの気配を右から感じるような気がする
- 正解: `look_relative_right`
- 予測: `not_target`（89.4%）

### user_addition__user_20261022_064

- 発話: おっ！いいもの見つけた！
- 正解: `look_target`
- 予測: `not_target`（100.0%）

### user_addition__user_20261022_066

- 発話: このブロックは何でできているんだろう？
- 正解: `look_target`
- 予測: `not_target`（100.0%）

### user_addition__user_20261022_072

- 発話: ドギド！こっちこっち！
- 正解: `look_player`
- 予測: `not_target`（100.0%）

### contrast__contrast_029

- 発話: 顔だけこちらへ向けてもらえるかな。
- 正解: `look_player`
- 予測: `equip_sword`（100.0%）

### contrast__contrast_030

- 発話: 私が立っているところへ、もう少し近寄ってもらえる？
- 正解: `dogido_recall`
- 予測: `equip_sword`（83.0%）

### gaze_variant__gaze_direction_10_front

- 発話: 何かの気配を前から感じるような気がする
- 正解: `look_relative_front`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_direction_10_back

- 発話: 何かの気配を後ろから感じるような気がする
- 正解: `look_relative_back`
- 予測: `not_target`（97.3%）

### gaze_variant__gaze_direction_10_left

- 発話: 何かの気配を左から感じるような気がする
- 正解: `look_relative_left`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_direction_10_front_left

- 発話: 何かの気配を左斜め前から感じるような気がする
- 正解: `look_relative_front_left`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_direction_10_front_right

- 発話: 何かの気配を右斜め前から感じるような気がする
- 正解: `look_relative_front_right`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_direction_10_back_left

- 発話: 何かの気配を左斜め後ろから感じるような気がする
- 正解: `look_relative_back_left`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_direction_10_back_right

- 発話: 何かの気配を右斜め後ろから感じるような気がする
- 正解: `look_relative_back_right`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_player_04_variant_01

- 発話: ドギド、ここ！私の方だよ！
- 正解: `look_player`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_player_04_variant_02

- 発話: おーいドギド、こっち見てる？
- 正解: `look_player`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_far_sky_shared_01

- 発話: ドギド、夕焼けがきれいだよ。一緒に見よう。
- 正解: `look_direction`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_far_sky_shared_02

- 発話: 今夜は星がよく見えるね。空を見上げてみようよ。
- 正解: `look_direction`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_far_sky_shared_03

- 発話: あの雲の形、おもしろいね。ドギドにも見える？
- 正解: `look_direction`
- 予測: `not_target`（100.0%）

## dev：20 / 46件

### live__short_runtime_040

- 発話: こっちにおいで
- 正解: `dogido_recall`
- 予測: `look_relative_right`（99.3%）

### live__short_runtime_045

- 発話: こっちで一緒にしようよ
- 正解: `dogido_recall`
- 予測: `not_target`（100.0%）

### live__short_runtime_243

- 発話: みの人ゾンビはどこだ?
- 正解: `enemy_information`
- 予測: `not_target`（99.6%）

### user_addition__user_20261022_063

- 発話: わー！なんか変なものがあるぞー！これはなんだろう？
- 正解: `look_target`
- 予測: `not_target`（90.4%）

### user_addition__user_20261022_067

- 発話: こいつはどういうものだ？
- 正解: `look_target`
- 予測: `not_target`（100.0%）

### user_addition__user_20261022_071

- 発話: こっちみて〜
- 正解: `look_player`
- 予測: `not_target`（99.9%）

### contrast__contrast_011

- 発話: ドギド、あっちのクリーパーのほうをみて！
- 正解: `look_direction`
- 予測: `enemy_information`（100.0%）

### contrast__contrast_037

- 発話: 頭上の明るいところ、見上げてみて。
- 正解: `look_direction`
- 予測: `look_relative_right`（99.9%）

### contrast__contrast_038

- 発話: 後ろを振り向いて、私が見てる方を見て。
- 正解: `look_direction`
- 予測: `look_relative_right`（98.0%）

### contrast__contrast_042

- 発話: この松明を壁に設置してくれる？
- 正解: `not_target`
- 予測: `equip_sword`（100.0%）

### gaze_variant__gaze_direction_07_front

- 発話: 正面を確認して！
- 正解: `look_relative_front`
- 予測: `look_relative_right`（99.0%）

### gaze_variant__gaze_direction_07_back

- 発話: 後ろを確認して！
- 正解: `look_relative_back`
- 予測: `look_relative_right`（48.3%）

### gaze_variant__gaze_direction_07_front_left

- 発話: 左斜め前を確認して！
- 正解: `look_relative_front_left`
- 予測: `look_relative_left`（76.8%）

### gaze_variant__gaze_direction_07_front_right

- 発話: 右斜め前を確認して！
- 正解: `look_relative_front_right`
- 予測: `look_relative_right`（99.3%）

### gaze_variant__gaze_direction_07_back_left

- 発話: 左斜め後ろを確認して！
- 正解: `look_relative_back_left`
- 予測: `look_relative_right`（99.0%）

### gaze_variant__gaze_direction_07_back_right

- 発話: 右斜め後ろを確認して！
- 正解: `look_relative_back_right`
- 予測: `look_relative_right`（100.0%）

### gaze_variant__gaze_player_03_variant_01

- 発話: ちょっとだけ私の方を見てもらえる？
- 正解: `look_player`
- 予測: `look_relative_left`（100.0%）

### gaze_variant__gaze_player_03_variant_02

- 発話: ねえねえ、私を見てよー。
- 正解: `look_player`
- 予測: `not_target`（100.0%）

### gaze_variant__gaze_far_align_view_01

- 発話: 今わたしが見てる方、ドギドも見てみて。
- 正解: `look_direction`
- 予測: `not_target`（99.9%）

### gaze_variant__gaze_far_align_view_02

- 発話: 私と同じ方を向いてみて。遠くにおもしろいものがあるよ。
- 正解: `look_direction`
- 予測: `not_target`（100.0%）
