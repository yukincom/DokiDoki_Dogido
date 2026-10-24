# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## test：24 / 50件

### legacy__train-011

- 発話: 剣にはもう持ち替えたよ。
- 正解: `not_target`
- 予測: `equip_sword`（60.8%）

### legacy__train-012

- 発話: 馬はさっき自分で連れてきた。
- 正解: `not_target`
- 予測: `look_relative_right`（46.8%）

### legacy__train-013

- 発話: 敵が三体見えたから逃げたよ。
- 正解: `not_target`
- 予測: `enemy_information`（99.9%）

### legacy__train-018

- 発話: 手持ちの剣が壊れちゃった。
- 正解: `not_target`
- 予測: `equip_sword`（99.3%）

### legacy__train-185

- 発話: ドギド、僕のところへ来て。
- 正解: `dogido_recall`
- 予測: `look_player`（80.7%）

### live__short_runtime_005

- 発話: ありがとう
- 正解: `not_target`
- 予測: `look_relative_right`（42.7%）

### live__short_runtime_046

- 発話: ドギドウズを呼んでください
- 正解: `recall_mount`
- 予測: `dogido_recall`（99.0%）

### live__short_runtime_147

- 発話: 大丈夫だよ
- 正解: `not_target`
- 予測: `enemy_information`（50.4%）

### user_addition__user_20261022_008

- 発話: あの溶岩が流れているとこ行ってみようか
- 正解: `look_direction`
- 予測: `enemy_information`（50.0%）

### user_addition__user_20261022_048

- 発話: 一緒に空を見ようよ！
- 正解: `look_direction`
- 予測: `look_relative_front`（46.9%）

### user_addition__user_20261022_064

- 発話: おっ！いいもの見つけた！
- 正解: `look_target`
- 予測: `enemy_information`（92.5%）

### user_addition__user_20261022_066

- 発話: このブロックは何でできているんだろう？
- 正解: `look_target`
- 予測: `enemy_information`（99.8%）

### contrast__contrast_030

- 発話: 私が立っているところへ、もう少し近寄ってもらえる？
- 正解: `dogido_recall`
- 予測: `look_player`（63.6%）

### contrast__contrast_032

- 発話: 私はもう少し近くまで行ってみるね。
- 正解: `not_target`
- 予測: `look_relative_right`（96.6%）

### contrast__contrast_046

- 発話: 右は向かなくていいから、私のそばに来て。
- 正解: `dogido_recall`
- 予測: `look_relative_right`（100.0%）

### contrast__contrast_048

- 発話: 「右を向いて」と書かれた看板があったよ。
- 正解: `not_target`
- 予測: `look_relative_right`（100.0%）

### gaze_variant__gaze_direction_10_front

- 発話: 何かの気配を前から感じるような気がする
- 正解: `look_relative_front`
- 予測: `enemy_information`（86.7%）

### gaze_variant__gaze_direction_10_back

- 発話: 何かの気配を後ろから感じるような気がする
- 正解: `look_relative_back`
- 予測: `look_player`（43.4%）

### gaze_variant__gaze_direction_10_left

- 発話: 何かの気配を左から感じるような気がする
- 正解: `look_relative_left`
- 予測: `look_relative_right`（50.3%）

### gaze_variant__gaze_direction_10_front_right

- 発話: 何かの気配を右斜め前から感じるような気がする
- 正解: `look_relative_front_right`
- 予測: `look_relative_right`（91.0%）

### gaze_variant__gaze_direction_10_back_left

- 発話: 何かの気配を左斜め後ろから感じるような気がする
- 正解: `look_relative_back_left`
- 予測: `look_relative_right`（33.3%）

### gaze_variant__gaze_far_sky_shared_01

- 発話: ドギド、夕焼けがきれいだよ。一緒に見よう。
- 正解: `look_direction`
- 予測: `look_player`（95.1%）

### gaze_variant__gaze_far_sky_shared_02

- 発話: 今夜は星がよく見えるね。空を見上げてみようよ。
- 正解: `look_direction`
- 予測: `enemy_information`（74.2%）

### gaze_variant__gaze_far_sky_shared_03

- 発話: あの雲の形、おもしろいね。ドギドにも見える？
- 正解: `look_direction`
- 予測: `enemy_information`（83.7%）

## dev：21 / 46件

### live__short_runtime_014

- 発話: ゾンビ出たら泣いちゃう？
- 正解: `not_target`
- 予測: `enemy_information`（95.8%）

### live__short_runtime_033

- 発話: ドギドー何してるの?
- 正解: `not_target`
- 予測: `enemy_information`（66.7%）

### live__short_runtime_040

- 発話: こっちにおいで
- 正解: `dogido_recall`
- 予測: `look_player`（94.0%）

### live__short_runtime_041

- 発話: どぎど私の隣においで
- 正解: `dogido_recall`
- 予測: `look_relative_right`（95.8%）

### live__short_runtime_045

- 発話: こっちで一緒にしようよ
- 正解: `dogido_recall`
- 予測: `look_relative_right`（78.7%）

### live__short_runtime_059

- 発話: 今日の冒険は終了しましょうか
- 正解: `not_target`
- 予測: `look_relative_right`（70.2%）

### user_addition__user_20261022_063

- 発話: わー！なんか変なものがあるぞー！これはなんだろう？
- 正解: `look_target`
- 予測: `enemy_information`（54.0%）

### contrast__contrast_011

- 発話: ドギド、あっちのクリーパーのほうをみて！
- 正解: `look_direction`
- 予測: `look_relative_right`（61.6%）

### contrast__contrast_037

- 発話: 頭上の明るいところ、見上げてみて。
- 正解: `look_direction`
- 予測: `equip_sword`（92.4%）

### contrast__contrast_038

- 発話: 後ろを振り向いて、私が見てる方を見て。
- 正解: `look_direction`
- 予測: `look_player`（91.7%）

### contrast__contrast_040

- 発話: 私はさっき後ろを振り向いたよ。
- 正解: `not_target`
- 予測: `look_relative_back_right`（39.4%）

### contrast__contrast_042

- 発話: この松明を壁に設置してくれる？
- 正解: `not_target`
- 予測: `equip_sword`（96.7%）

### contrast__contrast_044

- 発話: ラクダを呼ぶつもりだったけど、今日は歩くことにした。
- 正解: `not_target`
- 予測: `enemy_information`（51.7%）

### gaze_variant__gaze_direction_07_front

- 発話: 正面を確認して！
- 正解: `look_relative_front`
- 予測: `look_relative_right`（99.0%）

### gaze_variant__gaze_direction_07_back

- 発話: 後ろを確認して！
- 正解: `look_relative_back`
- 予測: `look_relative_back_right`（38.5%）

### gaze_variant__gaze_direction_07_left

- 発話: 左手を確認して！
- 正解: `look_relative_left`
- 予測: `look_relative_right`（67.1%）

### gaze_variant__gaze_direction_07_front_right

- 発話: 右斜め前を確認して！
- 正解: `look_relative_front_right`
- 予測: `look_relative_right`（65.5%）

### gaze_variant__gaze_direction_07_back_left

- 発話: 左斜め後ろを確認して！
- 正解: `look_relative_back_left`
- 予測: `look_relative_back_right`（42.9%）

### gaze_variant__gaze_far_align_view_01

- 発話: 今わたしが見てる方、ドギドも見てみて。
- 正解: `look_direction`
- 予測: `look_player`（66.1%）

### gaze_variant__gaze_far_align_view_02

- 発話: 私と同じ方を向いてみて。遠くにおもしろいものがあるよ。
- 正解: `look_direction`
- 予測: `look_player`（88.5%）

### gaze_variant__gaze_far_align_view_03

- 発話: ほら、私が眺めてるあたり。あそこに何か見えるでしょ？
- 正解: `look_direction`
- 予測: `look_player`（87.5%）

## train_additions：35 / 47件

### live_20261024_143335

- 発話: ここで終わりだ
- 正解: `not_target`
- 予測: `look_relative_right`（94.8%）

### live_20261024_143624

- 発話: たいまついっぱいつけたからね
- 正解: `not_target`
- 予測: `look_relative_right`（80.8%）

### live_20261024_143751

- 発話: いや私はちょっとだからこと近いんじゃないの?
- 正解: `not_target`
- 予測: `look_relative_back_right`（55.9%）

### live_20261024_143828

- 発話: 行き当たりだ
- 正解: `not_target`
- 予測: `equip_sword`（33.9%）

### live_20261024_150658

- 発話: 松明つってどうなんだろう
- 正解: `not_target`
- 予測: `enemy_information`（34.8%）

### live_20261024_150724

- 発話: 松松がいっぱいだね
- 正解: `not_target`
- 予測: `look_relative_back_right`（83.5%）

### live_20261024_151144

- 発話: よしよしされてうれしかったの?
- 正解: `not_target`
- 予測: `look_relative_right`（92.6%）

### live_20261024_151249

- 発話: ちょっと文字数が多いので
- 正解: `not_target`
- 予測: `look_relative_right`（68.3%）

### live_20261024_154838

- 発話: とりあえず明かりをつけたぞ
- 正解: `not_target`
- 予測: `look_relative_back_right`（64.0%）

### live_20261024_154537

- 発話: いやめっちゃ迷子だよ
- 正解: `not_target`
- 予測: `enemy_information`（97.1%）

### live_20261024_154449

- 発話: じゃあつる橋のことはどう思う?
- 正解: `not_target`
- 予測: `equip_sword`（36.8%）

### live_20261024_154433

- 発話: ドギドは小野のことどう思う?
- 正解: `not_target`
- 予測: `look_relative_right`（76.3%）

### live_20261024_154747

- 発話: シャベルじゃない?クワは高安道具だよね。
- 正解: `not_target`
- 予測: `look_relative_right`（74.0%）

### normal_tool_contrast_20261024_003

- 発話: ドギドは松明の明かり、好き？
- 正解: `not_target`
- 予測: `look_relative_back_right`（87.6%）

### normal_tool_contrast_20261024_004

- 発話: 松明って、何であんなに明るいんだろう。
- 正解: `not_target`
- 予測: `look_relative_back_right`（85.9%）

### normal_tool_contrast_20261024_006

- 発話: ドギドはつるはしの形、どう思う？
- 正解: `not_target`
- 予測: `enemy_information`（52.1%）

### normal_tool_contrast_20261024_007

- 発話: さっきはつるはしでずっと石を掘ってたんだ。
- 正解: `not_target`
- 予測: `enemy_information`（95.5%）

### normal_tool_contrast_20261024_008

- 発話: 石のつるはしと鉄のつるはしって、何が違うの？
- 正解: `not_target`
- 予測: `look_relative_right`（59.6%）

### normal_tool_contrast_20261024_010

- 発話: 斧で切ったら、木がいっぱい集まったよ。
- 正解: `not_target`
- 予測: `look_relative_right`（98.4%）

### normal_tool_contrast_20261024_011

- 発話: ドギドは斧とつるはし、見た目ならどっちが好き？
- 正解: `not_target`
- 予測: `look_relative_right`（72.8%）

### normal_tool_contrast_20261024_012

- 発話: 斧の使い道を教えてくれる？
- 正解: `not_target`
- 予測: `look_relative_right`（87.7%）

### normal_tool_contrast_20261024_015

- 発話: シャベルとクワって、使う場所が違うんだよね？
- 正解: `not_target`
- 予測: `enemy_information`（69.7%）

### normal_tool_contrast_20261024_016

- 発話: 穴を埋め終わったから、シャベルはもうしまったよ。
- 正解: `not_target`
- 予測: `enemy_information`（45.9%）

### normal_tool_contrast_20261024_018

- 発話: 畑を耕す道具って、クワで合ってる？
- 正解: `not_target`
- 予測: `look_relative_right`（52.0%）

### normal_tool_contrast_20261024_019

- 発話: クワを持って畑に行こうと思ってるんだ。
- 正解: `not_target`
- 予測: `look_relative_right`（97.8%）

### normal_tool_contrast_20261024_020

- 発話: このクワ、ずいぶん使い込んだなあ。
- 正解: `not_target`
- 予測: `look_relative_right`（98.6%）

### normal_tool_contrast_20261024_022

- 発話: ドギドは剣の見た目をどう思う？
- 正解: `not_target`
- 予測: `look_relative_right`（74.2%）

### normal_tool_contrast_20261024_023

- 発話: この剣、ちょっと長く感じるな。
- 正解: `not_target`
- 予測: `look_relative_right`（99.2%）

### normal_tool_contrast_20261024_024

- 発話: 剣と斧の違いについて聞きたいんだ。
- 正解: `not_target`
- 予測: `look_relative_right`（95.6%）

### normal_tool_contrast_20261024_026

- 発話: 持ち替えるなら剣かなあ、まだ迷ってる。
- 正解: `not_target`
- 予測: `equip_sword`（69.0%）

### normal_tool_contrast_20261024_027

- 発話: 手には今シャベルを持ってるよ。
- 正解: `not_target`
- 予測: `equip_sword`（50.1%）

### normal_tool_contrast_20261024_028

- 発話: 手持ちの道具の話をしてるんだよ。
- 正解: `not_target`
- 予測: `look_relative_back_right`（64.0%）

### normal_tool_contrast_20261024_030

- 発話: ここ、どっちから来たのか分からなくなったよ。
- 正解: `not_target`
- 予測: `enemy_information`（92.7%）

### normal_tool_contrast_20261024_031

- 発話: 明かりが増えたから歩きやすくなったね。
- 正解: `not_target`
- 予測: `enemy_information`（64.3%）

### normal_tool_contrast_20261024_032

- 発話: 道具をいろいろ持ってきてよかった。
- 正解: `not_target`
- 予測: `look_relative_right`（95.9%）
