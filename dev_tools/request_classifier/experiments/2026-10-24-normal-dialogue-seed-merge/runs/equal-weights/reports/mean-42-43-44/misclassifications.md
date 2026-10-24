# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## dev：28 / 46件

### legacy__train-105

- 発話: 馬を呼ばなくていい、今は剣を装備して。
- 正解: `equip_sword`
- 予測: `enemy_information`（79.0%）

### legacy__train-107

- 発話: 木の剣じゃなくて鉄の剣を持って。
- 正解: `equip_sword`
- 予測: `look_player`（65.0%）

### legacy__train-160

- 発話: さっきは不要って言ったけど、今はラマを呼び戻して。
- 正解: `recall_mount`
- 予測: `look_player`（55.7%）

### legacy__train-576

- 発話: 敵が出てくる前に備えたいから、今、剣に持ち替えて。
- 正解: `equip_sword`
- 予測: `enemy_information`（73.0%）

### live__short_runtime_014

- 発話: ゾンビ出たら泣いちゃう？
- 正解: `not_target`
- 予測: `enemy_information`（70.7%）

### live__short_runtime_033

- 発話: ドギドー何してるの?
- 正解: `not_target`
- 予測: `look_player`（88.2%）

### live__short_runtime_040

- 発話: こっちにおいで
- 正解: `dogido_recall`
- 予測: `look_player`（99.9%）

### live__short_runtime_041

- 発話: どぎど私の隣においで
- 正解: `dogido_recall`
- 予測: `look_player`（90.0%）

### live__short_runtime_045

- 発話: こっちで一緒にしようよ
- 正解: `dogido_recall`
- 予測: `look_player`（99.2%）

### live__short_runtime_059

- 発話: 今日の冒険は終了しましょうか
- 正解: `not_target`
- 予測: `enemy_information`（50.8%）

### user_addition__user_20261022_063

- 発話: わー！なんか変なものがあるぞー！これはなんだろう？
- 正解: `look_target`
- 予測: `look_player`（87.0%）

### user_addition__user_20261022_067

- 発話: こいつはどういうものだ？
- 正解: `look_target`
- 予測: `look_player`（84.0%）

### contrast__contrast_011

- 発話: ドギド、あっちのクリーパーのほうをみて！
- 正解: `look_direction`
- 予測: `look_player`（82.1%）

### contrast__contrast_037

- 発話: 頭上の明るいところ、見上げてみて。
- 正解: `look_direction`
- 予測: `look_player`（65.0%）

### contrast__contrast_038

- 発話: 後ろを振り向いて、私が見てる方を見て。
- 正解: `look_direction`
- 予測: `look_player`（86.6%）

### contrast__contrast_040

- 発話: 私はさっき後ろを振り向いたよ。
- 正解: `not_target`
- 予測: `look_player`（52.2%）

### contrast__contrast_041

- 発話: 松明を持つのはやめて、鉄の剣へ持ち替えて。
- 正解: `equip_sword`
- 予測: `look_player`（40.8%）

### contrast__contrast_042

- 発話: この松明を壁に設置してくれる？
- 正解: `not_target`
- 予測: `look_player`（53.0%）

### contrast__contrast_044

- 発話: ラクダを呼ぶつもりだったけど、今日は歩くことにした。
- 正解: `not_target`
- 予測: `look_player`（68.4%）

### gaze_variant__gaze_direction_07_front

- 発話: 正面を確認して！
- 正解: `look_relative_front`
- 予測: `look_player`（56.5%）

### gaze_variant__gaze_direction_07_back

- 発話: 後ろを確認して！
- 正解: `look_relative_back`
- 予測: `look_relative_back_right`（47.5%）

### gaze_variant__gaze_direction_07_left

- 発話: 左手を確認して！
- 正解: `look_relative_left`
- 予測: `look_relative_back_right`（56.0%）

### gaze_variant__gaze_direction_07_front_left

- 発話: 左斜め前を確認して！
- 正解: `look_relative_front_left`
- 予測: `look_player`（79.1%）

### gaze_variant__gaze_direction_07_front_right

- 発話: 右斜め前を確認して！
- 正解: `look_relative_front_right`
- 予測: `look_player`（82.8%）

### gaze_variant__gaze_direction_07_back_left

- 発話: 左斜め後ろを確認して！
- 正解: `look_relative_back_left`
- 予測: `look_player`（51.0%）

### gaze_variant__gaze_far_align_view_01

- 発話: 今わたしが見てる方、ドギドも見てみて。
- 正解: `look_direction`
- 予測: `look_player`（98.9%）

### gaze_variant__gaze_far_align_view_02

- 発話: 私と同じ方を向いてみて。遠くにおもしろいものがあるよ。
- 正解: `look_direction`
- 予測: `look_player`（95.9%）

### gaze_variant__gaze_far_align_view_03

- 発話: ほら、私が眺めてるあたり。あそこに何か見えるでしょ？
- 正解: `look_direction`
- 予測: `look_player`（98.6%）
