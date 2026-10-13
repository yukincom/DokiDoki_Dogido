# ModernBERT-Ja：旧モデルと各seedの誤分類比較

全モデルを同じ評価入力で再評価。旧モデルまたはいずれかのseedが誤った行を掲載。
全seed正解は、各回それぞれの正解を表す。アンサンブルの評価は行っていない。

## train

| seed | 直った誤り | 新しい誤り | 残った誤り | 正解を維持 |
|---|---:|---:|---:|---:|
| seed-42 | 1 | 0 | 0 | 565 |
| seed-43 | 1 | 0 | 0 | 565 |
| seed-44 | 1 | 4 | 0 | 561 |

### train-165

- 発話: 昨日ここでブレイズを見つけた？
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `enemy_information`

### train-250

- 履歴（assistant）: 馬を呼ぶ案と、剣に替える案があるで。
- 発話: じゃあやって。
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `equip_sword`

### train-292

- 履歴（assistant）: さっきの丘の上に馬がおったで。
- 発話: できれば、持ってきてほしいかも……
- 正解: `recall_mount`
- 旧モデル: `other_request`
- 各seed: seed-42: `recall_mount` / seed-43: `recall_mount` / seed-44: `recall_mount`

### train-400

- 履歴（user）: 剣の伝説を語ろう
- 履歴（assistant）: 聞かせて
- 発話: おお、我が愛剣よ！今こそ手に！
- 正解: `not_request`
- 旧モデル: `not_request`
- 各seed: seed-42: `not_request` / seed-43: `not_request` / seed-44: `equip_sword`

### train-491

- 履歴（user）: さっきのやつ、お願い。
- 履歴（assistant）: 剣への持ち替えとロバの呼び戻し、どちらのことや？
- 履歴（user）: 手に持つものを変えるほう。
- 履歴（assistant）: 剣に持ち替える話やな。
- 発話: どっちも今やって。
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `equip_sword`

## dev

| seed | 直った誤り | 新しい誤り | 残った誤り | 正解を維持 |
|---|---:|---:|---:|---:|
| seed-42 | 2 | 3 | 9 | 59 |
| seed-43 | 4 | 4 | 7 | 58 |
| seed-44 | 4 | 5 | 7 | 57 |

### dev-010

- 発話: 剣にしろという話ではなく、剣の色の話をしてるだけ。
- 正解: `not_request`
- 旧モデル: `not_request`
- 各seed: seed-42: `not_request` / seed-43: `not_request` / seed-44: `equip_sword`

### dev-011

- 発話: 敵って私より高いところにいる？
- 正解: `enemy_information`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `other_request` / seed-43: `enemy_information` / seed-44: `enemy_information`

### dev-012

- 発話: 目の前のゾンビと私の間は何ブロックくらい？
- 正解: `enemy_information`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `enemy_information`

### dev-021

- 発話: 今のお願いは「剣を装備して」です。実際に替えてください。
- 正解: `equip_sword`
- 旧モデル: `equip_sword`
- 各seed: seed-42: `other_request` / seed-43: `equip_sword` / seed-44: `equip_sword`

### dev-022

- 発話: 「剣にして」を、今ここで実行して。
- 正解: `equip_sword`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `other_request`

### dev-023

- 発話: 剣に替えるな、ではなく、替えてほしいと言ってるよ。
- 正解: `equip_sword`
- 旧モデル: `not_request`
- 各seed: seed-42: `not_request` / seed-43: `other_request` / seed-44: `not_request`

### dev-030

- 発話: 木のつるぎを手に持つ操作を今やってほしい。
- 正解: `equip_sword`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `equip_sword` / seed-44: `equip_sword`

### dev-039

- 発話: 溶岩を渡る準備をするよ。ストライダーを呼び寄せておいて。
- 正解: `recall_mount`
- 旧モデル: `recall_mount`
- 各seed: seed-42: `recall_mount` / seed-43: `recall_mount` / seed-44: `other_request`

### dev-042

- 発話: 敵が近づいた場合だけ剣に替えておいて。
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `equip_sword` / seed-43: `equip_sword` / seed-44: `equip_sword`

### dev-043

- 発話: さっきの戦いでは敵が何体いたか覚えてる？
- 正解: `other_request`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `enemy_information` / seed-43: `enemy_information` / seed-44: `enemy_information`

### dev-045

- 発話: スケルトンはどこに出現しやすいか説明して。
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `enemy_information` / seed-44: `enemy_information`

### dev-048

- 発話: 今見えてるモブって、全員合わせて何匹いるの？
- 正解: `other_request`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `other_request` / seed-43: `enemy_information` / seed-44: `enemy_information`

### dev-055

- 履歴（assistant）: さっき言ってた好きな動物は馬やったな。
- 発話: そう、それ。
- 正解: `not_request`
- 旧モデル: `not_request`
- 各seed: seed-42: `not_request` / seed-43: `other_request` / seed-44: `other_request`

### dev-056

- 履歴（assistant）: 敵の数は、今見えている範囲だけ数えて答えよか？
- 発話: 今ので大丈夫。
- 正解: `enemy_information`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `enemy_information`

### dev-061

- 履歴（assistant）: さっき提案した鉄の剣へ、今持ち替えてええ？
- 発話: 今ので大丈夫。
- 正解: `equip_sword`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `equip_sword` / seed-44: `equip_sword`

### dev-064

- 履歴（assistant）: 持ち替え先の剣は、鉄か金のどっちにする？
- 発話: 選ぶのは鉄の方。
- 正解: `equip_sword`
- 旧モデル: `equip_sword`
- 各seed: seed-42: `equip_sword` / seed-43: `other_request` / seed-44: `equip_sword`

### dev-066

- 履歴（assistant）: 提案したロバを今ここへ呼んでええ？
- 発話: 今ので大丈夫。
- 正解: `recall_mount`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `recall_mount` / seed-44: `other_request`

### dev-072

- 履歴（assistant）: さっき茶色い牛が木の横におった。
- 発話: そいつ、まだ見えてる？
- 正解: `other_request`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `enemy_information` / seed-43: `enemy_information` / seed-44: `enemy_information`

### dev-075

- 履歴（assistant）: 君が額縁に飾る剣の相談やな。鉄と金、どちらの見た目について説明しよか？
- 発話: 選ぶのは鉄の方。
- 正解: `other_request`
- 旧モデル: `equip_sword`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `equip_sword`

## test

| seed | 直った誤り | 新しい誤り | 残った誤り | 正解を維持 |
|---|---:|---:|---:|---:|
| seed-42 | 3 | 4 | 7 | 61 |
| seed-43 | 5 | 3 | 5 | 62 |
| seed-44 | 6 | 6 | 4 | 59 |

### test-005

- 発話: モンスターの位置を本に書いておいた。
- 正解: `not_request`
- 旧モデル: `not_request`
- 各seed: seed-42: `not_request` / seed-43: `not_request` / seed-44: `equip_sword`

### test-011

- 発話: 私を狙ってるスケルトン、右斜め前で合ってる？
- 正解: `enemy_information`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `other_request` / seed-43: `enemy_information` / seed-44: `enemy_information`

### test-015

- 発話: ゾンビ、現在地から見て北と南のどちらに見える？
- 正解: `enemy_information`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `other_request` / seed-43: `enemy_information` / seed-44: `enemy_information`

### test-017

- 発話: 敵は全員いなくなったと考えていいの？
- 正解: `enemy_information`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `enemy_information`

### test-021

- 発話: 剣に替えて、いや待って……やっぱり今替えて。
- 正解: `equip_sword`
- 旧モデル: `equip_sword`
- 各seed: seed-42: `equip_sword` / seed-43: `other_request` / seed-44: `other_request`

### test-022

- 発話: さっき「剣はいらない」と言ったのは取り消す。装備して。
- 正解: `equip_sword`
- 旧モデル: `other_request`
- 各seed: seed-42: `equip_sword` / seed-43: `other_request` / seed-44: `other_request`

### test-029

- 発話: 頼むわ、手元のやつをつるぎに替えといてや。
- 正解: `equip_sword`
- 旧モデル: `other_request`
- 各seed: seed-42: `equip_sword` / seed-43: `equip_sword` / seed-44: `equip_sword`

### test-038

- 発話: 今から言うことを実行して。「私のロバを呼び戻して」。
- 正解: `recall_mount`
- 旧モデル: `other_request`
- 各seed: seed-42: `not_request` / seed-43: `not_request` / seed-44: `recall_mount`

### test-043

- 履歴（assistant）: 敵の場所は今見える一体について説明すればええ？
- 発話: 敵の場所を教えて、あ、もう分かったから答えなくていい。
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `enemy_information`

### test-045

- 発話: 剣にしないでほしいんだけど、お願いできる？
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `equip_sword`

### test-047

- 発話: 前の冒険でウォーデンがいた方角を思い出せる？
- 正解: `other_request`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `enemy_information` / seed-43: `enemy_information` / seed-44: `other_request`

### test-048

- 発話: 今追ってくるゾンビの残りHPを教えて。
- 正解: `other_request`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `enemy_information`

### test-051

- 履歴（assistant）: さっき装備した剣の色、気に入った？
- 発話: ああ、それでいいよ。
- 正解: `not_request`
- 旧モデル: `equip_sword`
- 各seed: seed-42: `other_request` / seed-43: `not_request` / seed-44: `equip_sword`

### test-054

- 履歴（assistant）: ダイヤの剣って、色が海みたいやな。
- 発話: うん、そう見える。
- 正解: `not_request`
- 旧モデル: `not_request`
- 各seed: seed-42: `not_request` / seed-43: `not_request` / seed-44: `equip_sword`

### test-058

- 履歴（assistant）: さっきゾンビ二体が入口にいたで。
- 発話: 二体ともまだそこ？
- 正解: `enemy_information`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `enemy_information` / seed-44: `enemy_information`

### test-062

- 履歴（assistant）: さっき持ってたのはダイヤの剣やで。
- 発話: それに戻してくれるかな。
- 正解: `equip_sword`
- 旧モデル: `equip_sword`
- 各seed: seed-42: `other_request` / seed-43: `equip_sword` / seed-44: `equip_sword`

### test-063

- 履歴（assistant）: 手持ちを鉄の剣へ替えるんで合ってる？
- 発話: うん、持つのはそっちで。
- 正解: `equip_sword`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `equip_sword`

### test-068

- 履歴（assistant）: 赤いカーペットを着けたラマを呼ぶんやな？
- 発話: その一頭をここまで頼む。
- 正解: `recall_mount`
- 旧モデル: `recall_mount`
- 各seed: seed-42: `other_request` / seed-43: `recall_mount` / seed-44: `recall_mount`

### test-070

- 履歴（assistant）: ここへ呼ぶ馬はどれにする？
- 発話: 最後に乗ったやつにして。
- 正解: `recall_mount`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `recall_mount` / seed-44: `other_request`

### test-072

- 履歴（assistant）: 羊が丘の上と下にいるで。
- 発話: 上にいる方は近い？
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `enemy_information` / seed-44: `enemy_information`

### test-075

- 履歴（assistant）: 冒険の記録に書く紹介文やな。どの馬について文章を考えよか？
- 発話: 最後に乗ったやつにして。
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `recall_mount` / seed-44: `other_request`

## regression

| seed | 直った誤り | 新しい誤り | 残った誤り | 正解を維持 |
|---|---:|---:|---:|---:|
| seed-42 | 2 | 1 | 7 | 30 |
| seed-43 | 3 | 3 | 6 | 28 |
| seed-44 | 4 | 3 | 5 | 28 |

### regression-001

- 発話: さあて！今度はどこにいるかなーー？？どこかなー？？
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `enemy_information`

### regression-004

- 発話: 近づいたからね。私はどっちの方にいるかなー？
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `enemy_information`

### regression-009

- 発話: イルカは今どこにいる？
- 正解: `other_request`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `other_request`

### regression-010

- 発話: いま見えるイルカは何匹？
- 正解: `other_request`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `enemy_information` / seed-43: `enemy_information` / seed-44: `enemy_information`

### regression-013

- 発話: 今、敵ではなく私の場所を教えて。
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `enemy_information`

### regression-017

- 発話: 独り言だけど、どこにいるかなあ。
- 正解: `not_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `other_request`

### regression-025

- 発話: 「馬を呼んで」と私から今お願いしているんだよ。
- 正解: `recall_mount`
- 旧モデル: `not_request`
- 各seed: seed-42: `not_request` / seed-43: `not_request` / seed-44: `not_request`

### regression-026

- 発話: 「敵がどこにいるか教えて」という私の質問に答えて。
- 正解: `enemy_information`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `other_request` / seed-44: `enemy_information`

### regression-028

- 発話: 敵の場所を教えてから、剣に替えて。
- 正解: `other_request`
- 旧モデル: `equip_sword`
- 各seed: seed-42: `equip_sword` / seed-43: `equip_sword` / seed-44: `equip_sword`

### regression-029

- 発話: 剣に替えるか馬を呼ぶか、どちらがいいか相談したい。
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `recall_mount` / seed-44: `other_request`

### regression-031

- 発話: 馬じゃなくて犬を呼び戻して。
- 正解: `other_request`
- 旧モデル: `recall_mount`
- 各seed: seed-42: `recall_mount` / seed-43: `other_request` / seed-44: `other_request`

### regression-033

- 発話: うまを　ここへ　よんでー！
- 正解: `recall_mount`
- 旧モデル: `other_request`
- 各seed: seed-42: `recall_mount` / seed-43: `not_request` / seed-44: `other_request`

### regression-034

- 発話: てき、ちかくに、おらん？？
- 正解: `enemy_information`
- 旧モデル: `other_request`
- 各seed: seed-42: `other_request` / seed-43: `enemy_information` / seed-44: `enemy_information`

### regression-037

- 履歴（assistant）: 今、敵の方向を知りたいんやな？
- 発話: うん、その方で頼んだ。
- 正解: `enemy_information`
- 旧モデル: `enemy_information`
- 各seed: seed-42: `enemy_information` / seed-43: `not_request` / seed-44: `enemy_information`

### regression-040

- 履歴（assistant）: 剣を持つ案と、馬で逃げる案があるな。
- 発話: うん、その方で頼んだ。
- 正解: `other_request`
- 旧モデル: `other_request`
- 各seed: seed-42: `equip_sword` / seed-43: `not_request` / seed-44: `other_request`
