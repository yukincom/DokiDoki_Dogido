# ModernBERT-Ja v1.3 → v1.6：誤分類の変化

同一のdev 75件・test 75件・regression 40件を、ID・本文・履歴・正解の完全一致を確認して比較。devはモデル選択に使った分割。trainは含めない。

分類名：`not_request`＝依頼でない、`enemy_information`＝現在の敵情報、`equip_sword`＝今の剣持ち替え、`recall_mount`＝今の乗り物呼び戻し、`other_request`＝その他の依頼。

「誤り継続」は新旧とも不正解で、誤った分類先が変わった場合も含む。正解継続例のIDは[comparison.json](comparison.json)、今回の誤分類だけは[misclassifications.md](misclassifications.md)を参照。確率は未校正。

## test：66/75 → 64/75

改善 5件／新しい誤り 7件／誤り継続 4件／正解継続 59件。

### 改善（5件）

#### test-045

- 発話: 剣にしないでほしいんだけど、お願いできる？
- 正解: `other_request`
- 前回: `equip_sword` → 今回: `other_request`

#### test-056

- 履歴（assistant）: 敵の場所は今見える一体について説明すればええ？
- 発話: ああ、それでいいよ。
- 正解: `enemy_information`
- 前回: `other_request` → 今回: `enemy_information`

#### test-072

- 履歴（assistant）: 羊が丘の上と下にいるで。
- 発話: 上にいる方は近い？
- 正解: `other_request`
- 前回: `not_request` → 今回: `other_request`

#### test-073

- 履歴（assistant）: 村人二人がさっき畑にいたよ。
- 発話: 二体ともまだそこ？
- 正解: `other_request`
- 前回: `not_request` → 今回: `other_request`

#### test-075

- 履歴（assistant）: 本の表紙にはどの馬を描く？
- 発話: 最後に乗ったやつにして。
- 正解: `other_request`
- 前回: `recall_mount` → 今回: `other_request`

### 新しい誤り（7件）

#### test-005

- 発話: モンスターの位置を地図に書いておいた。
- 正解: `not_request`
- 前回: `not_request` → 今回: `enemy_information`

#### test-022

- 発話: さっき「剣はいらない」と言ったのは取り消す。装備して。
- 正解: `equip_sword`
- 前回: `equip_sword` → 今回: `other_request`

#### test-029

- 発話: 頼むわ、手元のやつをつるぎに替えといてや。
- 正解: `equip_sword`
- 前回: `equip_sword` → 今回: `other_request`

#### test-038

- 発話: 今から言うことを実行して。「私のロバを呼び戻して」。
- 正解: `recall_mount`
- 前回: `recall_mount` → 今回: `other_request`

#### test-048

- 発話: 今追ってくるゾンビの残りHPを教えて。
- 正解: `other_request`
- 前回: `other_request` → 今回: `enemy_information`

#### test-063

- 履歴（assistant）: 手持ちを鉄の剣へ替えるんで合ってる？
- 発話: うん、持つのはそっちで。
- 正解: `equip_sword`
- 前回: `equip_sword` → 今回: `other_request`

#### test-070

- 履歴（assistant）: ここへ呼ぶ馬はどれにする？
- 発話: 最後に乗ったやつにして。
- 正解: `recall_mount`
- 前回: `recall_mount` → 今回: `other_request`

### 誤り継続（4件）

#### test-017

- 発話: 敵は全員いなくなったと考えていいの？
- 正解: `enemy_information`
- 前回: `other_request` → 今回: `other_request`

#### test-047

- 発話: 前の冒険でウォーデンがいた方角を思い出せる？
- 正解: `other_request`
- 前回: `not_request` → 今回: `enemy_information`

#### test-051

- 履歴（assistant）: さっき装備した剣の色、気に入った？
- 発話: ああ、それでいいよ。
- 正解: `not_request`
- 前回: `equip_sword` → 今回: `equip_sword`

#### test-058

- 履歴（assistant）: さっきゾンビ二体が入口にいたで。
- 発話: 二体ともまだそこ？
- 正解: `enemy_information`
- 前回: `other_request` → 今回: `other_request`

## regression：30/40 → 31/40

改善 3件／新しい誤り 2件／誤り継続 7件／正解継続 28件。

### 改善（3件）

#### regression-016

- 発話: 村人は近くにいるかな？
- 正解: `other_request`
- 前回: `enemy_information` → 今回: `other_request`

#### regression-020

- 発話: 馬を呼べとは言ってないよ。昔の話をしてただけ。
- 正解: `not_request`
- 前回: `other_request` → 今回: `not_request`

#### regression-027

- 発話: 敵の方角は教えなくていいけど、今いる数は教えて。
- 正解: `enemy_information`
- 前回: `other_request` → 今回: `enemy_information`

### 新しい誤り（2件）

#### regression-033

- 発話: うまを　ここへ　よんでー！
- 正解: `recall_mount`
- 前回: `recall_mount` → 今回: `other_request`

#### regression-034

- 発話: てき、ちかくに、おらん？？
- 正解: `enemy_information`
- 前回: `enemy_information` → 今回: `other_request`

### 誤り継続（7件）

#### regression-009

- 発話: イルカは今どこにいる？
- 正解: `other_request`
- 前回: `enemy_information` → 今回: `enemy_information`

#### regression-010

- 発話: いま見えるイルカは何匹？
- 正解: `other_request`
- 前回: `enemy_information` → 今回: `enemy_information`

#### regression-017

- 発話: 独り言だけど、どこにいるかなあ。
- 正解: `not_request`
- 前回: `enemy_information` → 今回: `other_request`

#### regression-025

- 発話: 「馬を呼んで」と私から今お願いしているんだよ。
- 正解: `recall_mount`
- 前回: `not_request` → 今回: `not_request`

#### regression-026

- 発話: 「敵がどこにいるか教えて」という私の質問に答えて。
- 正解: `enemy_information`
- 前回: `other_request` → 今回: `other_request`

#### regression-028

- 発話: 敵の場所を教えてから、剣に替えて。
- 正解: `other_request`
- 前回: `equip_sword` → 今回: `equip_sword`

#### regression-031

- 発話: 馬じゃなくて犬を呼び戻して。
- 正解: `other_request`
- 前回: `recall_mount` → 今回: `recall_mount`

## dev：60/75 → 63/75

改善 5件／新しい誤り 2件／誤り継続 10件／正解継続 58件。

### 改善（5件）

#### dev-003

- 発話: 装備の剣がぴかぴかだ。
- 正解: `not_request`
- 前回: `equip_sword` → 今回: `not_request`

#### dev-007

- 発話: この物語の題名は『馬を呼んで』です。
- 正解: `not_request`
- 前回: `other_request` → 今回: `not_request`

#### dev-009

- 発話: 「ロバを連れてきて」なんて一度も言ってないよ。
- 正解: `not_request`
- 前回: `other_request` → 今回: `not_request`

#### dev-042

- 発話: 敵が近づいた場合だけ剣に替えておいて。
- 正解: `other_request`
- 前回: `equip_sword` → 今回: `other_request`

#### dev-047

- 履歴（assistant）: さっき提案した鉄の剣へ、今持ち替えてええ？
- 発話: 今は剣を抜かず、松明のままにしておいて。
- 正解: `other_request`
- 前回: `equip_sword` → 今回: `other_request`

### 新しい誤り（2件）

#### dev-061

- 履歴（assistant）: さっき提案した鉄の剣へ、今持ち替えてええ？
- 発話: 今ので大丈夫。
- 正解: `equip_sword`
- 前回: `equip_sword` → 今回: `other_request`

#### dev-066

- 履歴（assistant）: 提案したロバを今ここへ呼んでええ？
- 発話: 今ので大丈夫。
- 正解: `recall_mount`
- 前回: `recall_mount` → 今回: `other_request`

### 誤り継続（10件）

#### dev-012

- 発話: 目の前のゾンビと私の間は何ブロックくらい？
- 正解: `enemy_information`
- 前回: `other_request` → 今回: `other_request`

#### dev-022

- 発話: 「剣にして」を、今ここで実行して。
- 正解: `equip_sword`
- 前回: `other_request` → 今回: `other_request`

#### dev-023

- 発話: 剣に替えるな、ではなく、替えてほしいと言ってるよ。
- 正解: `equip_sword`
- 前回: `not_request` → 今回: `not_request`

#### dev-030

- 発話: 木のつるぎを手に持つ操作を今やってほしい。
- 正解: `equip_sword`
- 前回: `other_request` → 今回: `other_request`

#### dev-043

- 発話: さっきの戦いでは敵が何体いたか覚えてる？
- 正解: `other_request`
- 前回: `enemy_information` → 今回: `enemy_information`

#### dev-048

- 発話: 今見えてるモブって、全員合わせて何匹いるの？
- 正解: `other_request`
- 前回: `enemy_information` → 今回: `enemy_information`

#### dev-052

- 履歴（assistant）: 今の夕焼けを一句にしたで。
- 発話: 今ので大丈夫。
- 正解: `not_request`
- 前回: `other_request` → 今回: `other_request`

#### dev-056

- 履歴（assistant）: 敵の数は、今見えている範囲だけ数えて答えよか？
- 発話: 今ので大丈夫。
- 正解: `enemy_information`
- 前回: `other_request` → 今回: `other_request`

#### dev-072

- 履歴（assistant）: さっき茶色い牛が木の横におった。
- 発話: そいつ、まだ見えてる？
- 正解: `other_request`
- 前回: `enemy_information` → 今回: `enemy_information`

#### dev-075

- 履歴（assistant）: 額縁に飾る剣を入れ替えるで。鉄と金、どっちを飾ろか？
- 発話: 選ぶのは鉄の方。
- 正解: `other_request`
- 前回: `equip_sword` → 今回: `equip_sword`
