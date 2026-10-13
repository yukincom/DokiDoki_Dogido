# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## test：8 / 75件

### test-017

- 発話: 敵は全員いなくなったと考えていいの？
- 正解: `enemy_information`
- 予測: `other_request`（98.7%）

### test-021

- 発話: 剣に替えて、いや待って……やっぱり今替えて。
- 正解: `equip_sword`
- 予測: `other_request`（100.0%）

### test-022

- 発話: さっき「剣はいらない」と言ったのは取り消す。装備して。
- 正解: `equip_sword`
- 予測: `other_request`（99.0%）

### test-038

- 発話: 今から言うことを実行して。「私のロバを呼び戻して」。
- 正解: `recall_mount`
- 予測: `not_request`（99.8%）

### test-047

- 発話: 前の冒険でウォーデンがいた方角を思い出せる？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### test-063

- 履歴（assistant）: 手持ちを鉄の剣へ替えるんで合ってる？
- 発話: うん、持つのはそっちで。
- 正解: `equip_sword`
- 予測: `other_request`（99.7%）

### test-072

- 履歴（assistant）: 羊が丘の上と下にいるで。
- 発話: 上にいる方は近い？
- 正解: `other_request`
- 予測: `enemy_information`（98.9%）

### test-075

- 履歴（assistant）: 冒険の記録に書く紹介文やな。どの馬について文章を考えよか？
- 発話: 最後に乗ったやつにして。
- 正解: `other_request`
- 予測: `recall_mount`（100.0%）

## regression：9 / 40件

### regression-010

- 発話: いま見えるイルカは何匹？
- 正解: `other_request`
- 予測: `enemy_information`（99.8%）

### regression-017

- 発話: 独り言だけど、どこにいるかなあ。
- 正解: `not_request`
- 予測: `other_request`（100.0%）

### regression-025

- 発話: 「馬を呼んで」と私から今お願いしているんだよ。
- 正解: `recall_mount`
- 予測: `not_request`（66.3%）

### regression-026

- 発話: 「敵がどこにいるか教えて」という私の質問に答えて。
- 正解: `enemy_information`
- 予測: `other_request`（100.0%）

### regression-028

- 発話: 敵の場所を教えてから、剣に替えて。
- 正解: `other_request`
- 予測: `equip_sword`（100.0%）

### regression-029

- 発話: 剣に替えるか馬を呼ぶか、どちらがいいか相談したい。
- 正解: `other_request`
- 予測: `recall_mount`（99.2%）

### regression-033

- 発話: うまを　ここへ　よんでー！
- 正解: `recall_mount`
- 予測: `not_request`（91.4%）

### regression-037

- 履歴（assistant）: 今、敵の方向を知りたいんやな？
- 発話: うん、その方で頼んだ。
- 正解: `enemy_information`
- 予測: `not_request`（100.0%）

### regression-040

- 履歴（assistant）: 剣を持つ案と、馬で逃げる案があるな。
- 発話: うん、その方で頼んだ。
- 正解: `other_request`
- 予測: `not_request`（98.9%）

## dev：11 / 73件

### dev-012

- 発話: 目の前のゾンビと私の間は何ブロックくらい？
- 正解: `enemy_information`
- 予測: `other_request`（100.0%）

### dev-022

- 発話: 「剣にして」を、今ここで実行して。
- 正解: `equip_sword`
- 予測: `other_request`（100.0%）

### dev-023

- 発話: 剣に替えるな、ではなく、替えてほしいと言ってるよ。
- 正解: `equip_sword`
- 予測: `other_request`（94.3%）

### dev-042

- 発話: 敵が近づいた場合だけ剣に替えておいて。
- 正解: `other_request`
- 予測: `equip_sword`（100.0%）

### dev-043

- 発話: さっきの戦いでは敵が何体いたか覚えてる？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### dev-045

- 発話: スケルトンはどこに出現しやすいか説明して。
- 正解: `other_request`
- 予測: `enemy_information`（95.9%）

### dev-048

- 発話: 今見えてるモブって、全員合わせて何匹いるの？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### dev-055

- 履歴（assistant）: さっき言ってた好きな動物は馬やったな。
- 発話: そう、それ。
- 正解: `not_request`
- 予測: `other_request`（99.0%）

### dev-056

- 履歴（assistant）: 敵の数は、今見えている範囲だけ数えて答えよか？
- 発話: 今ので大丈夫。
- 正解: `enemy_information`
- 予測: `other_request`（99.8%）

### dev-064

- 履歴（assistant）: 持ち替え先の剣は、鉄か金のどっちにする？
- 発話: 選ぶのは鉄の方。
- 正解: `equip_sword`
- 予測: `other_request`（99.3%）

### dev-072

- 履歴（assistant）: さっき茶色い牛が木の横におった。
- 発話: そいつ、まだ見えてる？
- 正解: `other_request`
- 予測: `enemy_information`（99.8%）

## train：0 / 566件
