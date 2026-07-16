# player_chat 雑談3本柱 — 実装計画

**日付:** 2026-07-16  
**状態:** **P1〜P4 実装済み**（P5 fallback 任意は未）  
**関連:** [player-chat-topic-overfit-plan.md](player-chat-topic-overfit-plan.md)、[player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md)

---

## ゴール（3本柱）

| # | 柱 | 一言 |
|---|---|---|
| **1** | **none を守る** | 弱い語・ASRゆらぎで偽モブ identify に引きずらない |
| **2** | **本当の観測だけ短く** | visual / passive / hearing / ついさっき を短い事実として渡す |
| **3** | **5往復＋LLM で相槌** | 履歴は伸ばさない。雑談のリズムは LLM＋既存履歴 |

成功イメージ:

| 入力 | 期待 |
|---|---|
| もしもし / マイク直った / すいません | stance=**none**、自然な相槌。偽種名レールに入らない |
| 大きい木／気があるね | stance=**none**（または clarify）。シロクマ骨子なし |
| なんだあのババア | stance=**hypothesis**、ウィッチ可 |
| 変な旗 | hypothesis、ピリ可 |
| 川にサケがいる状況でサケの話 | 観測ラベル「サケ」が事実に載る。弾かれない |
| 前哨基地ある？（タイガ） | hypothesis + plausibility（F′） |

---

## 現状の問題（コード上）

| 場所 | 問題 |
|---|---|
| `resolve_reply_stance` | `if topic_hits: return hypothesis` → **1 ヒットで identify レール** |
| `find_catalog_topics` | 「大きい」「きれい」など **GENERIC タグ単独**で hit |
| narration details | hypothesis でなくても **topic hints を載せうる** → モデルが引っ張られる |
| `build_identify_skeleton` | hypothesis なら骨子。弱い二択（シロクマかスニッファー）も出る |
| 観測のプロンプト載せ | threat / hearing はある。**passive は allowed には入るが「見た事実」1行が弱い** |
| 履歴 | 5往復のまま維持（変更しない） |

---

## 柱1: none を守る

### 1.1 stance 入場条件（T-A）— 最優先

**ファイル:** `dogido_server/player_chat_policy.py` → `resolve_reply_stance`

**変更後ロジック（案）:**

```text
1. has_visual / threat に「視認」 → saw
2. topic_hits を「identify 用に使える hit」にフィルタ（1.2）
   usable_hits が空 → （3へ）
3. usable_hits があり、かつ次のいずれか → hypothesis
   a. identify 意図（何/誰/あいつ/あれ 等。既存 _CLARIFY_HINTS を整理）
   b. 高信頼: 非 GENERIC 語を含む match、または score 閾値＋ top 独走
   c. 観測一致: hit.entry_id ∈ visual∪passive∪hearing 解決 id
4. identify っぽいが usable_hits 空 → clarify
5. それ以外 → none
```

**ポイント:**  
「topic が1件でもある」だけでは hypothesis にしない。  
「大きい木があるね」は usable_hits 空 or 意図なし → **none**。

### 1.2 GENERIC タグ単独ヒットを落とす（T-B）

**ファイル:** `player_chat_policy.py` または `entry_catalog.find_catalog_topics` の後処理

```text
GENERIC_TOPIC_TERMS = frozenset({
  "大きい", "小さい", "きれい", "白い", "黒い", "赤い", "青い",
  "長い", "短い", "丸い", "速い", "怖い", "変な", "強い", "弱い",
  # 必要なら実測で追加。旗・ババア・とんがり帽子・前哨基地は入れない
})
```

ルール:

- hit の `matched_terms` が **すべて GENERIC** → identify 用 hit から除外  
- matched に **1つでも非 GENERIC**（旗、ババア、前哨基地…）→ 残す  
- `find_catalog_topics` 自体は描写用に残してもよいが、**player_chat 経路ではフィルタ後だけ使う**

### 1.3 hints / 骨子 / 白リスト enforce を stance に連動（T-C + 既存）

**ファイル:** `narration._render_player_chat_reply`

| stance | catalog_topic_hints | identify_skeleton | speech_whitelist_enforce | plausibility |
|---|---|---|---|---|
| **none** | **載せない** | なし | False（現行） | 載せない※ |
| **clarify** | 載せない | なし | False | 載せない |
| **hypothesis** | usable_hits のみ | 高信頼時のみ | True | structure 語があるとき |
| **saw** | 任意（短く） | なし（視認優先） | True | 同上 |

※「前哨基地ある？」は usable 非 GENERIC → hypothesis なので F′ は残る。

**S3 骨子追加条件:**

- matched に非 GENERIC がある  
- 同点トップ2がどちらも GENERIC 由来なら骨子禁止  

### 1.4 テスト（柱1）

| ケース | 期待 stance | 期待しないもの |
|---|---|---|
| 大きい気があるね | none | シロクマ、スニッファー骨子、hints |
| 大きい木があるね | none | 同上 |
| きれいな〜 | none | 熱帯魚 hints |
| なんだあのババア | hypothesis | — |
| 変な旗持ってる | hypothesis | — |
| もしもし | none | 種名レール |
| あれ何？（特徴なし） | clarify | 種名骨子 |

---

## 柱2: 本当の観測だけ短く

### 2.1 観測ソース（既にあるもの＋整理）

| ソース | 現状 | 計画 |
|---|---|---|
| visual_threats | threat_summary | 維持 |
| recent_visual_memos | ついさっき 視認 | 維持 |
| passive + recent_passive | **allowed のみ** | **短い observation 行にも載せる** |
| hearing + buffer | hearing_summary（空なら非表示） | 維持 |
| topic（弱い） | hints に載りうる | **none では載せない**（柱1） |
| topic（強い） | hints | hypothesis のみ |

### 2.2 `observation_summary` 1ブロック（新規 details）

**組み立て（narration）:** 優先順で最大 **3行** 程度。

```text
観測メモ（短い事実。無い行は出さない）:
- 視認: ピリジャー 前 12マス
- ついさっき: ウィッチ 右
- 近くの生き物: サケ、ウシ
- 音: ゾンビっぽい 左 far
```

ルール:

- **今フレーム or バッファ retention 内だけ**  
- 種名はカタログ label 解決できたものだけ（hearing と同じ思想）  
- トピック仮説の種は **ここに入れない**（観測と仮説を混ぜない）  
- 重複（視認とついさっきが同じ種）は1行にまとめてよい  

**プロンプト (`player_chat_prompts`):**

- `threat_summary` / 分散していた事実を **`observation_summary` に集約**してもよい（段階的で可）  
- 最低限: threat を残しつつ、**passive 1行を追加**  
  `近くの生き物: サケ`（passive があるときだけ）

### 2.3 allowed_speech_labels と観測の一致

- allowed = **観測由来 ∪（hypothesis 時のみ usable topic labels）**  
- none 時: enforce オフだが、**観測名を observation に載せる**ことで LLM が正しく触れる  
- 動物園: 観測があれば載る → habitat 不要  

### 2.4 digest（任意・小さめ）

- 既存 `event_digest`（〜8件）はそのまま5往復と併用  
- 変更するなら: ambient「サケを見た」が確実に digest に入っているか確認（既に `〜を見た` あり）  
- **reject / style_mismatch の文は dogido 履歴に載せない**（汚染防止。service 側を確認・必要なら修正）

### 2.5 テスト（柱2）

| 状況 | details に載る | 載らない |
|---|---|---|
| passive に salmon | 近くの生き物: サケ | シロクマ |
| visual 0・バッファ pillager | ついさっき 視認 ピリ… | — |
| 大きい木・観測なし | 観測ほぼ空 | topic シロクマ |
| ババア・観測なし | topic ヒントのみ（hypothesis） | 偽の視認行 |

---

## 柱3: 5往復＋LLM で相槌

### 3.1 変えないもの

| 項目 | 値 |
|---|---|
| `DialogueContext.max_utterances` | **10（5往復）のまま** |
| max_digest_notes | 8 のまま（必要なら後で） |
| 履歴の全文検索 RAG | **やらない** |

### 3.2 プロンプト（none 時の形）

```text
参考傾向:
- 相棒の返事。実況・定型あいさつにしない
- 【答え方】雑談として自然に。根拠のない種名捏造はしない
- （戦闘時のみ静止禁止など）

本番:
【直近の会話】…最大5往復…
【直近の出来事メモ】…あれば…
プレイヤー:「…」
場所メモ: …
時間帯: …
答え方スタンス: none
観測メモ: …あれば短く…
（topic hints なし）
→ 12〜42字で一言
```

### 3.3 LLM がやりやすい条件（実装チェックリスト）

- [ ] none で **偽 topic / 偽骨子が details に無い**  
- [ ] 観測があるときだけ **短い事実行**がある  
- [ ] style reject が none で種名を殺さない（現行 enforce オフ）  
- [ ] fallback は unusable 時のみ。**none の相槌を「ようわからん」にしない**のが理想  
  - 任意改善: none + unusable のときだけ、もう少し相槌寄りの fallback  
  - 例: 「おう、聞こえてるで」は以前問題になったので使わない。  
    「うん」「そうやな、もうちょい Tra 言って」程度の中立相槌を別キーにしてもよい  

### 3.4 履歴に載せないもの

| 載せない | 理由 |
|---|---|
| style_mismatch / unusable で捨てた LLM 生文 | サケ蒸し返しループ |
| identify 誤骨子がプレイヤーに出なかった場合 | 同上 |
| cue 擬音（既存） | 維持 |

**実装確認:** `service._update_dialogue_context` が **実際に emit された action.text だけ**を `add_dogido` しているか。emit 前に fallback 置換されていれば OK。

---

## データ流（変更後）

```text
user_text
  → find_catalog_topics (raw)
  → filter_usable_topic_hits (GENERIC 除去)
  → resolve_reply_stance (saw / hypothesis? / clarify / none)
  → observation_summary (visual+buffer+passive+hearing のみ)
  → allowed = 観測 ∪ (hypothesis 時 usable topic labels)
  → enforce_wl = saw|hypothesis
  → hints / skeleton / plausibility = hypothesis 時のみ（条件付き）
  → details → LLM
  → sanitize
  → emit された文だけ履歴5往復へ
```

---

## PR 分割（実装順）

| PR | 柱 | 内容 | 主なファイル |
|---|---|---|---|
| **P1** | 1 | GENERIC フィルタ + stance 入場条件 | `player_chat_policy.py`, tests |
| **P2** | 1 | none/clarify で hints・骨子・plausibility を載せない | `narration.py`, prompts はほぼそのまま |
| **P3** | 2 | `observation_summary`（passive 行含む）を details + プロンプト | `narration.py`, `player_chat_prompts.py` |
| **P4** | 1+2 | 回帰テスト一式（木／ババア／旗／サケ観測／もしもし） | tests |
| **P5** | 3 | （任意）none 用 fallback の見直し、履歴汚染の確認 | fallbacks, service |

推奨: **P1 → P2 → P4 の一部 → P3 → P4 完了**。  
P1 だけで「大きい気→シロクマ」は止まる。

---

## 受け入れ（プロダクト）

1. 森で「大きい木／気」→ **シロクマ／スニッファー骨子が出ない**、stance=none  
2. ババア・旗・前哨 → **今どおり identify / plausibility**  
3. サケが近くにいる → 事実 or allowed にサケ。雑談でサケに触れて **style で落ちない**  
4. もしもし等 → **none + 相槌**。ようわからん連打にならない（P5 まで含むとより良い）  
5. 履歴は **5往復のまま**

---

## 明示的にやらないこと

- 会話履歴の延長（10往復化など）  
- 履歴ベクトル検索  
- 全 mob の spawn_biomes  
- 今すぐ VLM（将来枠は overfit 計画に記載済み）  
- ASR 大規模修正  

---

## 実装状況

| PR | 状態 |
|---|---|
| P1 GENERIC + stance | ✅ |
| P2 none で hints/骨子/plausibility 非載荷 | ✅ |
| P3 observation_summary | ✅ |
| P4 回帰テスト `tests/test_player_chat_casual.py` | ✅ |
| P5 none 用 fallback 見直し | 任意・未着手 |

### 変更ファイル（要約）

- `dogido_server/player_chat_policy.py` … usable filter / stance  
- `dogido_server/state_machine/mixins/narration.py` … 経路分岐 + 観測サマリ  
- `dogido_server/llm/player_chat_prompts.py` … observation 節  
