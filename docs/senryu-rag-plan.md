# Senryu-RAG 実装プラン

**ブランチ:** `Senryu-RAG`  
**関連:** [rag.md](rag.md), [haiku-architecture.md](haiku-architecture.md), [dialogue-design.md](dialogue-design.md)

川柳（senryu/haiku）向けに、カタログ知識をどう足すかの実装方針。  
「全部ベクトル化」ではなく、**既存の直引きを使い切ってから**、必要なら Simple Vector を載せる。

---

## 0. 監査結果（無駄・取りこぼし・二重）

### もう使っている（＝同じ中身を RAG に載せても二重）

| 既存経路 | 何をやっているか |
|---|---|
| `entry_catalog.mob_poetic_tags` | mob の `poetic.*_tags` + `role` をフラット化 |
| `HaikuMixin._haiku_tags` | 上を最大16個 `haiku_tags` に載せ、プロンプトの **「詩語ヒント」** へ |
| `HaikuFeature.tags`（平和 mob） | feature 候補にも poetic を付与 |
| `feature_candidates` / `candidate_tensions` | 状況の候補・取り合わせをコード側で構築 |
| biome ラベル / group / temp・降水・雪Y | `biome_traits` として既に details 入り |

→ **見えている mob の poetic を Chroma に入れて retrieve し直すのはほぼ無駄。**  
いまの句に既に「詩語ヒント」として載っている。

### カタログにあるのに川柳経路が使っていない（＝取りこぼし・ここが本命）

| 資産 | 状況 |
|---|---|
| biome の `note` | entry にはある（数は少なめ）。haiku は temp/降水だけ。**note 未注入** |
| structure の `note` / overview | **構造物入場の narration では使用**。**haiku には未注入** |
| nearby block の `note` | block エントリに短い note があるものあり。haiku は **日本語ラベル列だけ** |
| poetic の構造（visual/sound/… の区分） | フラットタグに潰している。区分を残した短文にすると LLM が使いやすい可能性 |
| 敵対 mob poetic | 川柳は脅威中は基本起動しないので、平和時の句では優先度低 |

### 載せるな（固定セリフ・別経路）

| 資産 | 理由 |
|---|---|
| `data/fallbacks/**` | 失敗時ネット。RAG に入れると失敗句を模倣しやすい |
| `data/responses/ques/**` | ルール発話専用。すでに別経路 |
| `data/mobs/ambient_reactions.json` | ambient 専用 |
| `dogido_tactics` | 戦闘 chat 用。川柳に混ぜると攻略口調になる |
| combat `priority` | 戦闘優先度 ≠ 詠みたい度 |

### 結論（方針の芯）

```text
① まず「ID が分かっているもの」は entry_catalog 直引きで使い切る
   （既存パターンの延長。embed 不要・二重なし）

② ベクトル RAG は、直引きで足りないときだけ
   （似た情景語・横断ヒント・将来の教育知識 など）
   かつ「見えている ID の poetic 再取得」は禁止
```

ブランチ名は Senryu-RAG でも、**第1波の本体は「カタログ使い切り」**。  
ベクトルは **第2波**（または需要が出てから）。  
「RAG」を広義に「外部知識を句に足す」と読めば、①もこのブランチの成果になる。

---

## 1. ゴール / 非ゴール

### ゴール

1. 川柳プロンプトに、**いまの場面の観察文**が欠落なく載る（biome/structure/block note + 整理した poetic）
2. 既存の haiku フロー・fallback・状態機械を壊さない
3. そのうえで必要なら、**直引きと被らない** Simple Vector を後付けできる形にする

### 非ゴール

- GraphRAG / ネット検索 / `player_chat` 本接続 / M5Stack
- JSON 全件への `rarity`
- fallbacks・ques のベクトル化
- **見えている mob poetic の再インデックス用途の RAG**（二重）

---

## 2. 役割分担（完成形）

```text
GameEvent
  └─ HaikuContext（既存）
       ├─ feature_candidates / candidate_tensions     … コードが選ぶ
       ├─ haiku_tags（mob poetic・既存）              … ID 直引き
       ├─ catalog_notes  【NEW・第1波】               … biome/structure/block note
       ├─ poetic_lines   【NEW or 強化・第1波】       … 区分を残した短文（任意）
       └─ observation_hints 【NEW・第2波・任意】      … ベクトル retrieve
            （直引きで得た id と同じ hit は捨てる）

  → irony / scene / haiku プロンプト
```

| 層 | 実装 | 知識源 | いつ使う |
|---|---|---|---|
| A. 直引き | `entry_catalog` + HaikuMixin | 今の biome / structure / nearby blocks / passive mobs の ID | **常に（第1波）** |
| B. ベクトル | `dogido_server/rag/` | 直引きに載らない横断知識 | **第2波。オフでも動く** |

---

## 3. 第1波 — カタログ使い切り（ベクトルなし）

### 3.1 やること

**`HaikuContext` に「いまの ID から取れる観察文」を足す。**

1. **biome note**  
   - `_biome_entry` は既にある → `note` があれば details へ  
   - 例: `catalog_notes: ["雪のタイガ: 雪が降ると葉が白く…"]`

2. **structure note**  
   - `event.world.structure` / `state.current_structure` があるとき  
   - narration と同じ structure entry 系を **haiku でも読む**（新規パーサ不要）

3. **nearby block notes（上位数個）**  
   - 既に `block_entry` がある → ラベルだけでなく短い `note` を最大 N 件  
   - note が空ならラベルのみ（現状維持）

4. **poetic の見せ方（軽く）**  
   - フラット16タグはそのまま残してよい（互換）  
   - 追加で「主役 mob 1〜2体だけ」`role` + 代表タグを1行にまとめた `poetic_lines` を足すと、  
     ベクトル化なしで「誰の詩語か」がはっきりする  
   - **同じタグ列を2回プロンプトに載せない**（`haiku_tags` か `poetic_lines` のどちらか主、片方は短く）

5. **プロンプト**  
   - `build_haiku_*_messages` に「カタログ観察」ブロックを1つ  
   - ルールは既存どおり: **状況にない実体を増やさない**

### 3.2 やらないこと（第1波）

- chromadb / sentence-transformers 導入
- 全 entries のインデックス
- rarity フィールド

### 3.3 モジュール

新規巨大パッケージは不要。既存に寄せる:

| 場所 | 内容 |
|---|---|
| `entry_catalog.py` | 必要なら `biome_note` / `block_note` の薄いヘルパ（既存 loader 再利用） |
| `haiku_context.py` | `catalog_notes: tuple[str, ...]` 等フィールド追加 |
| `mixins/haiku.py` | `_haiku_context` で note 収集 |
| `llm/haiku_prompts.py` | 観察ブロック表示 |
| `tests/test_haiku.py` 等 | note がある fixture で details に入ることを検証 |

### 3.4 完了条件

- 雪タイガ等 **note 付き biome** で、irony/scene/haiku のいずれかのプロンプト材料に note が入る
- structure 滞在中に structure note が入る（データがある場合）
- note なし環境では従来どおり（空リスト）
- 既存 haiku テストが通る

### 3.5 第1波で効きが弱いとき

- biome note 自体が **約10/65** と薄い → **コンテンツ作業**（note 追記）が先。ベクトルでは解決しない
- block note が疎 → 同上
- mob poetic は全 mob 充実済み → ここは「見せ方」改善が主

---

## 4. 第2波 — 本当の Vector RAG（直引きと被らせない）

第1波のあと、まだ「似た情景の語彙が欲しい」「教育的ヒントが欲しい」なら入れる。

### 4.1 インデックスしてよいもの

| 載せてよい | 理由 |
|---|---|
| biome note / structure 説明文（全文） | 直引きは「今の ID だけ」。類似 biome の **描写語だけ**借りる用途 |
| poetic を **短文化したコーパス** | ただし retrieve 後に **今の scene の id/label と一致する hit は捨てる or 降格**（二重排除） |
| （将来）下手さ・観察の教育短文 | カタログ外のメタ知識 |

| 載せない | 理由 |
|---|---|
| 見えている mob の poetic を「再取得」 | 既に haiku_tags |
| fallbacks / ques | 固定セリフ汚染 |
| tactics | 戦闘用 |

### 4.2 二重排除ルール（必須）

```text
retrieve 結果 hit について:
  if hit.id in current_scene_ids:
      drop  # 直引き側が担当済み
  if hit が新しい mob/block 固有名を主役に勧める:
      固有名を落とし、描写語だけ残す or drop
```

### 4.3 技術（第2波で初めて入れる）

- Chroma 直接 + 軽量 embed（LlamaIndex は必須にしない）
- `dogido_server/rag/` + `scripts/build_senryu_rag_index.py`
- `rag_enabled`（index 無しはスキップ）
- top_k 2〜3、プロンプトは短い `observation_hints` のみ
- 失敗時は hints 空で従来フロー（川柳自体は落とさない）

### 4.4 第2波をスキップしてよい条件

第1波 + note コンテンツ追加で句の語彙が十分なら、**ベクトルは後回しで正解**。  
このブランチで「カタログ使い切りまで」マージしてよい。

---

## 5. 実装フェーズ（PR 分割）

### PR1 — カタログ観察の haiku 注入（本命）

- biome / structure / nearby block の note → HaikuContext
- プロンプト1ブロック
- テスト
- **依存ライブラリ追加なし**

### PR2 — poetic の見せ方整理（小さく）

- 主役 mob の poetic を1行要約（role 中心）
- `haiku_tags` との重複を整理
- 任意。PR1 に含めてもよいが差分が大きくなるなら分離

### PR3 — Vector RAG（任意・需要が出てから）

- chunk（**直引きと役割が被らない設計**をテストに明記）
- retrieve + 二重 drop
- プロンプト `observation_hints`
- docs 更新

### PR4 — ドキュメント

- [rag.md](rag.md) を「①カタログ直引き ②任意ベクトル」の二段に書き換え
- [haiku-architecture.md](haiku-architecture.md) に観察データの流れを追記
- 旧 rag メモの「全部 data/ を LlamaIndex」は **採用しない** と明記

---

## 6. 以前の案からの変更点

| 以前 | 本プラン |
|---|---|
| まず Chroma + 全 poetic インデックス | **まず entry_catalog で note 使い切り** |
| RAG が poetic の主供給 | poetic の主供給は **現状の ID 直引きのまま** |
| rarity 検討の余地 | **付けない** |
| LlamaIndex 前提気味 | 第2波でも **直接 Chroma で十分** |
| haiku 接続が後回し | 価値が出る接続は **第1PR から**（ベクトルなし） |

---

## 7. Key Decisions

1. **見える ID の知識 = entry_catalog 直引きが正本。ベクトルでやり直さない**
2. **第1波は note（biome/structure/block）の haiku 注入。依存ゼロ**
3. **fallbacks/ques は知識源にしない**
4. **レア度フィールドは作らない**
5. **ベクトルは第2波・任意。入れるなら二重 drop 必須**
6. **biome note が薄い問題はコンテンツ作業。RAG の代替にしない**
7. **状態機械・panic・cue は触らない**

---

## 8. 着手チェックリスト

- [ ] `Senryu-RAG` で作業
- [ ] PR1: `_haiku_context` に note 収集を足すところから（rag パッケージはまだ作らない）
- [ ] 迷ったら: 「このデータ、今の event の ID で直引きできる？」→ Yes ならカタログ、No だけベクトル候補
- [ ] 「詩語ヒントに既にある？」→ Yes なら足さない

---

## 9. 最初の具体タスク（PR1）

1. `HaikuContext` に `catalog_notes: tuple[str, ...]` を追加し `*_details()` に載せる
2. `_haiku_context` で:
   - biome entry の `note`
   - structure entry の `note`（あれば）
   - nearby 上位ブロックの `note`（空はスキップ、最大3）
3. `haiku_prompts` の irony / scene / haiku に「カタログ観察」節
4. ユニットテスト: note 付き biome で details に文字列が入る
5. 手動: 実際の句で note の語が活きるか確認

これで「使えるものは使う」「二重にしない」がコード上はっきりする。ベクトルは、その結果を見てから判断する。
