目的: 実撮影だけで差分量・領域の効果を比較する。発話率は過敏反応率ではなく、画像にない変化の断定や誇張を内容で確認する。

| ケース | 変化セル割合 | 平均差 / 255 | 視点 | 前 → 後 |
|---|---:|---:|---|---|
| pair_01 | 0.000% | 0.000 | 同姿勢 | [前](pair_01/before-native.png) → [後](pair_01/after-native.png) |
| pair_02 | 0.000% | 4.688 | 同姿勢 | [前](pair_02/before-native.png) → [後](pair_02/after-native.png) |
| pair_03 | 0.174% | 0.439 | 同姿勢 | [前](pair_03/before-native.png) → [後](pair_03/after-native.png) |
| pair_04 | 0.868% | 2.034 | 同姿勢 | [前](pair_04/before-native.png) → [後](pair_04/after-native.png) |
| pair_05 | 1.215% | 4.983 | 同姿勢 | [前](pair_05/before-native.png) → [後](pair_05/after-native.png) |
| pair_06 | 1.910% | 2.035 | 同姿勢 | [前](pair_06/before-native.png) → [後](pair_06/after-native.png) |
| pair_07 | 2.951% | 3.564 | 同姿勢 | [前](pair_07/before-native.png) → [後](pair_07/after-native.png) |
| pair_08 | 5.208% | 6.845 | 同姿勢 | [前](pair_08/before-native.png) → [後](pair_08/after-native.png) |
| pair_09 | 15.451% | 15.943 | 同姿勢 | [前](pair_09/before-native.png) → [後](pair_09/after-native.png) |
| pair_10 | 20.833% | 27.800 | 同姿勢 | [前](pair_10/before-native.png) → [後](pair_10/after-native.png) |
| pair_11 | 36.458% | 15.547 | 同姿勢 | [前](pair_11/before-native.png) → [後](pair_11/after-native.png) |
| pair_12 | 0.000% | 8.026 | 旋回あり | [前](pair_12/before-native.png) → [後](pair_12/after-native.png) |
| pair_13 | 86.632% | 40.649 | 旋回あり | [前](pair_13/before-native.png) → [後](pair_13/after-native.png) |

実画像対13件。1セル=約0.174%。微小色差は領域集計から除くが、この校正試験では低変化率もモデルへ渡す。実運用の棄却閾値は未決定。

全条件で同じ撮影済み192px画像を使用。元画像は加工しない。過去発話なしの独立試験なので、会話履歴を使った反復抑制は未評価。

| ケース | 条件 | seed | action | 返答 | 外形 | 秒 |
|---|---|---:|---|---|---|---:|
| pair_01 | images_only | 42 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやな！ | json | 1.536 |
| pair_01 | images_and_amount | 42 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやな！ | json | 1.41 |
| pair_01 | images_amount_and_regions | 42 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやな！ | json | 1.449 |
| pair_01 | images_and_amount | 43 | speak | お、金か？なんかキラキラしてるやん！ | fenced_json | 1.38 |
| pair_01 | images_amount_and_regions | 43 | speak | お、金鉱石やん！これ、結構すごいやんか！ | fenced_json | 1.523 |
| pair_01 | images_only | 43 | speak | お、金が見つかったんちゃうか？めっちゃキラキラしてるやん！ | json | 1.411 |
| pair_01 | images_amount_and_regions | 44 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやん！ | fenced_json | 1.485 |
| pair_01 | images_only | 44 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやん！ | json | 1.31 |
| pair_01 | images_and_amount | 44 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやん！ | fenced_json | 1.416 |
| pair_02 | images_and_amount | 42 | silent |  | json | 1.259 |
| pair_02 | images_amount_and_regions | 42 | silent |  | json | 1.344 |
| pair_02 | images_only | 42 | speak | ええ天気やな！海がめっちゃ綺麗で見とれるわ。 | json | 1.351 |
| pair_02 | images_amount_and_regions | 43 | silent |  | fenced_json | 1.372 |
| pair_02 | images_only | 43 | silent |  | json | 1.185 |
| pair_02 | images_and_amount | 43 | silent |  | fenced_json | 1.301 |
| pair_02 | images_only | 44 | silent |  | json | 1.167 |
| pair_02 | images_and_amount | 44 | speak | ええ天気やな！海もキラキラしてて気持ちええわ！ | fenced_json | 1.44 |
| pair_02 | images_amount_and_regions | 44 | silent |  | fenced_json | 1.373 |
| pair_03 | images_amount_and_regions | 42 | silent |  | fenced_json | 1.452 |
| pair_03 | images_only | 42 | silent |  | json | 1.182 |
| pair_03 | images_and_amount | 42 | speak | 海の中って、めっちゃ綺麗やなぁ！ | fenced_json | 1.428 |
| pair_03 | images_only | 43 | silent |  | json | 1.172 |
| pair_03 | images_and_amount | 43 | speak | 海の中、めっちゃ綺麗やな！なんかワクワクするわ！ | fenced_json | 1.421 |
| pair_03 | images_amount_and_regions | 43 | silent |  | json | 1.425 |
| pair_03 | images_and_amount | 44 | silent |  | fenced_json | 1.335 |
| pair_03 | images_amount_and_regions | 44 | silent |  | json | 1.443 |
| pair_03 | images_only | 44 | silent |  | json | 1.186 |
| pair_04 | images_only | 42 | speak | 海の中はめっちゃ綺麗やな！なんかワクワクするわ！ | json | 1.31 |
| pair_04 | images_and_amount | 42 | speak | 海の中って、めっちゃ綺麗やなぁ！ | fenced_json | 1.394 |
| pair_04 | images_amount_and_regions | 42 | speak | 海の中はめっちゃ綺麗やな！なんか落ち着くわぁ。 | fenced_json | 1.786 |
| pair_04 | images_and_amount | 43 | speak | 海の中、めっちゃ綺麗やな！なんかワクワクするわ！ | fenced_json | 1.439 |
| pair_04 | images_amount_and_regions | 43 | speak | 海の中、めっちゃ綺麗やん！なんかキラキラしてるで！ | fenced_json | 1.722 |
| pair_04 | images_only | 43 | speak | 海の中、めっちゃ綺麗やなぁ！なんかワクワクするわ！ | json | 1.31 |
| pair_04 | images_amount_and_regions | 44 | speak | 海の中、めっちゃ綺麗やん！なんかキラキラしとるで！ | fenced_json | 1.737 |
| pair_04 | images_only | 44 | speak | 海の中、めっちゃ綺麗やな！なんか落ち着くわぁ。 | json | 1.36 |
| pair_04 | images_and_amount | 44 | speak | 海の中、めっちゃ綺麗やな！なんか落ち着くわ。 | fenced_json | 1.457 |
| pair_05 | images_and_amount | 42 | speak | なんか、さっきより明るくなった気がするな？ | json | 1.38 |
| pair_05 | images_amount_and_regions | 42 | speak | お、海の中もめっちゃ綺麗やん！なんかキラキラしてるで！ | fenced_json | 1.829 |
| pair_05 | images_only | 42 | speak | お、なんか綺麗な珊瑚が見えてきたやん！ | json | 1.274 |
| pair_05 | images_amount_and_regions | 43 | speak | お、なんかあっちの方で動いたもんおるか？ | fenced_json | 1.845 |
| pair_05 | images_only | 43 | speak | 海の中、めっちゃ綺麗やなぁ！なんかワクワクするわ！ | json | 1.306 |
| pair_05 | images_and_amount | 43 | speak | なんか、さっきより明るくなった気がするな！ | fenced_json | 1.41 |
| pair_05 | images_only | 44 | speak | お、海がめっちゃ綺麗やん！なんか癒やされるなぁ。 | json | 1.306 |
| pair_05 | images_and_amount | 44 | speak | お、なんか海の色がちょっと変わった気がするな？ | fenced_json | 1.41 |
| pair_05 | images_amount_and_regions | 44 | speak | めっちゃ綺麗な海やな！ここ、どこや？ | fenced_json | 1.814 |
| pair_06 | images_amount_and_regions | 42 | speak | あ、見て！亀が砂浜におるで！めっちゃ可愛いやん！ | fenced_json | 1.823 |
| pair_06 | images_only | 42 | speak | あ、見て！カメがおるやん！めっちゃ可愛ええなあ。 | json | 1.346 |
| pair_06 | images_and_amount | 42 | speak | あ、見て！カメさんが砂浜におるで！ | json | 1.386 |
| pair_06 | images_only | 43 | speak | あ、見て！亀が砂浜におるで！めっちゃ可愛いやん！ | json | 1.323 |
| pair_06 | images_and_amount | 43 | speak | あ、見てや！カメさんが砂浜におるで！ | fenced_json | 1.437 |
| pair_06 | images_amount_and_regions | 43 | speak | あ、カメがおるやん！めっちゃ可愛いなぁ。 | fenced_json | 1.725 |
| pair_06 | images_and_amount | 44 | speak | あ、見て！砂浜にカメがおるやん！めっちゃ可愛ええな。 | fenced_json | 1.48 |
| pair_06 | images_amount_and_regions | 44 | speak | あ、カメがおるやん！めっちゃ可愛ええな！ | fenced_json | 1.723 |
| pair_06 | images_only | 44 | speak | あ！見てや、亀がおるで！めっちゃ可愛いやん！ | json | 1.346 |
| pair_07 | images_only | 42 | speak | 見てや！金が見つかったで！これ、めっちゃ価値ありそうやん！ | json | 1.312 |
| pair_07 | images_and_amount | 42 | speak | お、金の発光がめっちゃ綺麗やん！これ掘ってもええんかな？ | fenced_json | 1.484 |
| pair_07 | images_amount_and_regions | 42 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやん！ | json | 1.576 |
| pair_07 | images_and_amount | 43 | speak | お、金が見つかったで！これ、めっちゃ価値高そうやん！ | fenced_json | 1.452 |
| pair_07 | images_amount_and_regions | 43 | speak | お、金の発掘やん！これ、めっちゃ価値高そうやで！ | fenced_json | 1.618 |
| pair_07 | images_only | 43 | speak | お、金が見つかったやん！これ、めっちゃ価値高そうやで！ | json | 1.338 |
| pair_07 | images_amount_and_regions | 44 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやん！ | fenced_json | 1.618 |
| pair_07 | images_only | 44 | speak | 見てや！金が見つかったで！これ、めっちゃ価値あるやつやん！ | json | 1.358 |
| pair_07 | images_and_amount | 44 | speak | おっ、金が見つかったで！これ、めっちゃ価値ありそうやん！ | fenced_json | 1.441 |
| pair_08 | images_and_amount | 42 | speak | な、なんか急に暗くなった気がせぇへん？ | fenced_json | 1.428 |
| pair_08 | images_amount_and_regions | 42 | speak | な、なんか急に暗くなった気がせぇへん？ | fenced_json | 2.193 |
| pair_08 | images_only | 42 | speak | 急に暗くなった気がするけど、気のせいか？ | json | 1.388 |
| pair_08 | images_amount_and_regions | 43 | speak | な、急に暗くなった気がせぇへんか？なんか変やで。 | fenced_json | 2.28 |
| pair_08 | images_only | 43 | speak | なんか急に暗くなった気がするやん！どこ行ったんや？ | json | 1.458 |
| pair_08 | images_and_amount | 43 | speak | な、急に暗くなった気がせぇへんか？なんか怖いわぁ… | fenced_json | 1.503 |
| pair_08 | images_only | 44 | speak | な、急に暗くなった気がせえへんか？なんか変やで... | json | 1.373 |
| pair_08 | images_and_amount | 44 | speak | あれ、ツレ、どこ行ったんや！？急に消えたやんけ！ | fenced_json | 1.486 |
| pair_08 | images_amount_and_regions | 44 | speak | ちょ、ツレ！どこ行ったんや！急に消えすぎやろ！ | fenced_json | 2.268 |
| pair_09 | images_amount_and_regions | 42 | speak | ええ景色やなぁ！海がめっちゃ綺麗やんか！ | fenced_json | 2.26 |
| pair_09 | images_only | 42 | speak | ええ天気やな！海もキラキラしてて気持ちええわ！ | json | 1.432 |
| pair_09 | images_and_amount | 42 | silent |  | fenced_json | 1.318 |
| pair_09 | images_only | 43 | speak | ええ景色やなぁ。海がキラキラしてて気持ちええわ！ | json | 1.304 |
| pair_09 | images_and_amount | 43 | silent |  | json | 1.268 |
| pair_09 | images_amount_and_regions | 43 | speak | ええ景色やな！海がめっちゃ綺麗やん！ | fenced_json | 2.147 |
| pair_09 | images_and_amount | 44 | silent |  | json | 1.279 |
| pair_09 | images_amount_and_regions | 44 | speak | ええ景色やな！海がめっちゃ綺麗やんか！ | fenced_json | 2.178 |
| pair_09 | images_only | 44 | speak | ええ景色やなぁ。海がキラキラしてて気持ちええわ！ | json | 1.419 |
| pair_10 | images_only | 42 | speak | お、景色変わったな！もっと広い場所に出た感じやん！ | json | 1.326 |
| pair_10 | images_and_amount | 42 | speak | お、あっちの丘の向こうに木がめっちゃ増えたやん！ | fenced_json | 1.47 |
| pair_10 | images_amount_and_regions | 42 | speak | お、向こうの景色も綺麗やな！晴れてて気持ちええわ！ | json | 1.599 |
| pair_10 | images_and_amount | 43 | speak | お、景色変わったな！あっちに木がもっと増えてるで！ | fenced_json | 1.442 |
| pair_10 | images_amount_and_regions | 43 | speak | お、向こうの丘の向こうに木がいくつか見えてきたな！ | fenced_json | 1.589 |
| pair_10 | images_only | 43 | speak | なんか、景色がちょっと変わった気がするけど、気のせいか？ | json | 1.303 |
| pair_10 | images_amount_and_regions | 44 | speak | あ、向こうの丘の向こうに、何か新しい木が生えてきたんちゃう？ | fenced_json | 1.668 |
| pair_10 | images_only | 44 | speak | お、景色がちょっと変わったな。あっちに木が見えてきたで！ | json | 1.339 |
| pair_10 | images_and_amount | 44 | speak | あ、向こうの丘の向こうに木がもっと増えた気がするで！ | fenced_json | 1.493 |
| pair_11 | images_and_amount | 42 | speak | なんかさっきより明るくなったか？気のせいかな。 | fenced_json | 1.441 |
| pair_11 | images_amount_and_regions | 42 | speak | なんかさっきより、ちょっと暗くなった気がするんやけど…？ | fenced_json | 1.676 |
| pair_11 | images_only | 42 | silent |  | json | 1.184 |
| pair_11 | images_amount_and_regions | 43 | silent |  | json | 1.464 |
| pair_11 | images_only | 43 | speak | なんか、さっきより暗くなった気がするんやけど……気のせいかな？ | json | 1.354 |
| pair_11 | images_and_amount | 43 | speak | なんか、さっきより暗くなった気がするわ。気のせいか？ | fenced_json | 1.515 |
| pair_11 | images_only | 44 | speak | なんか暗いなぁ…ここ、洞窟の中か？ | json | 1.444 |
| pair_11 | images_and_amount | 44 | speak | なんかさっきより暗くなった気がするわ。気のせいか？ | fenced_json | 1.437 |
| pair_11 | images_amount_and_regions | 44 | silent |  | json | 1.462 |
| pair_12 | images_amount_and_regions | 42 | speak | なんか急に明るうなったな？何があったんや？ | fenced_json | 1.591 |
| pair_12 | images_only | 42 | speak | うわっ、暗いな！ツレ、どこにおるんや？ | json | 1.372 |
| pair_12 | images_and_amount | 42 | speak | なんか暗すぎて、何が何だかさっぱりわからんわ… | fenced_json | 1.479 |
| pair_12 | images_only | 43 | speak | 暗すぎて何も見えへんやん！ツレ、ライト持ってへんの？ | json | 1.372 |
| pair_12 | images_and_amount | 43 | speak | なんか、さっきより少し明るくなった気がするな？ | fenced_json | 1.441 |
| pair_12 | images_amount_and_regions | 43 | speak | なんか、さっきより明るくなった気がするんやけど？ | fenced_json | 1.527 |
| pair_12 | images_and_amount | 44 | speak | 暗すぎて何も見えへんやん！ツレ、どこにおるん？ | fenced_json | 1.501 |
| pair_12 | images_amount_and_regions | 44 | silent |  | fenced_json | 1.384 |
| pair_12 | images_only | 44 | speak | うわっ、暗っ！ツレ、どこにおるんや？オレ、怖くなってきたわ。 | json | 1.479 |
| pair_13 | images_only | 42 | speak | うわっ、急に暗なったな！さっきまで海の中やったんか？ | json | 1.346 |
| pair_13 | images_and_amount | 42 | speak | うわっ、急に地上に出た感じやな！暗いけど大丈夫か？ | json | 1.473 |
| pair_13 | images_amount_and_regions | 42 | speak | うわっ、一気に外に出たやん！急に明るなってびっくりしたわ！ | fenced_json | 1.77 |
| pair_13 | images_and_amount | 43 | speak | うわっ！急に陸の上に出た感じやな！どこやここ？ | fenced_json | 1.525 |
| pair_13 | images_amount_and_regions | 43 | speak | うわっ！急に陸の上に出たやん！どっか行ったんか？ | fenced_json | 1.777 |
| pair_13 | images_only | 43 | speak | うわっ、急に景色変わったやん！ここはどこや？ | json | 1.341 |
| pair_13 | images_amount_and_regions | 44 | speak | 急に地上に出た感じやな！夜やからちょっと暗いけど大丈夫か？ | fenced_json | 1.769 |
| pair_13 | images_only | 44 | speak | うわっ、急に暗くなったやん！どこ行ったん！？ | json | 1.345 |
| pair_13 | images_and_amount | 44 | speak | 急に視界が開けたな！ここは砂浜なんか？ | fenced_json | 1.46 |
