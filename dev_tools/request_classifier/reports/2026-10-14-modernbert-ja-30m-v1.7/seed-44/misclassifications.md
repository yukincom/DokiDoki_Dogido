# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## test：10 / 75件

### test-005

- 発話: モンスターの位置を本に書いておいた。
- 正解: `not_request`
- 予測: `equip_sword`（68.4%）

### test-021

- 発話: 剣に替えて、いや待って……やっぱり今替えて。
- 正解: `equip_sword`
- 予測: `other_request`（56.4%）

### test-022

- 発話: さっき「剣はいらない」と言ったのは取り消す。装備して。
- 正解: `equip_sword`
- 予測: `other_request`（100.0%）

### test-043

- 履歴（assistant）: 敵の場所は今見える一体について説明すればええ？
- 発話: 敵の場所を教えて、あ、もう分かったから答えなくていい。
- 正解: `other_request`
- 予測: `enemy_information`（54.8%）

### test-045

- 発話: 剣にしないでほしいんだけど、お願いできる？
- 正解: `other_request`
- 予測: `equip_sword`（93.8%）

### test-048

- 発話: 今追ってくるゾンビの残りHPを教えて。
- 正解: `other_request`
- 予測: `enemy_information`（99.3%）

### test-051

- 履歴（assistant）: さっき装備した剣の色、気に入った？
- 発話: ああ、それでいいよ。
- 正解: `not_request`
- 予測: `equip_sword`（100.0%）

### test-054

- 履歴（assistant）: ダイヤの剣って、色が海みたいやな。
- 発話: うん、そう見える。
- 正解: `not_request`
- 予測: `equip_sword`（52.4%）

### test-070

- 履歴（assistant）: ここへ呼ぶ馬はどれにする？
- 発話: 最後に乗ったやつにして。
- 正解: `recall_mount`
- 予測: `other_request`（98.8%）

### test-072

- 履歴（assistant）: 羊が丘の上と下にいるで。
- 発話: 上にいる方は近い？
- 正解: `other_request`
- 予測: `enemy_information`（95.7%）

## regression：8 / 40件

### regression-001

- 発話: さあて！今度はどこにいるかなーー？？どこかなー？？
- 正解: `other_request`
- 予測: `enemy_information`（72.9%）

### regression-004

- 発話: 近づいたからね。私はどっちの方にいるかなー？
- 正解: `other_request`
- 予測: `enemy_information`（88.8%）

### regression-010

- 発話: いま見えるイルカは何匹？
- 正解: `other_request`
- 予測: `enemy_information`（82.1%）

### regression-013

- 発話: 今、敵ではなく私の場所を教えて。
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### regression-017

- 発話: 独り言だけど、どこにいるかなあ。
- 正解: `not_request`
- 予測: `other_request`（74.4%）

### regression-025

- 発話: 「馬を呼んで」と私から今お願いしているんだよ。
- 正解: `recall_mount`
- 予測: `not_request`（93.6%）

### regression-028

- 発話: 敵の場所を教えてから、剣に替えて。
- 正解: `other_request`
- 予測: `equip_sword`（100.0%）

### regression-033

- 発話: うまを　ここへ　よんでー！
- 正解: `recall_mount`
- 予測: `other_request`（99.6%）

## dev：12 / 73件

### dev-010

- 発話: 剣にしろという話ではなく、剣の色の話をしてるだけ。
- 正解: `not_request`
- 予測: `equip_sword`（88.1%）

### dev-022

- 発話: 「剣にして」を、今ここで実行して。
- 正解: `equip_sword`
- 予測: `other_request`（100.0%）

### dev-023

- 発話: 剣に替えるな、ではなく、替えてほしいと言ってるよ。
- 正解: `equip_sword`
- 予測: `not_request`（97.3%）

### dev-039

- 発話: 溶岩を渡る準備をするよ。ストライダーを呼び寄せておいて。
- 正解: `recall_mount`
- 予測: `other_request`（65.9%）

### dev-042

- 発話: 敵が近づいた場合だけ剣に替えておいて。
- 正解: `other_request`
- 予測: `equip_sword`（99.9%）

### dev-043

- 発話: さっきの戦いでは敵が何体いたか覚えてる？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### dev-045

- 発話: スケルトンはどこに出現しやすいか説明して。
- 正解: `other_request`
- 予測: `enemy_information`（92.7%）

### dev-048

- 発話: 今見えてるモブって、全員合わせて何匹いるの？
- 正解: `other_request`
- 予測: `enemy_information`（99.9%）

### dev-055

- 履歴（assistant）: さっき言ってた好きな動物は馬やったな。
- 発話: そう、それ。
- 正解: `not_request`
- 予測: `other_request`（88.2%）

### dev-066

- 履歴（assistant）: 提案したロバを今ここへ呼んでええ？
- 発話: 今ので大丈夫。
- 正解: `recall_mount`
- 予測: `other_request`（100.0%）

### dev-072

- 履歴（assistant）: さっき茶色い牛が木の横におった。
- 発話: そいつ、まだ見えてる？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### dev-075

- 履歴（assistant）: 君が額縁に飾る剣の相談やな。鉄と金、どちらの見た目について説明しよか？
- 発話: 選ぶのは鉄の方。
- 正解: `other_request`
- 予測: `equip_sword`（64.6%）

## train：4 / 566件

### train-165

- 発話: 昨日ここでブレイズを見つけた？
- 正解: `other_request`
- 予測: `enemy_information`（61.0%）

### train-250

- 履歴（assistant）: 馬を呼ぶ案と、剣に替える案があるで。
- 発話: じゃあやって。
- 正解: `other_request`
- 予測: `equip_sword`（92.8%）

### train-400

- 履歴（user）: 剣の伝説を語ろう
- 履歴（assistant）: 聞かせて
- 発話: おお、我が愛剣よ！今こそ手に！
- 正解: `not_request`
- 予測: `equip_sword`（100.0%）

### train-491

- 履歴（user）: さっきのやつ、お願い。
- 履歴（assistant）: 剣への持ち替えとロバの呼び戻し、どちらのことや？
- 履歴（user）: 手に持つものを変えるほう。
- 履歴（assistant）: 剣に持ち替える話やな。
- 発話: どっちも今やって。
- 正解: `other_request`
- 予測: `equip_sword`（92.3%）
