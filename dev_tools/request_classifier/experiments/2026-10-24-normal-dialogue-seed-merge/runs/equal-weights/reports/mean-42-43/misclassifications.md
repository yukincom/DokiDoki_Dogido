# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## dev：26 / 46件

### legacy__train-101

- 発話: 斧じゃなくて剣にして。
- 正解: `equip_sword`
- 予測: `look_player`（98.3%）

### legacy__train-105

- 発話: 馬を呼ばなくていい、今は剣を装備して。
- 正解: `equip_sword`
- 予測: `look_player`（91.8%）

### legacy__train-107

- 発話: 木の剣じゃなくて鉄の剣を持って。
- 正解: `equip_sword`
- 予測: `look_player`（93.5%）

### legacy__train-576

- 発話: 敵が出てくる前に備えたいから、今、剣に持ち替えて。
- 正解: `equip_sword`
- 予測: `look_player`（64.8%）

### live__short_runtime_014

- 発話: ゾンビ出たら泣いちゃう？
- 正解: `not_target`
- 予測: `look_player`（76.3%）

### live__short_runtime_033

- 発話: ドギドー何してるの?
- 正解: `not_target`
- 予測: `look_player`（99.2%）

### live__short_runtime_040

- 発話: こっちにおいで
- 正解: `dogido_recall`
- 予測: `look_player`（99.6%）

### live__short_runtime_041

- 発話: どぎど私の隣においで
- 正解: `dogido_recall`
- 予測: `look_player`（94.3%）

### live__short_runtime_045

- 発話: こっちで一緒にしようよ
- 正解: `dogido_recall`
- 予測: `look_player`（99.9%）

### live__short_runtime_059

- 発話: 今日の冒険は終了しましょうか
- 正解: `not_target`
- 予測: `look_player`（86.6%）

### user_addition__user_20261022_055

- 発話: 右手を確認して！
- 正解: `look_relative_right`
- 予測: `look_player`（94.5%）

### user_addition__user_20261022_063

- 発話: わー！なんか変なものがあるぞー！これはなんだろう？
- 正解: `look_target`
- 予測: `look_player`（95.1%）

### user_addition__user_20261022_067

- 発話: こいつはどういうものだ？
- 正解: `look_target`
- 予測: `look_player`（94.7%）

### contrast__contrast_011

- 発話: ドギド、あっちのクリーパーのほうをみて！
- 正解: `look_direction`
- 予測: `look_player`（93.6%）

### contrast__contrast_037

- 発話: 頭上の明るいところ、見上げてみて。
- 正解: `look_direction`
- 予測: `look_player`（97.3%）

### contrast__contrast_038

- 発話: 後ろを振り向いて、私が見てる方を見て。
- 正解: `look_direction`
- 予測: `look_player`（92.4%）

### contrast__contrast_040

- 発話: 私はさっき後ろを振り向いたよ。
- 正解: `not_target`
- 予測: `look_player`（62.2%）

### contrast__contrast_041

- 発話: 松明を持つのはやめて、鉄の剣へ持ち替えて。
- 正解: `equip_sword`
- 予測: `look_player`（93.9%）

### contrast__contrast_042

- 発話: この松明を壁に設置してくれる？
- 正解: `not_target`
- 予測: `look_player`（91.4%）

### contrast__contrast_044

- 発話: ラクダを呼ぶつもりだったけど、今日は歩くことにした。
- 正解: `not_target`
- 予測: `look_player`（50.6%）

### gaze_variant__gaze_direction_07_front

- 発話: 正面を確認して！
- 正解: `look_relative_front`
- 予測: `look_player`（98.9%）

### gaze_variant__gaze_direction_07_front_left

- 発話: 左斜め前を確認して！
- 正解: `look_relative_front_left`
- 予測: `look_player`（96.0%）

### gaze_variant__gaze_direction_07_back_left

- 発話: 左斜め後ろを確認して！
- 正解: `look_relative_back_left`
- 予測: `look_player`（82.6%）

### gaze_variant__gaze_far_align_view_01

- 発話: 今わたしが見てる方、ドギドも見てみて。
- 正解: `look_direction`
- 予測: `look_player`（99.9%）

### gaze_variant__gaze_far_align_view_02

- 発話: 私と同じ方を向いてみて。遠くにおもしろいものがあるよ。
- 正解: `look_direction`
- 予測: `look_player`（99.9%）

### gaze_variant__gaze_far_align_view_03

- 発話: ほら、私が眺めてるあたり。あそこに何か見えるでしょ？
- 正解: `look_direction`
- 予測: `look_player`（97.8%）
