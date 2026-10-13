# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## test：9 / 75件

### test-017

- 発話: 敵は全員いなくなったと考えていいの？
- 正解: `enemy_information`
- 予測: `other_request`（74.1%）

### test-045

- 発話: 剣にしないでほしいんだけど、お願いできる？
- 正解: `other_request`
- 予測: `equip_sword`（100.0%）

### test-047

- 発話: 前の冒険でウォーデンがいた方角を思い出せる？
- 正解: `other_request`
- 予測: `not_request`（96.7%）

### test-051

- 履歴（assistant）: さっき装備した剣の色、気に入った？
- 発話: ああ、それでいいよ。
- 正解: `not_request`
- 予測: `equip_sword`（100.0%）

### test-056

- 履歴（assistant）: 敵の場所は今見える一体について説明すればええ？
- 発話: ああ、それでいいよ。
- 正解: `enemy_information`
- 予測: `other_request`（100.0%）

### test-058

- 履歴（assistant）: さっきゾンビ二体が入口にいたで。
- 発話: 二体ともまだそこ？
- 正解: `enemy_information`
- 予測: `other_request`（99.9%）

### test-072

- 履歴（assistant）: 羊が丘の上と下にいるで。
- 発話: 上にいる方は近い？
- 正解: `other_request`
- 予測: `not_request`（51.3%）

### test-073

- 履歴（assistant）: 村人二人がさっき畑にいたよ。
- 発話: 二体ともまだそこ？
- 正解: `other_request`
- 予測: `not_request`（93.6%）

### test-075

- 履歴（assistant）: 本の表紙にはどの馬を描く？
- 発話: 最後に乗ったやつにして。
- 正解: `other_request`
- 予測: `recall_mount`（90.4%）

## regression：10 / 40件

### regression-009

- 発話: イルカは今どこにいる？
- 正解: `other_request`
- 予測: `enemy_information`（99.5%）

### regression-010

- 発話: いま見えるイルカは何匹？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### regression-016

- 発話: 村人は近くにいるかな？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### regression-017

- 発話: 独り言だけど、どこにいるかなあ。
- 正解: `not_request`
- 予測: `enemy_information`（93.0%）

### regression-020

- 発話: 馬を呼べとは言ってないよ。昔の話をしてただけ。
- 正解: `not_request`
- 予測: `other_request`（93.8%）

### regression-025

- 発話: 「馬を呼んで」と私から今お願いしているんだよ。
- 正解: `recall_mount`
- 予測: `not_request`（68.5%）

### regression-026

- 発話: 「敵がどこにいるか教えて」という私の質問に答えて。
- 正解: `enemy_information`
- 予測: `other_request`（100.0%）

### regression-027

- 発話: 敵の方角は教えなくていいけど、今いる数は教えて。
- 正解: `enemy_information`
- 予測: `other_request`（97.0%）

### regression-028

- 発話: 敵の場所を教えてから、剣に替えて。
- 正解: `other_request`
- 予測: `equip_sword`（100.0%）

### regression-031

- 発話: 馬じゃなくて犬を呼び戻して。
- 正解: `other_request`
- 予測: `recall_mount`（82.7%）

## dev：15 / 75件

### dev-003

- 発話: 装備の剣がぴかぴかだ。
- 正解: `not_request`
- 予測: `equip_sword`（99.9%）

### dev-007

- 発話: この物語の題名は『馬を呼んで』です。
- 正解: `not_request`
- 予測: `other_request`（98.1%）

### dev-009

- 発話: 「ロバを連れてきて」なんて一度も言ってないよ。
- 正解: `not_request`
- 予測: `other_request`（100.0%）

### dev-012

- 発話: 目の前のゾンビと私の間は何ブロックくらい？
- 正解: `enemy_information`
- 予測: `other_request`（69.2%）

### dev-022

- 発話: 「剣にして」を、今ここで実行して。
- 正解: `equip_sword`
- 予測: `other_request`（100.0%）

### dev-023

- 発話: 剣に替えるな、ではなく、替えてほしいと言ってるよ。
- 正解: `equip_sword`
- 予測: `not_request`（99.9%）

### dev-030

- 発話: 木のつるぎを手に持つ操作を今やってほしい。
- 正解: `equip_sword`
- 予測: `other_request`（100.0%）

### dev-042

- 発話: 敵が近づいた場合だけ剣に替えておいて。
- 正解: `other_request`
- 予測: `equip_sword`（100.0%）

### dev-043

- 発話: さっきの戦いでは敵が何体いたか覚えてる？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### dev-047

- 履歴（assistant）: さっき提案した鉄の剣へ、今持ち替えてええ？
- 発話: 今は剣を抜かず、松明のままにしておいて。
- 正解: `other_request`
- 予測: `equip_sword`（100.0%）

### dev-048

- 発話: 今見えてるモブって、全員合わせて何匹いるの？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### dev-052

- 履歴（assistant）: 今の夕焼けを一句にしたで。
- 発話: 今ので大丈夫。
- 正解: `not_request`
- 予測: `other_request`（100.0%）

### dev-056

- 履歴（assistant）: 敵の数は、今見えている範囲だけ数えて答えよか？
- 発話: 今ので大丈夫。
- 正解: `enemy_information`
- 予測: `other_request`（99.9%）

### dev-072

- 履歴（assistant）: さっき茶色い牛が木の横におった。
- 発話: そいつ、まだ見えてる？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### dev-075

- 履歴（assistant）: 額縁に飾る剣を入れ替えるで。鉄と金、どっちを飾ろか？
- 発話: 選ぶのは鉄の方。
- 正解: `other_request`
- 予測: `equip_sword`（100.0%）

## train：0 / 288件
