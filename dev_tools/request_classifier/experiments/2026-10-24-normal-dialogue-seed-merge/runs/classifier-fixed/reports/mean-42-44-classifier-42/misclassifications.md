# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## test：32 / 50件

### legacy__train-011

- 発話: 剣にはもう持ち替えたよ。
- 正解: `not_target`
- 予測: `equip_sword`（69.8%）

### legacy__train-012

- 発話: 馬はさっき自分で連れてきた。
- 正解: `not_target`
- 予測: `enemy_information`（92.5%）

### legacy__train-013

- 発話: 敵が三体見えたから逃げたよ。
- 正解: `not_target`
- 予測: `enemy_information`（100.0%）

### legacy__train-018

- 発話: 手持ちの剣が壊れちゃった。
- 正解: `not_target`
- 予測: `enemy_information`（87.1%）

### legacy__train-083

- 発話: ダイヤの剣を装備して。
- 正解: `equip_sword`
- 予測: `recall_mount`（75.0%）

### legacy__train-085

- 発話: ドギド、武器をソードに変更して。
- 正解: `equip_sword`
- 予測: `enemy_information`（98.9%）

### legacy__train-089

- 発話: ホットバーの剣を持って。
- 正解: `equip_sword`
- 予測: `recall_mount`（96.9%）

### legacy__train-090

- 発話: つるぎ装備、お願い。
- 正解: `equip_sword`
- 予測: `enemy_information`（98.6%）

### legacy__train-184

- 発話: 飼っている猫を呼んで。
- 正解: `recall_mount`
- 予測: `enemy_information`（84.6%）

### legacy__train-526

- 発話: 『馬を呼べ』って台詞を練習してた。今頼みたいのは剣への持ち替えだよ。
- 正解: `equip_sword`
- 予測: `enemy_information`（47.7%）

### live__short_runtime_005

- 発話: ありがとう
- 正解: `not_target`
- 予測: `enemy_information`（85.9%）

### live__short_runtime_147

- 発話: 大丈夫だよ
- 正解: `not_target`
- 予測: `enemy_information`（96.6%）

### user_addition__user_20261022_008

- 発話: あの溶岩が流れているとこ行ってみようか
- 正解: `look_direction`
- 予測: `enemy_information`（96.6%）

### user_addition__user_20261022_048

- 発話: 一緒に空を見ようよ！
- 正解: `look_direction`
- 予測: `enemy_information`（87.3%）

### user_addition__user_20261022_058

- 発話: 何かの気配を右から感じるような気がする
- 正解: `look_relative_right`
- 予測: `enemy_information`（67.6%）

### user_addition__user_20261022_064

- 発話: おっ！いいもの見つけた！
- 正解: `look_target`
- 予測: `enemy_information`（99.3%）

### user_addition__user_20261022_066

- 発話: このブロックは何でできているんだろう？
- 正解: `look_target`
- 予測: `enemy_information`（98.6%）

### contrast__contrast_029

- 発話: 顔だけこちらへ向けてもらえるかな。
- 正解: `look_player`
- 予測: `enemy_information`（99.1%）

### contrast__contrast_030

- 発話: 私が立っているところへ、もう少し近寄ってもらえる？
- 正解: `dogido_recall`
- 予測: `enemy_information`（99.0%）

### contrast__contrast_032

- 発話: 私はもう少し近くまで行ってみるね。
- 正解: `not_target`
- 予測: `enemy_information`（96.1%）

### contrast__contrast_045

- 発話: こっちに来なくていいから、その場で右を向いて。
- 正解: `look_relative_right`
- 予測: `look_player`（53.8%）

### contrast__contrast_048

- 発話: 「右を向いて」と書かれた看板があったよ。
- 正解: `not_target`
- 予測: `enemy_information`（56.1%）

### gaze_variant__gaze_direction_10_front

- 発話: 何かの気配を前から感じるような気がする
- 正解: `look_relative_front`
- 予測: `enemy_information`（100.0%）

### gaze_variant__gaze_direction_10_back

- 発話: 何かの気配を後ろから感じるような気がする
- 正解: `look_relative_back`
- 予測: `enemy_information`（100.0%）

### gaze_variant__gaze_direction_10_left

- 発話: 何かの気配を左から感じるような気がする
- 正解: `look_relative_left`
- 予測: `enemy_information`（99.9%）

### gaze_variant__gaze_direction_10_front_left

- 発話: 何かの気配を左斜め前から感じるような気がする
- 正解: `look_relative_front_left`
- 予測: `enemy_information`（93.7%）

### gaze_variant__gaze_direction_10_front_right

- 発話: 何かの気配を右斜め前から感じるような気がする
- 正解: `look_relative_front_right`
- 予測: `enemy_information`（88.9%）

### gaze_variant__gaze_direction_10_back_right

- 発話: 何かの気配を右斜め後ろから感じるような気がする
- 正解: `look_relative_back_right`
- 予測: `enemy_information`（99.0%）

### gaze_variant__gaze_player_04_variant_01

- 発話: ドギド、ここ！私の方だよ！
- 正解: `look_player`
- 予測: `enemy_information`（96.2%）

### gaze_variant__gaze_far_sky_shared_01

- 発話: ドギド、夕焼けがきれいだよ。一緒に見よう。
- 正解: `look_direction`
- 予測: `enemy_information`（69.8%）

### gaze_variant__gaze_far_sky_shared_02

- 発話: 今夜は星がよく見えるね。空を見上げてみようよ。
- 正解: `look_direction`
- 予測: `enemy_information`（99.4%）

### gaze_variant__gaze_far_sky_shared_03

- 発話: あの雲の形、おもしろいね。ドギドにも見える？
- 正解: `look_direction`
- 予測: `enemy_information`（95.2%）

## dev：24 / 46件

### legacy__train-105

- 発話: 馬を呼ばなくていい、今は剣を装備して。
- 正解: `equip_sword`
- 予測: `enemy_information`（70.6%）

### legacy__train-107

- 発話: 木の剣じゃなくて鉄の剣を持って。
- 正解: `equip_sword`
- 予測: `recall_mount`（93.0%）

### live__short_runtime_014

- 発話: ゾンビ出たら泣いちゃう？
- 正解: `not_target`
- 予測: `enemy_information`（100.0%）

### live__short_runtime_033

- 発話: ドギドー何してるの?
- 正解: `not_target`
- 予測: `enemy_information`（96.5%）

### live__short_runtime_059

- 発話: 今日の冒険は終了しましょうか
- 正解: `not_target`
- 予測: `enemy_information`（95.5%）

### user_addition__user_20261022_063

- 発話: わー！なんか変なものがあるぞー！これはなんだろう？
- 正解: `look_target`
- 予測: `enemy_information`（97.1%）

### user_addition__user_20261022_067

- 発話: こいつはどういうものだ？
- 正解: `look_target`
- 予測: `enemy_information`（99.1%）

### contrast__contrast_011

- 発話: ドギド、あっちのクリーパーのほうをみて！
- 正解: `look_direction`
- 予測: `enemy_information`（98.7%）

### contrast__contrast_037

- 発話: 頭上の明るいところ、見上げてみて。
- 正解: `look_direction`
- 予測: `enemy_information`（100.0%）

### contrast__contrast_038

- 発話: 後ろを振り向いて、私が見てる方を見て。
- 正解: `look_direction`
- 予測: `enemy_information`（92.4%）

### contrast__contrast_040

- 発話: 私はさっき後ろを振り向いたよ。
- 正解: `not_target`
- 予測: `enemy_information`（99.1%）

### contrast__contrast_041

- 発話: 松明を持つのはやめて、鉄の剣へ持ち替えて。
- 正解: `equip_sword`
- 予測: `recall_mount`（94.6%）

### contrast__contrast_042

- 発話: この松明を壁に設置してくれる？
- 正解: `not_target`
- 予測: `enemy_information`（98.5%）

### contrast__contrast_044

- 発話: ラクダを呼ぶつもりだったけど、今日は歩くことにした。
- 正解: `not_target`
- 予測: `recall_mount`（99.9%）

### gaze_variant__gaze_direction_07_front

- 発話: 正面を確認して！
- 正解: `look_relative_front`
- 予測: `enemy_information`（100.0%）

### gaze_variant__gaze_direction_07_back

- 発話: 後ろを確認して！
- 正解: `look_relative_back`
- 予測: `dogido_recall`（66.7%）

### gaze_variant__gaze_direction_07_left

- 発話: 左手を確認して！
- 正解: `look_relative_left`
- 予測: `look_relative_back_right`（72.8%）

### gaze_variant__gaze_direction_07_front_left

- 発話: 左斜め前を確認して！
- 正解: `look_relative_front_left`
- 予測: `enemy_information`（49.7%）

### gaze_variant__gaze_direction_07_back_right

- 発話: 右斜め後ろを確認して！
- 正解: `look_relative_back_right`
- 予測: `enemy_information`（87.5%）

### gaze_variant__gaze_player_03_variant_01

- 発話: ちょっとだけ私の方を見てもらえる？
- 正解: `look_player`
- 予測: `enemy_information`（62.9%）

### gaze_variant__gaze_player_03_variant_02

- 発話: ねえねえ、私を見てよー。
- 正解: `look_player`
- 予測: `enemy_information`（63.3%）

### gaze_variant__gaze_far_align_view_01

- 発話: 今わたしが見てる方、ドギドも見てみて。
- 正解: `look_direction`
- 予測: `enemy_information`（72.6%）

### gaze_variant__gaze_far_align_view_02

- 発話: 私と同じ方を向いてみて。遠くにおもしろいものがあるよ。
- 正解: `look_direction`
- 予測: `enemy_information`（98.2%）

### gaze_variant__gaze_far_align_view_03

- 発話: ほら、私が眺めてるあたり。あそこに何か見えるでしょ？
- 正解: `look_direction`
- 予測: `enemy_information`（97.6%）

## train_additions：38 / 47件

### live_20261024_143335

- 発話: ここで終わりだ
- 正解: `not_target`
- 予測: `enemy_information`（97.0%）

### live_20261024_143618

- 発話: さらに地下に戻ってきました
- 正解: `not_target`
- 予測: `enemy_information`（97.5%）

### live_20261024_143624

- 発話: たいまついっぱいつけたからね
- 正解: `not_target`
- 予測: `enemy_information`（99.7%）

### live_20261024_143751

- 発話: いや私はちょっとだからこと近いんじゃないの?
- 正解: `not_target`
- 予測: `enemy_information`（98.8%）

### live_20261024_143828

- 発話: 行き当たりだ
- 正解: `not_target`
- 予測: `enemy_information`（100.0%）

### live_20261024_150658

- 発話: 松明つってどうなんだろう
- 正解: `not_target`
- 予測: `enemy_information`（95.9%）

### live_20261024_150724

- 発話: 松松がいっぱいだね
- 正解: `not_target`
- 予測: `enemy_information`（91.2%）

### live_20261024_151144

- 発話: よしよしされてうれしかったの?
- 正解: `not_target`
- 予測: `enemy_information`（59.2%）

### live_20261024_151249

- 発話: ちょっと文字数が多いので
- 正解: `not_target`
- 予測: `enemy_information`（99.9%）

### live_20261024_154838

- 発話: とりあえず明かりをつけたぞ
- 正解: `not_target`
- 予測: `enemy_information`（100.0%）

### live_20261024_154537

- 発話: いやめっちゃ迷子だよ
- 正解: `not_target`
- 予測: `enemy_information`（98.2%）

### live_20261024_154449

- 発話: じゃあつる橋のことはどう思う?
- 正解: `not_target`
- 予測: `enemy_information`（90.8%）

### live_20261024_154433

- 発話: ドギドは小野のことどう思う?
- 正解: `not_target`
- 予測: `enemy_information`（41.2%）

### live_20261024_154747

- 発話: シャベルじゃない?クワは高安道具だよね。
- 正解: `not_target`
- 予測: `enemy_information`（84.4%）

### normal_tool_contrast_20261024_002

- 発話: たいまつを増やしてから、ずいぶん明るくなったね。
- 正解: `not_target`
- 予測: `enemy_information`（98.7%）

### normal_tool_contrast_20261024_003

- 発話: ドギドは松明の明かり、好き？
- 正解: `not_target`
- 予測: `enemy_information`（49.2%）

### normal_tool_contrast_20261024_004

- 発話: 松明って、何であんなに明るいんだろう。
- 正解: `not_target`
- 予測: `enemy_information`（96.1%）

### normal_tool_contrast_20261024_006

- 発話: ドギドはつるはしの形、どう思う？
- 正解: `not_target`
- 予測: `enemy_information`（37.0%）

### normal_tool_contrast_20261024_007

- 発話: さっきはつるはしでずっと石を掘ってたんだ。
- 正解: `not_target`
- 予測: `enemy_information`（92.5%）

### normal_tool_contrast_20261024_008

- 発話: 石のつるはしと鉄のつるはしって、何が違うの？
- 正解: `not_target`
- 予測: `enemy_information`（83.8%）

### normal_tool_contrast_20261024_010

- 発話: 斧で切ったら、木がいっぱい集まったよ。
- 正解: `not_target`
- 予測: `enemy_information`（37.1%）

### normal_tool_contrast_20261024_011

- 発話: ドギドは斧とつるはし、見た目ならどっちが好き？
- 正解: `not_target`
- 予測: `enemy_information`（93.2%）

### normal_tool_contrast_20261024_012

- 発話: 斧の使い道を教えてくれる？
- 正解: `not_target`
- 予測: `enemy_information`（95.0%）

### normal_tool_contrast_20261024_014

- 発話: シャベルの先って平べったいね。
- 正解: `not_target`
- 予測: `enemy_information`（90.6%）

### normal_tool_contrast_20261024_015

- 発話: シャベルとクワって、使う場所が違うんだよね？
- 正解: `not_target`
- 予測: `enemy_information`（97.4%）

### normal_tool_contrast_20261024_016

- 発話: 穴を埋め終わったから、シャベルはもうしまったよ。
- 正解: `not_target`
- 予測: `enemy_information`（99.5%）

### normal_tool_contrast_20261024_018

- 発話: 畑を耕す道具って、クワで合ってる？
- 正解: `not_target`
- 予測: `enemy_information`（78.3%）

### normal_tool_contrast_20261024_019

- 発話: クワを持って畑に行こうと思ってるんだ。
- 正解: `not_target`
- 予測: `enemy_information`（89.8%）

### normal_tool_contrast_20261024_020

- 発話: このクワ、ずいぶん使い込んだなあ。
- 正解: `not_target`
- 予測: `enemy_information`（68.3%）

### normal_tool_contrast_20261024_022

- 発話: ドギドは剣の見た目をどう思う？
- 正解: `not_target`
- 予測: `enemy_information`（87.2%）

### normal_tool_contrast_20261024_023

- 発話: この剣、ちょっと長く感じるな。
- 正解: `not_target`
- 予測: `enemy_information`（96.0%）

### normal_tool_contrast_20261024_024

- 発話: 剣と斧の違いについて聞きたいんだ。
- 正解: `not_target`
- 予測: `enemy_information`（92.9%）

### normal_tool_contrast_20261024_026

- 発話: 持ち替えるなら剣かなあ、まだ迷ってる。
- 正解: `not_target`
- 予測: `enemy_information`（76.1%）

### normal_tool_contrast_20261024_027

- 発話: 手には今シャベルを持ってるよ。
- 正解: `not_target`
- 予測: `enemy_information`（84.6%）

### normal_tool_contrast_20261024_028

- 発話: 手持ちの道具の話をしてるんだよ。
- 正解: `not_target`
- 予測: `enemy_information`（90.4%）

### normal_tool_contrast_20261024_030

- 発話: ここ、どっちから来たのか分からなくなったよ。
- 正解: `not_target`
- 予測: `enemy_information`（99.7%）

### normal_tool_contrast_20261024_031

- 発話: 明かりが増えたから歩きやすくなったね。
- 正解: `not_target`
- 予測: `enemy_information`（99.9%）

### normal_tool_contrast_20261024_032

- 発話: 道具をいろいろ持ってきてよかった。
- 正解: `not_target`
- 予測: `look_relative_right`（85.5%）
