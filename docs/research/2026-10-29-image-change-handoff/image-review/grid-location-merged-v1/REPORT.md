目的: 8×6の各マスで大きな局所差・明るさ差だけを検出し、枠とメッセージを添えた効果を実画像で比較する。変化率はモデルへ渡さない。

| ケース | 検出マス | 局所差 | 明るさ差 | 通知 | 前 → 後 |
|---|---:|---:|---:|---|---|
| pair_06 | 3 | 3 | 0 | 画像の明るさの変化は検出していない。 | [前](pair_06/before-model.png) → [後](pair_06/after-merged.png) |
| pair_07 | 1 | 0 | 1 | 画像の明るさに変化がある。明るくなった領域を検出した。 | [前](pair_07/before-model.png) → [後](pair_07/after-merged.png) |
| pair_08 | 1 | 1 | 0 | 画像の明るさの変化は検出していない。 | [前](pair_08/before-model.png) → [後](pair_08/after-merged.png) |
| pair_09 | 22 | 0 | 22 | 画像の明るさに変化がある。明るくなった領域を検出した。 | [前](pair_09/before-model.png) → [後](pair_09/after-merged.png) |
| pair_10 | 13 | 8 | 8 | 画像の明るさに変化がある。暗くなった領域を検出した。 | [前](pair_10/before-model.png) → [後](pair_10/after-merged.png) |
| pair_11 | 20 | 0 | 20 | 画像の明るさに変化がある。明るくなった領域を検出した。 | [前](pair_11/before-model.png) → [後](pair_11/after-merged.png) |
| pair_12 | 1 | 0 | 1 | 画像の明るさに変化がある。明るくなった領域を検出した。 | [前](pair_12/before-model.png) → [後](pair_12/after-merged.png) |
| pair_13 | 44 | 5 | 44 | 画像の明るさに変化がある。明るくなった領域と暗くなった領域をそれぞれ検出した。 | [前](pair_13/before-model.png) → [後](pair_13/after-merged.png) |

検出コード・閾値・各マスの判定を前回から固定。共通の明るさ判定文を添え、枠なしの位置説明と隣接枠の外周だけを比較する。変化率は渡さない。

| ケース | 条件 | seed | action | 返答 | 外形 | 秒 |
|---|---|---:|---|---|---|---:|
| pair_06 | location_text | 42 | speak | あ、見て！カメさんが歩いてきたで！ | json | 1.5 |
| pair_06 | merged_outline | 42 | speak | あ、砂浜に緑色の塊が落ちてるで！何やろな？ | json | 1.34 |
| pair_06 | merged_outline | 43 | speak | 砂浜に緑色のやつが落ちてるで！何やろこれ？ | json | 1.356 |
| pair_06 | location_text | 43 | speak | あ、カメがおるやん！めっちゃのんびりしてるな。 | json | 1.341 |
| pair_06 | location_text | 44 | speak | あ！砂浜にカメがおるやん！めっちゃ可愛いなぁ。 | json | 1.364 |
| pair_06 | merged_outline | 44 | speak | あ、砂浜に緑色の何かが落ちてるで！何やろな？ | json | 1.327 |
| pair_07 | merged_outline | 42 | speak | お、金が見つかったで！これ、めっちゃ価値高そうやん！ | json | 1.353 |
| pair_07 | location_text | 42 | speak | お、なんか右の上の方が明るくなったな？何や、光っとるんか？ | fenced_json | 1.462 |
| pair_07 | location_text | 43 | speak | お、右の方が明るくなったな！何か光ってるんちゃうか？ | json | 1.406 |
| pair_07 | merged_outline | 43 | speak | お、金が見つかったやん！これ、めっちゃ価値高そうやで！ | json | 1.365 |
| pair_07 | merged_outline | 44 | speak | おっ、金が見つかったんちゃうか？やったな！ | json | 1.319 |
| pair_07 | location_text | 44 | speak | おっ、なんか右の上が明るくなったんちゃう？ | json | 1.349 |
| pair_08 | location_text | 42 | speak | な、急に視界が暗くなった気がするんやけど、気のせいか？ | fenced_json | 1.397 |
| pair_08 | merged_outline | 42 | speak | あ、松明が落ちたっぽいな。暗くなってきたで！ | json | 1.376 |
| pair_08 | merged_outline | 43 | speak | 暗いけど、あそこに松明あるみたいやで！ | json | 1.365 |
| pair_08 | location_text | 43 | speak | 急に暗くなった気がするやん！なんかあったんか？ | json | 1.35 |
| pair_08 | location_text | 44 | speak | な、なんか急に暗くなった気がせぇへんか？ | json | 1.37 |
| pair_08 | merged_outline | 44 | speak | あ、あそこに松明あるやん！なんか道になってるみたいやで。 | json | 1.381 |
| pair_09 | merged_outline | 42 | speak | お、空がめっちゃ明るくなってきたな！ | json | 1.276 |
| pair_09 | location_text | 42 | silent |  | json | 1.228 |
| pair_09 | location_text | 43 | speak | わあ、めっちゃ綺麗やな！太陽が昇ってきたで！ | json | 1.349 |
| pair_09 | merged_outline | 43 | speak | わあ、めっちゃ綺麗やな！太陽が昇ってきたんか？ | json | 1.349 |
| pair_09 | merged_outline | 44 | speak | お、なんか急に明るくなった気がするな！ | json | 1.287 |
| pair_09 | location_text | 44 | speak | お、なんか急に明るくなった気がするな！ | json | 1.322 |
| pair_10 | location_text | 42 | speak | あ、なんか向こうの景色変わったみたいやな！ | json | 1.375 |
| pair_10 | merged_outline | 42 | speak | お、なんか向こうの方、地形が変わったみたいやな？ | json | 1.468 |
| pair_10 | merged_outline | 43 | speak | あ、なんか向こうの崖の向こうに新しい景色が見えてきたで！ | json | 1.733 |
| pair_10 | location_text | 43 | speak | お、景色が変わったな！向こうに木がもっと増えてるで！ | json | 1.651 |
| pair_10 | location_text | 44 | speak | あ、なんか向こうの景色が変わった気がするで？ | json | 1.508 |
| pair_10 | merged_outline | 44 | speak | お、なんか向こうの景色が変わったみたいやな？ | json | 1.517 |
| pair_11 | merged_outline | 42 | speak | なんか急に明るなったな？何があったんや？ | fenced_json | 1.555 |
| pair_11 | location_text | 42 | speak | なんか、急に辺りが明るくなった気がするんやけど？ | fenced_json | 1.484 |
| pair_11 | location_text | 43 | speak | なんか、さっきより少し明るくなった気がするな？ | json | 1.537 |
| pair_11 | merged_outline | 43 | speak | なんか急に明るくなった気がするけど、気のせいか？ | json | 1.477 |
| pair_11 | merged_outline | 44 | speak | なんかさっきより少し明るくなった気がするな？ | json | 1.553 |
| pair_11 | location_text | 44 | speak | なんか少し明るくなったか？気のせいかな。 | json | 1.537 |
| pair_12 | location_text | 42 | speak | 急に明るくなったな！なんかあんの？ | json | 1.399 |
| pair_12 | merged_outline | 42 | speak | うわっ、急に明るなったな！何が起きたんや？ | json | 1.335 |
| pair_12 | merged_outline | 43 | speak | うわっ、急に明るなったな！何が起きたんや？ | json | 1.334 |
| pair_12 | location_text | 43 | speak | うわっ、急に明るなったな！何が起きたんや？ | json | 1.376 |
| pair_12 | location_text | 44 | speak | なんか、さっきより少し明るくなった気がするやん？ | json | 1.353 |
| pair_12 | merged_outline | 44 | speak | うわっ、急に明るくなったな！なんか見えてきたで！ | json | 1.333 |
| pair_13 | merged_outline | 42 | speak | うわっ、急に視界が開けたな！ここ、どこや？ | json | 1.372 |
| pair_13 | location_text | 42 | speak | うわっ、急に地上に出た感じやな！ここ、どこや？ | json | 1.456 |
| pair_13 | location_text | 43 | speak | うわっ！急に陸の上に出た感じやん！ここどこや？ | fenced_json | 1.524 |
| pair_13 | merged_outline | 43 | speak | うわっ、急に景色変わったやん！ここはどこや？ | json | 1.382 |
| pair_13 | merged_outline | 44 | speak | うわっ、急に視界が変わったやん！ここはどこや？ | json | 1.361 |
| pair_13 | location_text | 44 | speak | 急に陸の上に出た感じやな！ここはどこや？ | fenced_json | 1.454 |
