# player_chat × カタログ話題照合計画（観測 + 汎用トピック）

**日付:** 2026-07-15  
**状態:** 詳細設計（段階実装中）  
**ファイル名メモ:** 歴史的に `pillager-banner-chat-plan.md`。中身は旗専用ではない。  
**関連:** [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md)、[sound-identity-plan.md](sound-identity-plan.md)、[event-schema.md](event-schema.md)、[dialogue-design.md](dialogue-design.md)、[senryu-rag-plan.md](senryu-rag-plan.md)、**[player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md)**（**実装 PR 順の正本**: S1→S2→C→S3→F′→E′）

### 進行メモ（2026-07-15）

- **PR-A** ✅ 完了  
- **PR-B** ⏸ **途中停止**（`find_catalog_topics` 等エンジンのみ採用。プロンプト厚みは打ち切り）  
- **優先:** 旧 B→C→F→E は使わない。**状態機械寄せ**の順に切り替え（正本は [player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md)）  
- 本ドキュメントは引き続き **観測①・トピック②・表現③・structure リンク④の仕様**を正とする。PR 番号・実施順は SM 文書に従う  

## ゴール

プレイヤーが自由に話題にしたとき、ドギドが:

1. **可能なら** 観測（visual / 直近バッファ）に基づいて種名・状況を言い当てる  
2. 観測が弱いときは **断定せず** カタログ上の候補を「見えんけど、それなら〜かもしれん」で返す  
3. プレイヤーの「見えてる／いる」を、ドギド非検出だけで **否定して落とさない**  
4. 候補・言い当ては **カタログ JSON 内**に限る（無い種族・汎用 NPC を捏造しない）

**「旗」は一例。** 実装は特定キーワード専用にしない。

| 入力例 | 期待される候補のイメージ |
|---|---|
| あいつら変な旗持ってる | pillager / ominous_banner 知識 |
| なんだあのババア | witch（`visual_tags` 等の通称） |
| とんがり帽子の紫の | witch（poetic visual_tags） |
| グレーのクロスボウ隊 / オッサンら | pillager |
| 沼の小屋の薬投げ | witch（scene + motion） |
| 前哨基地ある？ | structure:pillager_outpost → ④で biome 突合 |
| おはよう | 話題ヒットなし |

カタログ準備（済・一部）:

- pillager: 旗・不気味・オッサン・ジジイ 等（`visual_tags`）  
- witch: とんがり帽子・紫・薬・**ババア**、role に魔女  
- vindicator / evoker: オッサン・ジジイ 等  
- `ominous_banner`: 不吉な旗 + note  
- structure: 各エントリに `biomes[]` 済み。`related_mobs` は未（④で追加）

タグや通称を JSON に足しただけでは足りない。下記 **①〜④** が要る。

---

## 全体像

```text
  ① 観測          adapter 視認・装備 → visual_threats
       │            + サーバ visual バッファ
       ▼
  ② トピック照合   プレイヤー文 → カタログ語彙（mob / structure / item）
       │            観測ありならスコアブースト
       ▼
  ④ 知識リンク     mob↔structure↔biome（id 直引き）※必要時
       │
       ▼
  ③ 表現           threat 行 + hints + プロンプト規則 + fallback
```

| 層 | 役割 | 本命か |
|---|---|---|
| ① | 「見た／ついさっき見た」事実 | 本命 |
| ② | 視認が空でも、プレイヤーが何を指すかを JSON 語彙で橋渡し | 会話の要 |
| ④ | 「前哨ありうる？」など生成関係 | 話題が structure に触れたとき |
| ③ | ①②④ を自然な口調・中立 fallback に載せる | 仕上げ |

**②④ のコードは種族・キーワードにハードコードしない。**  
旗・ババア・前哨は **テストケースとカタログデータ**。

---

## ① 観測: 脅威が visual_threats に載る

### 1.1 現状（コード上）

| 段階 | 内容 |
|---|---|
| スキャン | 既定おおよそ **30m**（`max_threat_distance` / `visible_threat_distance`） |
| 敵対判定 | 通常 Monster → 常時 hostile |
| 可視フィルタ | **LOS** + 近距離即時 / 複数視認即時 / **視線継続 tick**（既定 6） |
| サーバ | `visual_count=0` のフレームでは threat なし |

プレイヤーの「見えた」≠ adapter の `lineOfSight` 通過。  
開けた視界は少なく、**scan を 50m 級に伸ばすのは主対策にしない**（§2.5b）。

### 1.2 失敗しやすいパターン

| 原因 | 症状 |
|---|---|
| 距離 > スキャン | scan 外（プレイヤーにはまだ見えることあり） |
| LOS 失敗 | scan にはいるが `visual_threats` 落ち |
| confirm ticks 未満で話す | 一瞬見えたが未確定 |
| 話しかけの **その snapshot だけ** 空 | 前フレームでは載っていた → ①-D バッファ |
| 装備だけ目立つ（旗） | type は載っても「旗」は type だけでは弱い → ①-C / ② |

### 1.3 実装タスク（①）

| ID | 内容 | 担当 PR |
|---|---|---|
| ①-A | player_chat 時 `visual types/count` ログ。再現用 snapshot | **PR-A** |
| ①-B | adapter: confirm/LOS 調整（**実測後**）。scanned_but_not_visible ログ | **PR-D** |
| ①-C | `equipment_hints` 等の任意付帯（装備専用 if は書かない） | **PR-D** |
| ①-D | `recent_visual_memos`（8〜15s）→ threat_summary「ついさっき」 | **PR-C** |

### 1.4 ①の完了条件

- [ ] 正面の脅威について、高頻度で `visual_threats` に type が載る（実測後の ①-B 含む）  
- [ ] 可能なら `equipment_hints`  
- [ ] chat 直前に visual が空でも、バッファに直近種名があるケースが再現できる  

---

## ② 汎用トピック照合（プレイヤー文 → カタログ）

### 2.1 設計原則

| やってよい | やらない |
|---|---|
| カタログ各フィールドを **検索面** として走査 | `if "旗" in text: return pillager` |
| ヒットは **候補スコア付きリスト** | コード内 1キーワード＝1種族マップ |
| 観測 type で候補をブースト | 観測ゼロで tactics を断定的に載せる（→ ③） |
| カタログに語を足してヒットを増やす | 禁止語リスト・コード内別名表・**クエリ拡張** |

`resolve_biome_place_from_text` と同型:

- 語彙の正本は **カタログ JSON**  
- コードは「文中にカタログ語が含まれるか」の汎用マッチャ  

### 2.2 検索面（どこに通称を置くか）

**マッチ対象は「カタログ上の文字列」すべて（長さ条件あり）。**  
フィールド名の違いは重み付けの差だけで、ヒット可否の本質ではない。

| ソース | 役割 | 現状の実データ例 |
|---|---|---|
| `label` / `japanese` | 正式名 | ピリジャー、ウィッチ |
| `poetic.*_tags` / `role` | 見た目・場面・役割・**通称もここに置いてよい** | 旗、ババア、オッサン、前哨基地（scene 自由語） |
| `spoken_aliases`（**任意・未使用でも可**） | 通称を見た目タグと分けたいときの受け皿 | 今は必須にしない |
| structure の label / note | 前哨基地・海底神殿など | `pillager_outpost.japanese` 等 |
| 関連 item note（任意） | 不吉な旗＝襲撃知識 | ominous_banner |

**通称の置き場（方針の単一化）:**

- **現状どおり `visual_tags` 等 poetic に置いてよい**（ババア・オッサン・ジジイはこれで済）  
- `spoken_aliases` は「見た目ではない呼び方を分けて管理したくなったら」のオプション  
- どちらに置いても **同じマッチャ**が拾う。コードに `WITCH_NICKNAMES` は書かない  

**scene_tags の自由語（「前哨基地」）について:**

- ②の **文字列ヒット**には使ってよい（pillager 候補の手がかり）  
- mob↔structure の **id リンク**には使わない（④は `related_mobs` 等の id。表記ゆれに弱い）

### 2.3 マッチ手順（案）

```text
入力: player_text, observed_types (visual + visual buffer)
出力: list[TopicHit]  # 上位 1〜3

1. 正規化（strip、かな折りたたみ）
2. インデックス: term → (entry_id, field_kind, len)  ※長さ2以上、長い語優先
3. 文中ヒットで score 加算
4. 重み: label / 通称っぽい語 高、visual 中〜高、scene/motion/sound 中、comic/reaction 低
5. observed_types に入っていれば大幅ブースト
6. 閾値・top K。同点は観測あり → label 一致
7. ヒント文: label + 代表 matched_terms + 観測あり/なし
```

本質は **反転検索**（カタログのどの語が文に含まれるか）。

### 2.4 曖昧さ・誤爆

| ケース | 方針 |
|---|---|
| 複数エントリ同点 | top 2〜3、断定禁止 |
| 「旗」単独 | スコア式で弱くてよい。閾値はテストで固定（専用 if なし） |
| 「国旗」「白旗」 | 閾値・長い語優先で抑制。必要ならカタログに「不吉な旗」等 |
| 「ババア」「オッサン」 | **poetic 等に語があればヒット**（今は visual_tags 済） |
| 挨拶のみ | ヒット 0 → hints 省略 |

### 2.5 details に載せる形

```text
カタログからの話題ヒント（断定材料ではない）:
- ウィッチ: マッチ「ババア」／とんがり帽子。観測: なし
- ピリジャー: マッチ「旗」。観測: 視覚にあり
```

### 2.5b 視界ギャップ・音との切り分け（方針正本 → 執行は SM）

プレイヤーには見えるがドギド visual に載らないことは普通にある。  
**主対策は視界拡張ではなく会話の型**（scan 既定 ~30m は維持し、伸ばすなら慎重に 40前後の実験まで）。

| 経路 | してよいこと | してはいけないこと | 執行の置き場 |
|---|---|---|---|
| **音** | 乗った音・解決できた種名だけ | 空 hearing から種名を足す | **S2 白リスト** + [sound-identity-plan](sound-identity-plan.md) |
| **視界（載ってる / バッファ）** | 「おる／ついさっきおった」 | 無い type を見たことにする | **S1 stance=saw** + **C バッファ** |
| **視界なし + プレイヤーが指している** | 「俺には見えんけど、それなら **カタログ候補** かもしれん」 | 「おらへん」で否定 | **S1 stance=hypothesis** + **S3 骨子** |
| **知識（④）** | biome×structure で「ありうる／出にくい」 | カタログ外の捏造 | **F′ SM 1 行**（LLM 推論禁止） |

音の「推測しない」＝センサー空なのに種名捏造しない。  
視界ギャップの「かもしれん」＝プレイヤー文をクエリに JSON 候補で言い換える。  
**目的が違うので矛盾しない。**

**§2.5b はプロンプトに長文で積み増す正本ではない。**  
方針の正本であり、実装は [player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md) の S1〜S3 / F′ でコード担保する。  
（PR-B 時点のプロンプト骨子は S1 で薄くする。）

言い方の型（SM の stance / 骨子が実現する内容）:

```text
1. 自分の観測を偽らない → 「俺には（今は）見えてへん」
2. プレイヤーを落とさない → 「お前が見てるんやろ」「それなら〜」
3. 候補はトピック照合 top のみ。ヒット0なら種名なしで特徴を聞く
4. 観測なしは弱断定、観測ありは一致すれば通常に言ってよい
```

成功イメージ:

- 「なんだあのババア」+ visual 0 →「見えんけど、薬のババア（ウィッチ）かもしれん」  
- 「変な旗」+ visual 0 →「見えてへんけど、旗持ちの略奪者（ピリ）の気配やな、それなら」  
- 「あれ何？」+ 特徴なし → 種名なし、「もうちょい特徴教えて」

### 2.6 API・テスト・完了条件

| 場所 | 役割 |
|---|---|
| `entry_catalog.py` | `find_catalog_topics`（**B で済**） |
| player_chat 組み立て | hints + 今後 `reply_stance` / policy（**S1**） |
| sanitize | 種名白リスト（**S2**） |
| `prompts` | 長文規則ではなく **短い policy + 口調**（S1 で削減、E′ で確認） |
| カタログ | 通称不足分を poetic 等に追加 |

| 入力 | 観測 | 期待 |
|---|---|---|
| 変な旗持ってる | なし | pillager 候補。弱断定 |
| 変な旗 | pillager あり | pillager 最上位 |
| なんだあのババア | なし | witch（visual_tags のババア）。否定しない |
| オッサンら旗 | なし/あり | pillager 寄り |
| おはよう | なし | ヒットなし |
| 「いるやろ！」+ visual 0 | なし | JSON 内候補のみ。おらへんで落とさない |

- コードに `FLAG_KEYWORDS` / `WITCH_KEYWORDS` を置いた実装は却下  
- 定数は最小 term 長・重み・top_k のみ可  

完了条件（照合エンジン・B）:

- [x] 1 関数で旗・ババア・オッサン等が同じ経路  
- [x] 視認ありなら該当 type が上がりやすい  
- [x] 無関係文で hints なし  
- [x] 新通称は **JSON 追加だけ**で伸びる  
- [ ] 方針執行がプロンプト任せでない → **S1〜S3**（B の範囲外）  

---

## ③ 表現の補強（threat / tactics / fallback）

①②④ が届いたあと、薄い表現や悪い fallback を直す。  
**方針は §2.5b。執行は S1〜S3 / S2 sanitize。** ここはチェックリスト。

| 項目 | 内容 | PR |
|---|---|---|
| threat_summary | 視認行 + バッファ「ついさっき」+ 任意 equipment | C, D |
| tactics | visual + バッファの type で合成。**トピック候補は観測があるときだけ** tactics に混ぜる | E′ |
| プロンプト | 長文規則を削り stance/policy 中心。ヒント外は **S2 白リスト** | S1, E′ |
| fallback | 中立「ようわからん…」 | **A 済**、E′ で磨き |
| unusable / 白リスト外 | 中身のある日本語 fallback | A / **S2** / E′ |

完了条件:

- [ ] 視認 + 話題一致 → 種名・特徴が自然に出る  
- [ ] 視認なし + トピック → 「見えんけど〜かもしれん」（**S1/S3**）  
- [ ] プレイヤーの「いる」を非検出だけで否定しない（**S1**）  
- [x] 「聞こえとる」系に飛ばない（**A**）  

---

## ④ mob ↔ structure ↔ biome の知識リンク

### 4.1 何が足りないか

| 辺 | カタログ | player_chat |
|---|---|---|
| structure → biomes | **あり** | 未使用 |
| structure → note | あり | chat 推論では未使用 |
| structure → mobs | **無し** | — |
| mob → structure | scene 自由語のみ | id リンクなし |
| 現在 biome | 観測あり | 突合なし |

欠けているのは **mob↔structure の id リンク**と、現在 biome との合成。  
GraphRAG / ベクトルでは代替しない。

### 4.2 データ（一方向のみ）

structure 側に寄せる（biomes の隣）:

```json
"pillager_outpost": {
  "related_mobs": ["pillager", "ravager"],
  "biomes": ["plains", "taiga", "..."]
}
```

mob 側 `related_structures` と **両方は書かない**。

ゲーム知識:

- ピリは襲撃でも出る →「ピリがいる＝前哨が近い」は言わない  
- 答えられるのは「この biome に前哨が **生成されうるか**」  
- エルダー↔monument は強めの結びでよい  

### 4.3 API・合流

```text
structures_for_mob / structure_biomes / plausibility_hint(...)
```

②で mob/structure id が分かり、④で biome 突合して1行:

```text
知識リンク（断定ではない）:
- ピリジャー前哨基地: タイガは生成しうるバイオームに含む
- ピリがいること自体は襲撃でも起きる。前哨の有無は別
```

**生成しうる** と **いま視界にある** を混同しない。  
この1行は **F′ で SM が details に載せる**（プロンプトに推論を書かせない）。

### 4.4 完了条件

- [ ] 主要 structure に `related_mobs`  
- [ ] details に **SM 生成の** plausibility 行  
- [ ] タイガ + 前哨？ → ありうる系  
- [ ] 深海以外 + 海底神殿？ → 出にくいかも系  
- [ ] ピリいる＝前哨確定にしない  

優先データ: pillager_outpost, monument, Witch_hut, mansion（illager 系）。  
`cherry-grove` 等 id 表記ゆれは実装時に正規化確認。

---

## 実装順（PR）— 正本は SM 文書

**旧順 A→B→C→F→E は廃止。**  
実施順・受け入れ条件の正本: **[player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md)**

| PR | 内容 | 本計画の節 | 状態 |
|---|---|---|---|
| **A** | 中立 fallback + visual ログ | ①-A / ③ fallback | ✅ |
| **B** | ② 照合エンジン + details（プロンプト厚みは打ち切り） | ② | ⏸ エンジンのみ |
| **S1** | `reply_stance` + プロンプト規則カット | 2.5b 執行を SM へ | ✅ |
| **S2** | 出力白リスト sanitize | 2.5b 音・候補のコード担保 | 次 |
| **C** | ①-D visual バッファ | ①-D | |
| **S3** | identify 高信頼 → SM 骨子 | 2.5b 成功イメージ | |
| **F′** | ④ related_mobs + **SM** plausibility 1 行 | ④ | 旧 F |
| **E′** | tactics 整理・プロンプト縮退確認 | ③ | 旧 E（意味変更） |
| **D** | adapter LOS/装備（実測後・必要時前倒し） | ①-B/C | 旧 D |

```text
A ✅ → B ⏸ → S1 → S2 → C → S3 → F′ → E′
                              ╰ D は実測で痛いとき前倒し
```

### 旧 PR からの読み替え

| 旧 | 新 |
|---|---|
| B の「規則を厚くする」 | **やらない** → S1 で薄くする |
| C | **C**（S2 のあと） |
| F（LLM が biome 判断） | **F′**（SM が1行。LLM 推論禁止） |
| E（プロンプト仕上げ＝規則追加） | **E′**（規則削減後の口調・tactics） |

---

## 成功条件（プロダクト）

1. 観測できる脅威への問い → 高頻度で種名・特徴に触れる  
2. 観測なし → 「見えんけど〜かもしれん」。カタログ外を捏造しない  
3. 視界ギャップでもプレイヤー観測を否定して落とさない（**S1/S3 でコード寄り**）  
4. 音の「空から種名を足さない」と経路分離（**S2 白リスト**）  
5. poetic / 通称タグが検索と描写の両方に効く  
6. 新話題は **カタログ更新**で伸びる（コード分岐を増やさない）  
7. structure×biome で「ありうる／出にくい」を **SM のカタログ根拠1行**で言える（F′）  
8. 方針執行がプロンプト長文に依存しすぎない（[SM 文書](player-chat-sm-vs-prompt.md)）  

---

## レビューで潰した矛盾・重複（2026-07-15）

| 論点 | 以前 | いまの正 |
|---|---|---|
| 通称の置き場 | `spoken_aliases` 必須のように読める箇所と、visual_tags 済の記述が混在 | **poetic で可。aliases は任意** |
| 「ババアは aliases が無いとヒットしない」 | 実データは visual_tags にあり矛盾 | **タグにあればヒット** |
| 全体像 | ①②③のみで④が浮いていた | 全体像に④を組み込み |
| プロンプト規則 | 2.5c と 3.4 で重複 | 方針は §2.5b。**執行は S1〜S3（プロンプト長文に積まない）** |
| scene_tags「前哨基地」 | 文字列マッチ禁止と読める余地 | **②の検索語には可。④の id リンクには使わない** |
| 視界を伸ばす vs 会話型 | ①-B と 2.5b の優先が曖昧 | **会話型が主。scan 拡張は慎重・副** |
| 音 vs 視界ギャップ | 別節に散在 | **§2.5b の表に集約** |
| PR-E と fallback | A と E の両方 | **A=最低限、E′=縮退後の磨き** |
| PR 順 | A→B→C→F→E | **A→B⏸→S1→S2→C→S3→F′→E′**（[SM 文書](player-chat-sm-vs-prompt.md)） |

---

## 次の作業

1. **S1** ✅  
2. **S2**（白リスト sanitize）← 次  
3. **C** visual バッファ  
4. LOS が実測で酷いときだけ **D** 前倒し  
