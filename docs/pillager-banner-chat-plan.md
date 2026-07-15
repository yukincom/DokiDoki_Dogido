# player_chat × カタログ話題照合計画（観測 + 汎用トピック）

**日付:** 2026-07-15  
**状態:** 詳細設計（実装前〜段階実装）  
**関連:** [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md)、[event-schema.md](event-schema.md)、[dialogue-design.md](dialogue-design.md)、[senryu-rag-plan.md](senryu-rag-plan.md)

## ゴール

プレイヤーが自由に話題にしたとき、ドギドが:

1. **可能なら** 観測（visual / 直近バッファ）に基づいて種名・状況を言い当てる  
2. 観測が弱いときは **断定せず** カタログ上の候補を「かもしれん」で返す  
3. カタログに無い種族・NPC・ダンジョン住人など **的外れな推測をしない**

**「旗」は一例。** 実装は特定キーワード専用にしない。  
プレイヤーによっては:

| 入力例 | 期待される候補のイメージ |
|---|---|
| あいつら変な旗持ってる | pillager / ominous_banner 知識 |
| なんだあのババア | witch（通称・蔑称・見た目由来） |
| とんがり帽子の紫の | witch（poetic visual_tags） |
| グレーのクロスボウ隊 | pillager |
| 沼の小屋の薬投げ | witch（scene + motion） |
| おはよう | 話題ヒットなし |

カタログ準備（済・一部）:

- `pillager.poetic.visual_tags` に **「旗」「不気味」** 等  
- `witch.poetic` に **とんがり帽子・紫・薬**、role に **魔女**  
- `ominous_banner` → 不吉な旗 + note  

**タグを JSON に足しただけでは足りない。** 下記 ①観測 ②**汎用**プレイヤー文→カタログ ③載ったあとの補強 が要る。

---

## 全体像

```text
                    ┌─────────────────────────────┐
  ① 観測パイプライン │ adapter: 視認・装備・距離     │
                    │ → visual_threats (+ hints)  │
                    └─────────────┬───────────────┘
                                  │ 載る / 載らない
                                  ▼
  ② 汎用トピック照合 │ プレイヤー文の断片 → カタログ │
                    │ label / tags / role / note  │
                    │ / spoken_aliases（任意）     │
                    └─────────────┬───────────────┘
                                  ▼
  ③ 表現の補強       │ threat 行 + tactics + note  │
                    │ プロンプト規則・fallback     │
                    └─────────────────────────────┘
```

① が本命（「見た」事実）。  
② は視認が空・遅れても、**プレイヤーが何を指しているか**をカタログ語彙で橋を架ける。  
③ は ①② が届いたあと候補を自然に言わせる。

**② のコードはモブ種・キーワードにハードコードしない。**  
旗・ババアはテストケースとカタログデータの話。

---

## ① 観測: 脅威が visual_threats に載る

### 1.1 現状（コード上）

| 段階 | 内容 |
|---|---|
| スキャン | 既定おおよそ **30m** 内の `Monster` 等 |
| 敵対判定 | 通常 Monster → 常時 hostile |
| 可視フィルタ | **LOS** + 近距離即時 / 複数視認即時 / **視線継続 tick**（既定 6） |
| サーバ | `visual_count=0` のフレームでは threat なし |

プレイヤーの「見えた」≠ adapter の `lineOfSight` 通過。

### 1.2 失敗しやすいパターン

| 原因 | 症状 |
|---|---|
| 距離 > スキャン | そもそも scan 外 |
| LOS 失敗 | scan にはいるが `visual_threats` 落ち |
| confirm ticks 未満で話す | 一瞬見えたが未確定 |
| 話しかけの **その snapshot だけ** 空 | 前フレームでは載っていた（要バッファ） |
| 装備だけ目立つ（旗） | type は載っても「旗」は type だけでは弱い |

### 1.3 実装タスク（①）

#### ①-A 観測ログ

player_chat 時:

```text
player_chat_visual: types=… count=…
```

再現時に **生 status_snapshot** を1本保存。

#### ①-B adapter: 可視に載せやすくする（要実測後）

候補: illager 系の confirm 緩和、LOS 失敗時の近距離救済、レイ改善、  
`scanned_but_not_visible` 理由ログ。

#### ①-C 装備・付帯ヒント（任意フィールド）

```json
{
  "type": "pillager",
  "distance": 12.0,
  "equipment_hints": ["ominous_banner"]
}
```

特定装備専用ロジックではなく、**任意 id のヒント列**として載せる。

#### ①-D 直近 visual バッファ（サーバ）

音バッファと同型:

- `recent_visual_memos`: type, label_ja, direction, seen_at  
- 保持: 8〜15 秒（設定化）  
- threat_summary に **「ついさっき: ピリジャー 前」** を混ぜる  

### 1.4 ①の完了条件

- [ ] 正面の脅威について、高頻度で `visual_threats` に type が載る  
- [ ] 可能なら `equipment_hints`  
- [ ] chat 直前に visual が空でも、バッファに直近種名があるケースが再現できる  

---

## ② 汎用トピック照合（プレイヤー文 → カタログ）

### 2.1 設計原則（重要）

| やってよい | やらない |
|---|---|
| カタログ各フィールドを **検索面** として走査 | `if "旗" in text: return pillager` のような専用分岐 |
| ヒットは **候補スコア付きリスト** | 1キーワード＝1種族の固定マップをコードに埋める |
| 観測 type で候補をブースト | 観測ゼロでも tactics を断定的に載せる |
| カタログに語を足してヒットを増やす | 禁止語・大量ハードコード別名表をコード側に置く |

`resolve_biome_place_from_text` と同型の発想:

- **語彙の正本はカタログ（＋薄い spoken_aliases）**  
- コードは「文中にカタログ語が含まれるか」の汎用マッチャ  

### 2.2 検索面（エントリから集める語）

各 mob（将来は item/block も同 API で拡張可）について、次を **マッチ用文字列集合** にする:

| ソース | 例（pillager） | 例（witch） |
|---|---|---|
| `label` / `japanese` | ピリジャー | ウィッチ |
| `poetic.visual_tags` | 旗、灰色、クロスボウ、不気味 | 紫、とんがり帽子、薬、鼻にできもの |
| `poetic.sound_tags` | かちゃ、びゅっ | 笑い声、ごくごく |
| `poetic.motion_tags` | 囲む、射る | 投げる、回復する |
| `poetic.scene_tags` | 襲撃、前哨基地 | 沼、小屋、夜 |
| `poetic.reaction_tags` / `comic_tags` | （必要なら） | |
| `poetic.role` | 群れる略奪者 | じわじわ嫌がらせする魔女 |
| `dogido_tactics.notes` 等（任意） | 短い note 断片 | |
| **`spoken_aliases`（新規・任意）** | （不要なら空） | **ババア、魔女、あの婆** など |
| 関連 item note（任意） | ominous_banner の「不吉な旗」「襲撃隊」 | |

`spoken_aliases` の位置づけ:

- プレイヤーがよく言う **通称・蔑称・誤認名** をカタログ側に置く  
- コードに `WITCH_NICKNAMES = ["ババア"]` を書かない  
- 最初は必要エントリだけ（witch 等）。無いエントリは aliases なしで poetic だけで当たる  

### 2.3 マッチ手順（アルゴリズム案）

```text
入力: player_text, observed_types (visual + visual buffer)
出力: list[TopicHit]  # 上位 1〜3

1. 正規化
   - strip、かな折りたたみ（既存 _fold_kana_for_match 再利用）
   - 極端に短いクエリはスキップ

2. インデックス（起動時 or lazy キャッシュ）
   - 全 mob（＋任意で ominous_banner 等 item）について
     term → [ (entry_id, field_kind, term_len) ]
   - term は長さ 2 以上（「薬」等は残す）。1 文字だけは原則除外
   - 長い term 優先で照合（「とんがり帽子」＞「帽子」）

3. 文中ヒット収集
   for each term in index (長い順):
     if term in normalized_text:
       score[entry] += weight(field_kind) * f(len(term))
       record matched_terms

4. 重み field_kind（案）
   spoken_aliases / label     : 高
   visual_tags / role 断片    : 中〜高
   scene / motion / sound     : 中
   comic / reaction           : 低
   関連 item note             : 中（旗→襲撃知識）

5. 観測ブースト
   if entry_id in observed_types:
     score *= 大（または +固定大点）
   equipment_hints がヒット term と一致すればさらに +

6. 閾値・カット
   - 最低スコア未満は捨てる（「おはよう」で誤爆しない）
   - top K（1〜3）
   - 同点なら観測あり優先、次に label 一致

7. ヒント文生成
   - label + マッチした代表タグ数個 + note 短く
   - 「観測: あり/なし」を1語添える
```

**反転検索が本質:**  
「この文に旗があるか」ではなく **「カタログのどの語がこの文に含まれているか」**。  
新しい別名は JSON に足すだけで、マッチャ変更なし。

### 2.4 曖昧さ・誤爆の扱い

| ケース | 方針 |
|---|---|
| 複数エントリが同点 | top 2〜3 を候補として載せ、断定禁止 |
| 「旗」だけで弱い | 単独ヒットはスコア低めでもよい。観測ブースト or 共起（不気味・あいつら）で上げるのは **スコア式** で。専用 if は書かない |
| 「国旗」「白旗」 | 最初は閾値＋観測なしなら候補を出さない／弱くする。必要なら spoken に「不吉な旗」など長い語を優先 |
| 「ババア」 | catalog `spoken_aliases` が無いとヒットしない → **データで足す**（コード分岐しない） |
| 役割語「魔女」 | role に含まれるなら部分一致で witch に届く可能性。誤爆が出たら最小長・フィールド重みで調整 |
| 完全に無関係な挨拶 | ヒット 0 → hints 節ごと省略 |

### 2.5 player_chat に渡す形

```text
カタログからの話題ヒント（断定材料ではない）:
- ウィッチ: 通称っぽい語「ババア」／見た目 とんがり帽子・紫。観測: なし
- ピリジャー: 見た目タグ「旗」。不吉な旗は襲撃隊の大将の印、という知識。観測: 視覚にあり
```

プロンプト規則:

- ヒントは **可能性**。観測が無いときは断定しない  
- 観測があるときはヒントと threat を一致させてよい  
- **ヒントに無い id を話題から捏造しない**  
- 複数候補なら「どっちやろ」程度でよい  

### 2.6 API 案（実装モジュール）

| 場所 | 役割 |
|---|---|
| `entry_catalog.py` | `build_topic_term_index()` / `find_catalog_topics(text, *, observed_ids=()) -> list[TopicHit]` |
| `TopicHit` | `entry_id`, `kind` (mob/item/…), `label_ja`, `score`, `matched_terms`, `observed: bool` |
| narration / player_chat 組み立て | `catalog_topic_hints` を details に 0〜数行 |
| `prompts.py` | ヒント節 + 断定ルール（汎用文面。旗専用文にしない） |
| カタログ JSON | 必要エントリに `spoken_aliases: ["ババア", …]` を追加 |
| `tests/` | 下表 |

### 2.7 テスト計画（汎用性を固定する）

| 入力 | 観測 | 期待 |
|---|---|---|
| 変な旗持ってる | なし | pillager（± banner 知識）が候補。断定弱め |
| 変な旗 | pillager あり | pillager が最上位 |
| なんだあのババア | なし | witch が候補（aliases 登録後） |
| とんがり帽子の紫 | なし | witch（visual_tags） |
| グレーのクロスボウ | なし | pillager |
| おはよう | なし | ヒットなし |
| 旗（単独・観測なし） | なし | 閾値次第で空 or 弱い候補（テストで仕様固定） |
| 無関係な「薬」だけ | なし | 誤爆が許容外ならスコア調整（データ or 重み） |

**コードに `FLAG_KEYWORDS` / `WITCH_KEYWORDS` 定数を置いた実装はレビュー却下。**  
定数を置くなら「最小 term 長」「フィールド重み」「top_k」など **アルゴリズムパラメータのみ**。

### 2.8 ②の完了条件

- [ ] プレイヤー文 → カタログ走査の **1 関数** で、旗もババアも同じ経路  
- [ ] 視認ありなら該当 type が最上位になりやすい  
- [ ] 無関係文で hints が出ない  
- [ ] 新しい通称は `spoken_aliases`（または poetic タグ）追加だけで伸びる  

---

## ③ 載ったあとの補強（表現・知識）

①② が届いても、表現が薄いと種名に届かない。

### 3.1 カタログ

| 項目 | 内容 |
|---|---|
| poetic（済・拡充可） | 検索面かつ描写材料 |
| `spoken_aliases` | プレイヤー通称（witch のババア等） |
| `dogido_tactics` | notes / safe_hints（観測があるとき優先） |
| item note | ominous_banner 等をヒント文に短く |

### 3.2 threat_summary の厚み

```text
視認 ピリジャー が前 12マス（不吉な旗っぽい装備）
ついさっき ウィッチ 右
```

### 3.3 tactics の合成

既存 `collect_dogido_tactics_for_mobs` を:

- visual + visual バッファの type で呼ぶ  
- ②の候補 type は **観測があるときだけ** tactics に混ぜる（空観測で tactics だけ強いと断定しすぎ）

### 3.4 プロンプト・fallback

| 項目 | 内容 |
|---|---|
| 規則 | 脅威メモ・カタログヒントを優先。ヒント外の種族にすり替えない |
| fallback | 「おう、聞こえとるで〜。」廃止 → 中立「ようわからん、もうちょい教えて」等 |
| unusable | 英字 NPC 等で落ちても中身のある日本語 fallback |

### 3.5 ③の完了条件

- [ ] 視認 + 話題一致 → 種名・特徴のどちらかが自然に出る  
- [ ] 視認なし + トピックヒット → 断定しすぎないが候補に触れる  
- [ ] 失敗時も「聞こえとる」系に飛ばない  

---

## ④ mob ↔ structure ↔ biome の知識リンク

### 4.1 問題意識

プレイヤー:「ピリジャーいる！前哨基地もあるだろうか？」  
ドギド:「タイガやからありうるかも！」  

のような **生成可否の推論** には、単体エントリのタグ一致だけでは足りない。  
次の三角関係がコードから辿れる必要がある:

```text
  pillager  ←→  pillager_outpost  ←→  biomes (plains, taiga, …)
  elder_guardian ←→ monument ←→ deep_ocean…
  witch ←→ Witch_hut ←→ swamp
```

これは **GraphRAG / ベクトル検索ではない**。  
カタログに既にある（または薄い id リンクを足す）**構造化リンクの直引き**。

### 4.2 いまカタログにあるもの / 無いもの

| 辺 | カタログ | コードから使えるか |
|---|---|---|
| structure → biomes | **あり**（全 structure に `biomes[]`） | `structure_entries()` で読める。player_chat では未使用 |
| structure → note | あり（前哨・海底神殿など） | structure 入場 narration / haiku 寄り。chat 推論では未使用 |
| structure → mobs | **無し**（note の日本語だけ:「ガーディアンと…」） | 機械可読リンクなし |
| mob → structure | poetic `scene_tags` の自由語のみ（「前哨基地」「海底神殿」） | id ではない。表記ゆれに弱い |
| mob → biome | ほぼ無し | — |
| 現在地 biome | 観測（world / place_context） | 既にある |

**結論:** structure↔biome のデータは揃っている。  
**欠けているのは mob↔structure の機械可読リンクと、それを player_chat に載せる合成。**

### 4.3 データ方針（薄いリンク、正本は id）

scene_tags の「前哨基地」に依存して文字列マッチで繋がない。  
**どちらか一方向の id 配列を正本にし、コードで逆引きする。**

推奨（structure 側に寄せる — 既に biomes がある隣）:

```json
"pillager_outpost": {
  "japanese": "ピリジャー前哨基地",
  "biomes": ["plains", "taiga", "..."],
  "related_mobs": ["pillager", "ravager"],
  "note": "..."
},
"monument": {
  "japanese": "海底神殿",
  "biomes": ["deep_ocean", "..."],
  "related_mobs": ["guardian", "elder_guardian"],
  "note": "..."
},
"Witch_hut": {
  "related_mobs": ["witch"],
  "biomes": ["swamp"]
}
```

または mob 側:

```json
"pillager": {
  "related_structures": ["pillager_outpost"]
}
```

**両方書かない**（二重管理を避ける）。structure→mobs 推奨。

注意（ゲーム知識）:

- ピリジャーは **襲撃でも** 前哨と無関係に現れる → 「ピリがいる＝必ず前哨が近い」は **言わない**  
- 「前哨が **このバイオームに生成されうるか**」は structure.biomes で答えられる  
- エルダーは実質 monument 前提に近い → 強めの結びでよい  

### 4.4 API 案（entry_catalog）

```text
structures_for_mob(mob_id) -> list[structure_id]
mobs_for_structure(structure_id) -> list[mob_id]
structure_biomes(structure_id) -> set[biome_id]
structures_plausible_in_biome(biome_id) -> list[structure_id]

# 会話用の1行ヒント
plausibility_hint(
  *,
  topic_mobs, topic_structures, current_biome_id
) -> str | None
```

例:

```text
知識リンク（断定ではない）:
- ピリジャー前哨基地: 生成しうるバイオームに タイガ を含む → いまの場所ではありうる
- ピリジャーがいること自体は襲撃でも起きる。前哨の有無は別問題
```

プロンプト規則:

- **生成しうる** と **いま視界にある** を混同しない  
- 現在 biome が structure.biomes に無い →「この辺のバイオームには出にくいかも」  
- biome 不明 → リンクだけ述べて場所判断は避ける  

### 4.5 トピック照合（②）との合流

`find_catalog_topics` の対象を mob だけでなく **structure の label / note 断片** にも広げる:

| 入力 | ヒット例 |
|---|---|
| 前哨基地ある？ | structure:pillager_outpost |
| 海底神殿 | structure:monument |
| ピリジャー＋前哨 | mob + structure 両方 → ④で biomes 突合 |

② で id が分かり、④ で **現在 biome と突合**して1行足す。

### 4.6 ④の完了条件

- [ ] structure 主要エントリに `related_mobs`（または mob 側 related_structures）  
- [ ] `plausibility_hint` 相当が player_chat details に載る  
- [ ] タイガ + 「前哨ある？」→ ありうる系  
- [ ] 深海以外 + 「海底神殿？」→ 出にくいかも系（データに忠実）  
- [ ] 「ピリいる＝前哨確定」にしない  

### 4.7 GraphRAG との関係

| やること | 手段 |
|---|---|
| 既知の生成関係・関連 mob | **カタログ id リンク + 直引き**（本節） |
| 似た描写の借り物・曖昧想起 | 将来のベクトル（senryu-rag 第2波以降） |

**④ をベクトルで代替しない。** 正解がカタログに書ける関係は直引き。

---

## 実装順（推奨 PR 分割）

| PR | 内容 | 依存 |
|---|---|---|
| **PR-A** | chat fallback 中立化 + player_chat に visual 件数・types ログ | なし |
| **PR-B** | ② **汎用** `find_catalog_topics`（mob + 必要なら structure ラベル）+ テスト | カタログ tags / 通称タグ済 |
| **PR-C** | ①-D 直近 visual バッファ + threat_summary 合流 | A |
| **PR-F** | ④ structure `related_mobs` + biome 突合ヒント + テスト | B（topic id が欲しい） |
| **PR-D** | ①-B/C adapter: LOS/confirm・equipment_hints | 実測ログ |
| **PR-E** | ③ tactics / note 厚み・プロンプト仕上げ | B, C, F,（D あれば尚良） |

体感が早く上がるのは **A → B → C**。  
「前哨ありうる？」系の会話品質は **B のあと F** で一気に上がる。  
「見えてるのに 0」が続くなら **D を前倒し**。

PR-B の受け入れ:

- 同じ API で pillager（旗・オッサン）と witch（ババア / とんがり帽子）が通る  
- ハードコードキーワード表が無い  

PR-F の受け入れ:

- 現在 biome=taiga・話題に前哨 → details に「生成しうる」系  
- ピリ視認だけ・前哨未言及 → 前哨確定文を出さない  

---

## 成功条件（プロダクト）

1. 観測できる脅威について聞かれたとき、**高頻度で種名・特徴**に触れる  
2. 観測が無いときは **見えてへん／かもしれん** で、捏造種族にしない  
3. poetic / 通称タグが **検索と描写の両方**で使われる  
4. **新しい話題はカタログ更新で伸びる**（コード分岐を増やさない）  
5. 禁止リスト運用に依存しない  
6. **関連 structure / 現在 biome から「ありうる／出にくい」**をカタログ根拠で言える  

---

## データ作業メモ

### 通称・見た目タグ（済・作業中）

- witch `visual_tags` に **ババア**  
- pillager / vindicator / evoker に **オッサン・ジジイ** 等  
- 照合は poetic タグ全体を検索面にすれば足りる（専用 `spoken_aliases` は任意）

### structure リンク（④で追加）

優先度高:

| structure_id | related_mobs（案） |
|---|---|
| pillager_outpost | pillager, ravager |
| monument | guardian, elder_guardian |
| Witch_hut | witch |
| mansion | vindicator, evoker, allay?（要確認） |

biome 列は **既に structure にある**ので触らない（データ修正が必要なら別）。  
`cherry-grove` のような id 表記ゆれは ④ 実装時に `cherry_grove` 正規化を確認。

---

## 次の合意ポイント

1. まず **PR-A + PR-B**（fallback + **汎用**トピック照合）で体感を上げるか  
2. 続けて **PR-F**（mob–structure–biome リンク）を入れるか  
3. それとも **実 snapshot を取って ① の LOS 問題を先に潰す**か  

② は「旗専用」ではなく **プレイヤーの自由な呼び方 → カタログ** の共通基盤。  
④ は GraphRAG ではなく **既存 structure.biomes + 薄い related_mobs** で会話推論を足す。
