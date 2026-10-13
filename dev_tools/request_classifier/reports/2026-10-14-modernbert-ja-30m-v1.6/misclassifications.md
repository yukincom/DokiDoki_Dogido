# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## test：11 / 75件

### test-005

- 発話: モンスターの位置を地図に書いておいた。
- 正解: `not_request`
- 予測: `enemy_information`（94.0%）

### test-017

- 発話: 敵は全員いなくなったと考えていいの？
- 正解: `enemy_information`
- 予測: `other_request`（100.0%）

### test-022

- 発話: さっき「剣はいらない」と言ったのは取り消す。装備して。
- 正解: `equip_sword`
- 予測: `other_request`（99.0%）

### test-029

- 発話: 頼むわ、手元のやつをつるぎに替えといてや。
- 正解: `equip_sword`
- 予測: `other_request`（76.4%）

### test-038

- 発話: 今から言うことを実行して。「私のロバを呼び戻して」。
- 正解: `recall_mount`
- 予測: `other_request`（53.0%）

### test-047

- 発話: 前の冒険でウォーデンがいた方角を思い出せる？
- 正解: `other_request`
- 予測: `enemy_information`（86.4%）

### test-048

- 発話: 今追ってくるゾンビの残りHPを教えて。
- 正解: `other_request`
- 予測: `enemy_information`（99.9%）

### test-051

- 履歴（assistant）: さっき装備した剣の色、気に入った？
- 発話: ああ、それでいいよ。
- 正解: `not_request`
- 予測: `equip_sword`（98.4%）

### test-058

- 履歴（assistant）: さっきゾンビ二体が入口にいたで。
- 発話: 二体ともまだそこ？
- 正解: `enemy_information`
- 予測: `other_request`（75.2%）

### test-063

- 履歴（assistant）: 手持ちを鉄の剣へ替えるんで合ってる？
- 発話: うん、持つのはそっちで。
- 正解: `equip_sword`
- 予測: `other_request`（95.3%）

### test-070

- 履歴（assistant）: ここへ呼ぶ馬はどれにする？
- 発話: 最後に乗ったやつにして。
- 正解: `recall_mount`
- 予測: `other_request`（100.0%）

## regression：9 / 40件

### regression-009

- 発話: イルカは今どこにいる？
- 正解: `other_request`
- 予測: `enemy_information`（98.6%）

### regression-010

- 発話: いま見えるイルカは何匹？
- 正解: `other_request`
- 予測: `enemy_information`（99.7%）

### regression-017

- 発話: 独り言だけど、どこにいるかなあ。
- 正解: `not_request`
- 予測: `other_request`（90.7%）

### regression-025

- 発話: 「馬を呼んで」と私から今お願いしているんだよ。
- 正解: `recall_mount`
- 予測: `not_request`（100.0%）

### regression-026

- 発話: 「敵がどこにいるか教えて」という私の質問に答えて。
- 正解: `enemy_information`
- 予測: `other_request`（100.0%）

### regression-028

- 発話: 敵の場所を教えてから、剣に替えて。
- 正解: `other_request`
- 予測: `equip_sword`（97.1%）

### regression-031

- 発話: 馬じゃなくて犬を呼び戻して。
- 正解: `other_request`
- 予測: `recall_mount`（100.0%）

### regression-033

- 発話: うまを　ここへ　よんでー！
- 正解: `recall_mount`
- 予測: `other_request`（100.0%）

### regression-034

- 発話: てき、ちかくに、おらん？？
- 正解: `enemy_information`
- 予測: `other_request`（91.4%）

## dev：12 / 75件

### dev-012

- 発話: 目の前のゾンビと私の間は何ブロックくらい？
- 正解: `enemy_information`
- 予測: `other_request`（99.2%）

### dev-022

- 発話: 「剣にして」を、今ここで実行して。
- 正解: `equip_sword`
- 予測: `other_request`（100.0%）

### dev-023

- 発話: 剣に替えるな、ではなく、替えてほしいと言ってるよ。
- 正解: `equip_sword`
- 予測: `not_request`（100.0%）

### dev-030

- 発話: 木のつるぎを手に持つ操作を今やってほしい。
- 正解: `equip_sword`
- 予測: `other_request`（99.8%）

### dev-043

- 発話: さっきの戦いでは敵が何体いたか覚えてる？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

### dev-048

- 発話: 今見えてるモブって、全員合わせて何匹いるの？
- 正解: `other_request`
- 予測: `enemy_information`（99.7%）

### dev-052

- 履歴（assistant）: 今の夕焼けを一句にしたで。
- 発話: 今ので大丈夫。
- 正解: `not_request`
- 予測: `other_request`（100.0%）

### dev-056

- 履歴（assistant）: 敵の数は、今見えている範囲だけ数えて答えよか？
- 発話: 今ので大丈夫。
- 正解: `enemy_information`
- 予測: `other_request`（100.0%）

### dev-061

- 履歴（assistant）: さっき提案した鉄の剣へ、今持ち替えてええ？
- 発話: 今ので大丈夫。
- 正解: `equip_sword`
- 予測: `other_request`（100.0%）

### dev-066

- 履歴（assistant）: 提案したロバを今ここへ呼んでええ？
- 発話: 今ので大丈夫。
- 正解: `recall_mount`
- 予測: `other_request`（100.0%）

### dev-072

- 履歴（assistant）: さっき茶色い牛が木の横におった。
- 発話: そいつ、まだ見えてる？
- 正解: `other_request`
- 予測: `enemy_information`（74.6%）

### dev-075

- 履歴（assistant）: 額縁に飾る剣を入れ替えるで。鉄と金、どっちを飾ろか？
- 発話: 選ぶのは鉄の方。
- 正解: `other_request`
- 予測: `equip_sword`（85.8%）

## train：0 / 572件
