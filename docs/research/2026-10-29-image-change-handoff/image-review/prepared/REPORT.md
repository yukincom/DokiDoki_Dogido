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

| ケース | 条件 | seed | action | 返答 | 秒 |
|---|---|---:|---|---|---:|
| pair_01 | images_only | 42 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやな！ | 4.482 |
| pair_01 | images_and_amount | 42 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやな！ | 1.378 |
| pair_01 | images_amount_and_regions | 42 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやな！ | 1.452 |
| pair_01 | images_and_amount | 43 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.379 |
| pair_01 | images_amount_and_regions | 43 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.463 |
| pair_01 | images_only | 43 | speak | お、金が見つかったんちゃうか？めっちゃキラキラしてるやん！ | 1.331 |
| pair_01 | images_amount_and_regions | 44 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.48 |
| pair_01 | images_only | 44 | speak | お、金が見つかったで！これ、めっちゃ価値ありそうやん！ | 1.298 |
| pair_01 | images_and_amount | 44 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.409 |
| pair_02 | images_and_amount | 42 | silent |  | 1.256 |
| pair_02 | images_amount_and_regions | 42 | silent |  | 1.323 |
| pair_02 | images_only | 42 | speak | ええ天気やな！海がめっちゃ綺麗で見とれるわ。 | 1.351 |
| pair_02 | images_amount_and_regions | 43 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.378 |
| pair_02 | images_only | 43 | silent |  | 1.201 |
| pair_02 | images_and_amount | 43 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.369 |
| pair_02 | images_only | 44 | silent |  | 1.178 |
| pair_02 | images_and_amount | 44 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.443 |
| pair_02 | images_amount_and_regions | 44 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.378 |
| pair_03 | images_amount_and_regions | 42 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.461 |
| pair_03 | images_only | 42 | silent |  | 1.194 |
| pair_03 | images_and_amount | 42 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.406 |
| pair_03 | images_only | 43 | silent |  | 1.178 |
| pair_03 | images_and_amount | 43 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.431 |
| pair_03 | images_amount_and_regions | 43 | silent |  | 1.432 |
| pair_03 | images_and_amount | 44 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.332 |
| pair_03 | images_amount_and_regions | 44 | silent |  | 1.426 |
| pair_03 | images_only | 44 | silent |  | 1.182 |
| pair_04 | images_only | 42 | speak | 海の中はめっちゃ綺麗やな！なんかワクワクするわ！ | 1.31 |
| pair_04 | images_and_amount | 42 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.405 |
| pair_04 | images_amount_and_regions | 42 | invalid | JSONDecodeError: Expecting value: line 1 column 1 (char 0) | 1.808 |
