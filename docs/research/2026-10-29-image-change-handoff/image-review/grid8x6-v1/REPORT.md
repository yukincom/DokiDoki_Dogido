目的: 8×6の各マスで大きな局所差・明るさ差だけを検出し、枠とメッセージを添えた効果を実画像で比較する。変化率はモデルへ渡さない。

| ケース | 検出マス | 局所差 | 明るさ差 | 通知 | 前 → 後 |
|---|---:|---:|---:|---|---|
| pair_01 | 0 | 0 | 0 | 変化がない | [前](pair_01/before-model.png) → [後](pair_01/after-grid.png) |
| pair_02 | 0 | 0 | 0 | 変化がない | [前](pair_02/before-model.png) → [後](pair_02/after-grid.png) |
| pair_03 | 0 | 0 | 0 | 変化がない | [前](pair_03/before-model.png) → [後](pair_03/after-grid.png) |
| pair_04 | 0 | 0 | 0 | 変化がない | [前](pair_04/before-model.png) → [後](pair_04/after-grid.png) |
| pair_05 | 0 | 0 | 0 | 変化がない | [前](pair_05/before-model.png) → [後](pair_05/after-grid.png) |
| pair_06 | 3 | 3 | 0 | 変化領域を枠で表示 | [前](pair_06/before-model.png) → [後](pair_06/after-grid.png) |
| pair_07 | 1 | 0 | 1 | 変化領域を枠で表示 | [前](pair_07/before-model.png) → [後](pair_07/after-grid.png) |
| pair_08 | 1 | 1 | 0 | 変化領域を枠で表示 | [前](pair_08/before-model.png) → [後](pair_08/after-grid.png) |
| pair_09 | 22 | 0 | 22 | 変化領域を枠で表示 | [前](pair_09/before-model.png) → [後](pair_09/after-grid.png) |
| pair_10 | 13 | 8 | 8 | 変化領域を枠で表示 | [前](pair_10/before-model.png) → [後](pair_10/after-grid.png) |
| pair_11 | 20 | 0 | 20 | 変化領域を枠で表示 | [前](pair_11/before-model.png) → [後](pair_11/after-grid.png) |
| pair_12 | 1 | 0 | 1 | 変化領域を枠で表示 | [前](pair_12/before-model.png) → [後](pair_12/after-grid.png) |
| pair_13 | 44 | 5 | 44 | 変化領域を枠で表示 | [前](pair_13/before-model.png) → [後](pair_13/after-grid.png) |

閾値を超えたマスだけに1pxのオレンジ枠を追加。全マスの線・数値・変化率は描かない。過去発話なしの独立診断で、反復抑制は未評価。

| ケース | 条件 | seed | action | 返答 | 外形 | 秒 |
|---|---|---:|---|---|---|---:|
| pair_01 | images_only | 42 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやな！ | json | 1.684 |
| pair_01 | grid_hint | 42 | silent |  | json | 1.182 |
| pair_01 | grid_hint | 43 | silent |  | json | 1.171 |
| pair_01 | images_only | 43 | speak | お、金が見つかったんちゃうか？めっちゃキラキラしてるやん！ | json | 1.325 |
| pair_01 | images_only | 44 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやん！ | json | 1.289 |
| pair_01 | grid_hint | 44 | silent |  | json | 1.171 |
| pair_02 | grid_hint | 42 | silent |  | json | 1.171 |
| pair_02 | images_only | 42 | speak | ええ天気やな！海がめっちゃ綺麗で見とれるわ。 | json | 1.338 |
| pair_02 | images_only | 43 | silent |  | json | 1.169 |
| pair_02 | grid_hint | 43 | silent |  | json | 1.17 |
| pair_02 | grid_hint | 44 | silent |  | json | 1.174 |
| pair_02 | images_only | 44 | silent |  | json | 1.165 |
| pair_03 | images_only | 42 | silent |  | json | 1.169 |
| pair_03 | grid_hint | 42 | silent |  | json | 1.174 |
| pair_03 | grid_hint | 43 | silent |  | json | 1.17 |
| pair_03 | images_only | 43 | silent |  | json | 1.171 |
| pair_03 | images_only | 44 | silent |  | json | 1.168 |
| pair_03 | grid_hint | 44 | silent |  | json | 1.171 |
| pair_04 | grid_hint | 42 | silent |  | json | 1.174 |
| pair_04 | images_only | 42 | speak | 海の中はめっちゃ綺麗やな！なんかワクワクするわ！ | json | 1.31 |
| pair_04 | images_only | 43 | speak | 海の中、めっちゃ綺麗やなぁ！なんかワクワクするわ！ | json | 1.302 |
| pair_04 | grid_hint | 43 | silent |  | json | 1.175 |
| pair_04 | grid_hint | 44 | silent |  | json | 1.177 |
| pair_04 | images_only | 44 | speak | 海の中、めっちゃ綺麗やな！なんか落ち着くわぁ。 | json | 1.34 |
| pair_05 | images_only | 42 | speak | お、なんか綺麗な珊瑚が見えてきたやん！ | json | 1.261 |
| pair_05 | grid_hint | 42 | silent |  | json | 1.172 |
| pair_05 | grid_hint | 43 | silent |  | json | 1.174 |
| pair_05 | images_only | 43 | speak | 海の中、めっちゃ綺麗やなぁ！なんかワクワクするわ！ | json | 1.291 |
| pair_05 | images_only | 44 | speak | お、海がめっちゃ綺麗やん！なんか癒やされるなぁ。 | json | 1.309 |
| pair_05 | grid_hint | 44 | silent |  | json | 1.172 |
| pair_06 | grid_hint | 42 | speak | あ、砂浜に何か緑色のん落ちてるで！何やろな？ | json | 1.321 |
| pair_06 | images_only | 42 | speak | あ、見て！カメがおるやん！めっちゃ可愛ええなあ。 | json | 1.336 |
| pair_06 | images_only | 43 | speak | あ、見て！亀が砂浜におるで！めっちゃ可愛いやん！ | json | 1.317 |
| pair_06 | grid_hint | 43 | speak | あ、砂浜に緑色のやつがおるで！何やろ？ | json | 1.31 |
| pair_06 | grid_hint | 44 | speak | あ、砂浜に緑色の何かが落ちてるで！何やろな？ | json | 1.325 |
| pair_06 | images_only | 44 | speak | あ！見てや、亀がおるで！めっちゃ可愛いやん！ | json | 1.335 |
| pair_07 | images_only | 42 | speak | 見てや！金が見つかったで！これ、めっちゃ価値ありそうやん！ | json | 1.311 |
| pair_07 | grid_hint | 42 | speak | お、金が見つかったで！これ、めっちゃ価値高そうやん！ | json | 1.299 |
| pair_07 | grid_hint | 43 | speak | お、金が見つかったんちゃうか？ラッキーやな！ | json | 1.296 |
| pair_07 | images_only | 43 | speak | お、金が見つかったやん！これ、めっちゃ価値高そうやで！ | json | 1.33 |
| pair_07 | images_only | 44 | speak | 見てや！金が見つかったで！これ、めっちゃ価値あるやつやん！ | json | 1.324 |
| pair_07 | grid_hint | 44 | speak | おっ、金が見つかったんちゃうか？ラッキーやな！ | json | 1.317 |
| pair_08 | grid_hint | 42 | speak | あ、松明の火が揺れてるみたいや。なんか落ち着くなぁ。 | fenced_json | 1.394 |
| pair_08 | images_only | 42 | speak | 急に暗くなった気がするけど、気のせいか？ | json | 1.277 |
| pair_08 | images_only | 43 | speak | なんか急に暗くなった気がするやん！どこ行ったんや？ | json | 1.344 |
| pair_08 | grid_hint | 43 | speak | あ、あそこに松明が落ちてるで！誰かが置いたんかな？ | json | 1.399 |
| pair_08 | grid_hint | 44 | speak | あ、あそこに松明あるやん！なんか光ってるで！ | json | 1.315 |
| pair_08 | images_only | 44 | speak | な、急に暗くなった気がせえへんか？なんか変やで... | json | 1.373 |
| pair_09 | images_only | 42 | speak | ええ天気やな！海もキラキラしてて気持ちええわ！ | json | 1.322 |
| pair_09 | grid_hint | 42 | speak | わあ、めっちゃ綺麗な景色やな！海がキラキラしてるで！ | json | 1.326 |
| pair_09 | grid_hint | 43 | speak | お、太陽が昇ってきたな！今日もええ天気やで！ | json | 1.297 |
| pair_09 | images_only | 43 | speak | ええ景色やなぁ。海がキラキラしてて気持ちええわ！ | json | 1.306 |
| pair_09 | images_only | 44 | speak | ええ景色やなぁ。海がキラキラしてて気持ちええわ！ | json | 1.306 |
| pair_09 | grid_hint | 44 | speak | わぁ、めっちゃ綺麗な夕焼けやん！最高やな！ | json | 1.333 |
| pair_10 | grid_hint | 42 | speak | なんか空の雲の形が変わったみたいやな！ | json | 1.317 |
| pair_10 | images_only | 42 | speak | お、景色変わったな！もっと広い場所に出た感じやん！ | json | 1.323 |
| pair_10 | images_only | 43 | speak | なんか、景色がちょっと変わった気がするけど、気のせいか？ | json | 1.281 |
| pair_10 | grid_hint | 43 | speak | なんか、空の雲の形が変わった気がするな。気のせいか？ | json | 1.348 |
| pair_10 | grid_hint | 44 | speak | なんか急に景色が明るくなった気がするな！ | json | 1.299 |
| pair_10 | images_only | 44 | speak | お、景色がちょっと変わったな。あっちに木が見えてきたで！ | json | 1.324 |
| pair_11 | images_only | 42 | silent |  | json | 1.168 |
| pair_11 | grid_hint | 42 | speak | なんか、さっきから視界が悪くないか？暗すぎて何も見えへんわ。 | json | 1.39 |
| pair_11 | grid_hint | 43 | speak | なんか、地面の様子が変わった気がするんやけど、気のせいか？ | json | 1.345 |
| pair_11 | images_only | 43 | speak | なんか、さっきより暗くなった気がするんやけど……気のせいかな？ | json | 1.338 |
| pair_11 | images_only | 44 | speak | なんか暗いなぁ…ここ、洞窟の中か？ | json | 1.306 |
| pair_11 | grid_hint | 44 | speak | なんか、地面のところにオレンジ色の枠みたいなのが出てるで？ | json | 1.326 |
| pair_12 | grid_hint | 42 | speak | うわっ、急に明るなったな！なんか見えてきたで！ | json | 1.333 |
| pair_12 | images_only | 42 | speak | うわっ、暗いな！ツレ、どこにおるんや？ | json | 1.358 |
| pair_12 | images_only | 43 | speak | 暗すぎて何も見えへんやん！ツレ、ライト持ってへんの？ | json | 1.36 |
| pair_12 | grid_hint | 43 | speak | うわっ、急に目の前に何かおるやん！大丈夫か！ | json | 1.345 |
| pair_12 | grid_hint | 44 | speak | うわっ、急に明るくなったやん！何があったん？ | json | 1.33 |
| pair_12 | images_only | 44 | speak | うわっ、暗っ！ツレ、どこにおるんや？オレ、怖くなってきたわ。 | json | 1.484 |
| pair_13 | images_only | 42 | speak | うわっ、急に暗なったな！さっきまで海の中やったんか？ | json | 1.342 |
| pair_13 | grid_hint | 42 | speak | うわっ、急に明るくなったな！外に出たんか？ | json | 1.348 |
| pair_13 | grid_hint | 43 | speak | 急に明るくなったな！太陽が出てきたんか？ | json | 1.301 |
| pair_13 | images_only | 43 | speak | うわっ、急に景色変わったやん！ここはどこや？ | json | 1.33 |
| pair_13 | images_only | 44 | speak | うわっ、急に暗くなったやん！どこ行ったん！？ | json | 1.328 |
| pair_13 | grid_hint | 44 | speak | うわっ、急に明るなったな！空が見えるで！ | json | 1.356 |
