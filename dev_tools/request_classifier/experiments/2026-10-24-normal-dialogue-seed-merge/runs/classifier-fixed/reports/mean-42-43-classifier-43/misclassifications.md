# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## test：38 / 50件

### legacy__train-012

- 発話: 馬はさっき自分で連れてきた。
- 正解: `not_target`
- 予測: `look_player`（99.4%）

### legacy__train-013

- 発話: 敵が三体見えたから逃げたよ。
- 正解: `not_target`
- 予測: `enemy_information`（79.5%）

### legacy__train-018

- 発話: 手持ちの剣が壊れちゃった。
- 正解: `not_target`
- 予測: `look_player`（96.4%）

### legacy__train-062

- 発話: 近くのモンスターを数えて。
- 正解: `enemy_information`
- 予測: `look_player`（83.2%）

### legacy__train-082

- 発話: 手持ちを剣にして。
- 正解: `equip_sword`
- 予測: `look_player`（91.5%）

### legacy__train-083

- 発話: ダイヤの剣を装備して。
- 正解: `equip_sword`
- 予測: `look_player`（99.4%）

### legacy__train-085

- 発話: ドギド、武器をソードに変更して。
- 正解: `equip_sword`
- 予測: `look_player`（98.1%）

### legacy__train-089

- 発話: ホットバーの剣を持って。
- 正解: `equip_sword`
- 予測: `look_player`（94.6%）

### legacy__train-090

- 発話: つるぎ装備、お願い。
- 正解: `equip_sword`
- 予測: `look_player`（99.6%）

### legacy__train-141

- 発話: ミルクって名前の白い馬を呼んで。
- 正解: `recall_mount`
- 予測: `look_player`（90.0%）

### legacy__train-144

- 発話: 青いハーネスのハッピーガストをこっちへ。
- 正解: `recall_mount`
- 予測: `look_player`（65.6%）

### legacy__train-184

- 発話: 飼っている猫を呼んで。
- 正解: `recall_mount`
- 予測: `look_player`（99.2%）

### legacy__train-185

- 発話: ドギド、僕のところへ来て。
- 正解: `dogido_recall`
- 予測: `look_player`（100.0%）

### legacy__train-205

- 発話: 今は替えなくていい。それより敵の数を教えて。
- 正解: `enemy_information`
- 予測: `look_player`（97.2%）

### legacy__train-525

- 発話: 『敵はどこだ』って台詞が好きなんだ。ところで、今はロバを呼んでくれる？
- 正解: `recall_mount`
- 予測: `look_player`（99.3%）

### legacy__train-526

- 発話: 『馬を呼べ』って台詞を練習してた。今頼みたいのは剣への持ち替えだよ。
- 正解: `equip_sword`
- 予測: `look_player`（99.2%）

### legacy__train-736

- 発話: ゾンビホース、こっちに呼んでくれへん？
- 正解: `recall_mount`
- 予測: `look_player`（99.0%）

### legacy__train-770

- 発話: ゾンビオウムガイを、この舟の横まで呼んで
- 正解: `recall_mount`
- 予測: `look_player`（96.5%）

### live__short_runtime_005

- 発話: ありがとう
- 正解: `not_target`
- 予測: `look_player`（99.4%）

### live__short_runtime_046

- 発話: ドギドウズを呼んでください
- 正解: `recall_mount`
- 予測: `look_player`（100.0%）

### live__short_runtime_147

- 発話: 大丈夫だよ
- 正解: `not_target`
- 予測: `look_player`（93.5%）

### user_addition__user_20261022_008

- 発話: あの溶岩が流れているとこ行ってみようか
- 正解: `look_direction`
- 予測: `look_player`（87.0%）

### user_addition__user_20261022_048

- 発話: 一緒に空を見ようよ！
- 正解: `look_direction`
- 予測: `look_player`（99.8%）

### user_addition__user_20261022_058

- 発話: 何かの気配を右から感じるような気がする
- 正解: `look_relative_right`
- 予測: `look_player`（94.5%）

### user_addition__user_20261022_064

- 発話: おっ！いいもの見つけた！
- 正解: `look_target`
- 予測: `look_player`（99.0%）

### contrast__contrast_030

- 発話: 私が立っているところへ、もう少し近寄ってもらえる？
- 正解: `dogido_recall`
- 予測: `look_player`（100.0%）

### contrast__contrast_032

- 発話: 私はもう少し近くまで行ってみるね。
- 正解: `not_target`
- 予測: `look_player`（99.8%）

### contrast__contrast_045

- 発話: こっちに来なくていいから、その場で右を向いて。
- 正解: `look_relative_right`
- 予測: `look_player`（87.9%）

### contrast__contrast_046

- 発話: 右は向かなくていいから、私のそばに来て。
- 正解: `dogido_recall`
- 予測: `look_player`（99.8%）

### contrast__contrast_048

- 発話: 「右を向いて」と書かれた看板があったよ。
- 正解: `not_target`
- 予測: `look_player`（79.7%）

### gaze_variant__gaze_direction_10_front

- 発話: 何かの気配を前から感じるような気がする
- 正解: `look_relative_front`
- 予測: `look_player`（99.7%）

### gaze_variant__gaze_direction_10_back

- 発話: 何かの気配を後ろから感じるような気がする
- 正解: `look_relative_back`
- 予測: `look_player`（99.5%）

### gaze_variant__gaze_direction_10_left

- 発話: 何かの気配を左から感じるような気がする
- 正解: `look_relative_left`
- 予測: `look_player`（99.4%）

### gaze_variant__gaze_direction_10_front_left

- 発話: 何かの気配を左斜め前から感じるような気がする
- 正解: `look_relative_front_left`
- 予測: `look_player`（86.6%）

### gaze_variant__gaze_direction_10_back_left

- 発話: 何かの気配を左斜め後ろから感じるような気がする
- 正解: `look_relative_back_left`
- 予測: `look_player`（99.1%）

### gaze_variant__gaze_far_sky_shared_01

- 発話: ドギド、夕焼けがきれいだよ。一緒に見よう。
- 正解: `look_direction`
- 予測: `look_player`（99.4%）

### gaze_variant__gaze_far_sky_shared_02

- 発話: 今夜は星がよく見えるね。空を見上げてみようよ。
- 正解: `look_direction`
- 予測: `look_player`（92.6%）

### gaze_variant__gaze_far_sky_shared_03

- 発話: あの雲の形、おもしろいね。ドギドにも見える？
- 正解: `look_direction`
- 予測: `look_player`（75.0%）

## dev：36 / 46件

### legacy__train-042

- 発話: この辺にモンスターはおる？
- 正解: `enemy_information`
- 予測: `look_player`（93.5%）

### legacy__train-045

- 発話: 背後にクリーパーはいそう？
- 正解: `enemy_information`
- 予測: `look_player`（58.0%）

### legacy__train-101

- 発話: 斧じゃなくて剣にして。
- 正解: `equip_sword`
- 予測: `look_player`（96.1%）

### legacy__train-105

- 発話: 馬を呼ばなくていい、今は剣を装備して。
- 正解: `equip_sword`
- 予測: `look_player`（95.1%）

### legacy__train-107

- 発話: 木の剣じゃなくて鉄の剣を持って。
- 正解: `equip_sword`
- 予測: `look_player`（46.2%）

### legacy__train-151

- 発話: 剣は替えなくていいから、馬を呼んで。
- 正解: `recall_mount`
- 予測: `look_player`（93.6%）

### legacy__train-152

- 発話: ロバじゃなくてラバを呼び戻して。
- 正解: `recall_mount`
- 予測: `look_player`（66.2%）

### legacy__train-156

- 発話: 新しい馬は出さなくていい。僕の馬を呼んで。
- 正解: `recall_mount`
- 予測: `look_player`（86.3%）

### legacy__train-160

- 発話: さっきは不要って言ったけど、今はラマを呼び戻して。
- 正解: `recall_mount`
- 予測: `look_player`（98.5%）

### legacy__train-576

- 発話: 敵が出てくる前に備えたいから、今、剣に持ち替えて。
- 正解: `equip_sword`
- 予測: `look_player`（93.4%）

### live__short_runtime_014

- 発話: ゾンビ出たら泣いちゃう？
- 正解: `not_target`
- 予測: `look_player`（99.2%）

### live__short_runtime_033

- 発話: ドギドー何してるの?
- 正解: `not_target`
- 予測: `look_player`（99.7%）

### live__short_runtime_040

- 発話: こっちにおいで
- 正解: `dogido_recall`
- 予測: `look_player`（100.0%）

### live__short_runtime_041

- 発話: どぎど私の隣においで
- 正解: `dogido_recall`
- 予測: `look_player`（100.0%）

### live__short_runtime_045

- 発話: こっちで一緒にしようよ
- 正解: `dogido_recall`
- 予測: `look_player`（100.0%）

### live__short_runtime_059

- 発話: 今日の冒険は終了しましょうか
- 正解: `not_target`
- 予測: `look_player`（96.4%）

### live__short_runtime_243

- 発話: みの人ゾンビはどこだ?
- 正解: `enemy_information`
- 予測: `look_player`（70.9%）

### user_addition__user_20261022_016

- 発話: 村人は何人いる？
- 正解: `enemy_information`
- 予測: `look_player`（73.5%）

### user_addition__user_20261022_055

- 発話: 右手を確認して！
- 正解: `look_relative_right`
- 予測: `look_player`（98.9%）

### user_addition__user_20261022_067

- 発話: こいつはどういうものだ？
- 正解: `look_target`
- 予測: `look_player`（91.3%）

### contrast__contrast_011

- 発話: ドギド、あっちのクリーパーのほうをみて！
- 正解: `look_direction`
- 予測: `look_player`（96.9%）

### contrast__contrast_037

- 発話: 頭上の明るいところ、見上げてみて。
- 正解: `look_direction`
- 予測: `look_player`（66.3%）

### contrast__contrast_038

- 発話: 後ろを振り向いて、私が見てる方を見て。
- 正解: `look_direction`
- 予測: `look_player`（98.6%）

### contrast__contrast_040

- 発話: 私はさっき後ろを振り向いたよ。
- 正解: `not_target`
- 予測: `look_player`（94.6%）

### contrast__contrast_041

- 発話: 松明を持つのはやめて、鉄の剣へ持ち替えて。
- 正解: `equip_sword`
- 予測: `look_player`（77.9%）

### contrast__contrast_042

- 発話: この松明を壁に設置してくれる？
- 正解: `not_target`
- 予測: `look_player`（99.7%）

### contrast__contrast_043

- 発話: 鞍をつけた私のラクダをここまで呼んで。
- 正解: `recall_mount`
- 予測: `look_player`（95.5%）

### contrast__contrast_044

- 発話: ラクダを呼ぶつもりだったけど、今日は歩くことにした。
- 正解: `not_target`
- 予測: `look_player`（99.3%）

### gaze_variant__gaze_direction_07_front

- 発話: 正面を確認して！
- 正解: `look_relative_front`
- 予測: `look_player`（97.9%）

### gaze_variant__gaze_direction_07_back

- 発話: 後ろを確認して！
- 正解: `look_relative_back`
- 予測: `look_player`（63.9%）

### gaze_variant__gaze_direction_07_left

- 発話: 左手を確認して！
- 正解: `look_relative_left`
- 予測: `look_player`（57.6%）

### gaze_variant__gaze_direction_07_front_left

- 発話: 左斜め前を確認して！
- 正解: `look_relative_front_left`
- 予測: `look_player`（95.9%）

### gaze_variant__gaze_direction_07_back_left

- 発話: 左斜め後ろを確認して！
- 正解: `look_relative_back_left`
- 予測: `look_player`（99.2%）

### gaze_variant__gaze_far_align_view_01

- 発話: 今わたしが見てる方、ドギドも見てみて。
- 正解: `look_direction`
- 予測: `look_player`（99.8%）

### gaze_variant__gaze_far_align_view_02

- 発話: 私と同じ方を向いてみて。遠くにおもしろいものがあるよ。
- 正解: `look_direction`
- 予測: `look_player`（99.4%）

### gaze_variant__gaze_far_align_view_03

- 発話: ほら、私が眺めてるあたり。あそこに何か見えるでしょ？
- 正解: `look_direction`
- 予測: `look_player`（92.9%）

## train_additions：28 / 47件

### live_20261024_143751

- 発話: いや私はちょっとだからこと近いんじゃないの?
- 正解: `not_target`
- 予測: `look_player`（97.6%）

### live_20261024_143828

- 発話: 行き当たりだ
- 正解: `not_target`
- 予測: `look_player`（91.5%）

### live_20261024_151144

- 発話: よしよしされてうれしかったの?
- 正解: `not_target`
- 予測: `look_player`（43.8%）

### live_20261024_154838

- 発話: とりあえず明かりをつけたぞ
- 正解: `not_target`
- 予測: `look_target`（81.9%）

### live_20261024_154537

- 発話: いやめっちゃ迷子だよ
- 正解: `not_target`
- 予測: `look_player`（91.6%）

### live_20261024_154433

- 発話: ドギドは小野のことどう思う?
- 正解: `not_target`
- 予測: `look_player`（83.2%）

### live_20261024_154747

- 発話: シャベルじゃない?クワは高安道具だよね。
- 正解: `not_target`
- 予測: `look_player`（51.4%）

### normal_tool_contrast_20261024_001

- 発話: 灯りの準備は済んだから、ドギド、いま私の手元を剣に切り替えて。
- 正解: `equip_sword`
- 予測: `look_player`（99.1%）

### normal_tool_contrast_20261024_003

- 発話: ドギドは松明の明かり、好き？
- 正解: `not_target`
- 予測: `look_player`（94.9%）

### normal_tool_contrast_20261024_006

- 発話: ドギドはつるはしの形、どう思う？
- 正解: `not_target`
- 予測: `look_player`（88.1%）

### normal_tool_contrast_20261024_009

- 発話: 木を切るのはここまで。いま持ってる斧を剣に替えてもらっていい？
- 正解: `equip_sword`
- 予測: `look_player`（98.1%）

### normal_tool_contrast_20261024_011

- 発話: ドギドは斧とつるはし、見た目ならどっちが好き？
- 正解: `not_target`
- 予測: `look_player`（68.5%）

### normal_tool_contrast_20261024_012

- 発話: 斧の使い道を教えてくれる？
- 正解: `not_target`
- 予測: `look_player`（98.4%）

### normal_tool_contrast_20261024_013

- 発話: 土を掘るのをやめるから、シャベルから剣へ切り替えをお願い。
- 正解: `equip_sword`
- 予測: `look_player`（99.0%）

### normal_tool_contrast_20261024_014

- 発話: シャベルの先って平べったいね。
- 正解: `not_target`
- 予測: `look_player`（86.8%）

### normal_tool_contrast_20261024_015

- 発話: シャベルとクワって、使う場所が違うんだよね？
- 正解: `not_target`
- 予測: `look_player`（92.9%）

### normal_tool_contrast_20261024_017

- 発話: 畑仕事は終わり。持ってるクワの代わりに剣を持たせて。
- 正解: `equip_sword`
- 予測: `look_player`（98.8%）

### normal_tool_contrast_20261024_018

- 発話: 畑を耕す道具って、クワで合ってる？
- 正解: `not_target`
- 予測: `look_player`（98.9%）

### normal_tool_contrast_20261024_019

- 発話: クワを持って畑に行こうと思ってるんだ。
- 正解: `not_target`
- 予測: `look_player`（95.6%）

### normal_tool_contrast_20261024_020

- 発話: このクワ、ずいぶん使い込んだなあ。
- 正解: `not_target`
- 予測: `look_player`（61.3%）

### normal_tool_contrast_20261024_021

- 発話: 剣の話をする前に、私の手持ちをその剣へ切り替えてくれるかな。
- 正解: `equip_sword`
- 予測: `look_player`（97.2%）

### normal_tool_contrast_20261024_022

- 発話: ドギドは剣の見た目をどう思う？
- 正解: `not_target`
- 予測: `look_player`（90.1%）

### normal_tool_contrast_20261024_023

- 発話: この剣、ちょっと長く感じるな。
- 正解: `not_target`
- 予測: `look_player`（65.7%）

### normal_tool_contrast_20261024_024

- 発話: 剣と斧の違いについて聞きたいんだ。
- 正解: `not_target`
- 予測: `look_player`（82.6%）

### normal_tool_contrast_20261024_025

- 発話: ドギド、持ち替えを手伝って。今の道具を剣に替えてほしい。
- 正解: `equip_sword`
- 予測: `look_player`（98.7%）

### normal_tool_contrast_20261024_027

- 発話: 手には今シャベルを持ってるよ。
- 正解: `not_target`
- 予測: `look_player`（99.7%）

### normal_tool_contrast_20261024_029

- 発話: 階段を降りるから、降りる前に手持ちを剣にしておいて。
- 正解: `equip_sword`
- 予測: `look_player`（75.1%）

### normal_tool_contrast_20261024_032

- 発話: 道具をいろいろ持ってきてよかった。
- 正解: `not_target`
- 予測: `look_player`（64.9%）
