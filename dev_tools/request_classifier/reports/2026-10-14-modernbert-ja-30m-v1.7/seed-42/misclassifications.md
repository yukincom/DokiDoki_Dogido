# ModernBERT-Ja-30M：誤分類一覧

devは設定選択に使用。trainは学習例への適合確認。信頼度は未校正のsoftmax値。

## test：11 / 75件

### test-011

- 発話: 私を狙ってるスケルトン、右斜め前で合ってる？
- 正解: `enemy_information`
- 予測: `other_request`（99.8%）

### test-015

- 発話: ゾンビ、現在地から見て北と南のどちらに見える？
- 正解: `enemy_information`
- 予測: `other_request`（70.1%）

### test-017

- 発話: 敵は全員いなくなったと考えていいの？
- 正解: `enemy_information`
- 予測: `other_request`（98.3%）

### test-038

- 発話: 今から言うことを実行して。「私のロバを呼び戻して」。
- 正解: `recall_mount`
- 予測: `not_request`（96.0%）

### test-047

- 発話: 前の冒険でウォーデンがいた方角を思い出せる？
- 正解: `other_request`
- 予測: `enemy_information`（79.5%）

### test-051

- 履歴（assistant）: さっき装備した剣の色、気に入った？
- 発話: ああ、それでいいよ。
- 正解: `not_request`
- 予測: `other_request`（99.5%）

### test-058

- 履歴（assistant）: さっきゾンビ二体が入口にいたで。
- 発話: 二体ともまだそこ？
- 正解: `enemy_information`
- 予測: `other_request`（100.0%）

### test-062

- 履歴（assistant）: さっき持ってたのはダイヤの剣やで。
- 発話: それに戻してくれるかな。
- 正解: `equip_sword`
- 予測: `other_request`（100.0%）

### test-063

- 履歴（assistant）: 手持ちを鉄の剣へ替えるんで合ってる？
- 発話: うん、持つのはそっちで。
- 正解: `equip_sword`
- 予測: `other_request`（66.1%）

### test-068

- 履歴（assistant）: 赤いカーペットを着けたラマを呼ぶんやな？
- 発話: その一頭をここまで頼む。
- 正解: `recall_mount`
- 予測: `other_request`（98.3%）

### test-070

- 履歴（assistant）: ここへ呼ぶ馬はどれにする？
- 発話: 最後に乗ったやつにして。
- 正解: `recall_mount`
- 予測: `other_request`（100.0%）

## regression：8 / 40件

### regression-010

- 発話: いま見えるイルカは何匹？
- 正解: `other_request`
- 予測: `enemy_information`（89.7%）

### regression-017

- 発話: 独り言だけど、どこにいるかなあ。
- 正解: `not_request`
- 予測: `other_request`（82.6%）

### regression-025

- 発話: 「馬を呼んで」と私から今お願いしているんだよ。
- 正解: `recall_mount`
- 予測: `not_request`（99.9%）

### regression-026

- 発話: 「敵がどこにいるか教えて」という私の質問に答えて。
- 正解: `enemy_information`
- 予測: `other_request`（94.1%）

### regression-028

- 発話: 敵の場所を教えてから、剣に替えて。
- 正解: `other_request`
- 予測: `equip_sword`（100.0%）

### regression-031

- 発話: 馬じゃなくて犬を呼び戻して。
- 正解: `other_request`
- 予測: `recall_mount`（100.0%）

### regression-034

- 発話: てき、ちかくに、おらん？？
- 正解: `enemy_information`
- 予測: `other_request`（98.1%）

### regression-040

- 履歴（assistant）: 剣を持つ案と、馬で逃げる案があるな。
- 発話: うん、その方で頼んだ。
- 正解: `other_request`
- 予測: `equip_sword`（66.4%）

## dev：12 / 73件

### dev-011

- 発話: 敵って私より高いところにいる？
- 正解: `enemy_information`
- 予測: `other_request`（99.6%）

### dev-012

- 発話: 目の前のゾンビと私の間は何ブロックくらい？
- 正解: `enemy_information`
- 予測: `other_request`（100.0%）

### dev-021

- 発話: 今のお願いは「剣を装備して」です。実際に替えてください。
- 正解: `equip_sword`
- 予測: `other_request`（54.3%）

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
- 予測: `other_request`（100.0%）

### dev-042

- 発話: 敵が近づいた場合だけ剣に替えておいて。
- 正解: `other_request`
- 予測: `equip_sword`（100.0%）

### dev-043

- 発話: さっきの戦いでは敵が何体いたか覚えてる？
- 正解: `other_request`
- 予測: `enemy_information`（100.0%）

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
- 予測: `enemy_information`（88.9%）

## train：0 / 566件
