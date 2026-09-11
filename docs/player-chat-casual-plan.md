# player_chat 雑談3本柱 — 実装計画

**日付:** 2026-07-16  
**状態:** **P1〜P5 + 現在ターンの予定／安全方針 + 有界grounded planner 実装済み**

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
| 前哨基地ある？（タイガ） | 現在構造物IDと一致すれば確認済み。バイオームやピリジャー視認だけでは在ると言わない |
| よしよし、大丈夫そうですね | 会話の続き。`丈夫そう` をラバのvisual tagへ流さない |
| 近くにラバがいるの？（観測なし） | 「いない」とは断定せず、現在観測では未確認と返す |

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
| **none** | **載せない** | なし | True（許可名が空なら種名を生成させない） | 載せない※ |
| **clarify** | 載せない | なし | True | 載せない |
| **hypothesis** | identify actionの usable_hits のみ | 高信頼時のみ | True | identify actionでstructure語があるとき |
| **saw** | 載せない（観測優先） | なし（視認優先） | True | 載せない |

※「前哨基地ある？」は usable 非 GENERIC → hypothesis なので F′ は残る。

**S3 骨子追加条件:**

- matched に非 GENERIC がある  
- 同点トップ2がどちらも GENERIC 由来なら骨子禁止  
- 骨子は LLM の生成材料に限る。LLM 無効・生成失敗・usable 不合格時の
  fallback 本文には流用せず、話題非依存の中立文へ戻す
- 2文字以下の短いカタカナ名は、かな折り畳み部分一致を使わない
  （例: 「いいんじゃないかな」内の「いか」を「イカ」と誤認しない）

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
| hearing + buffer | **プレイヤーが音を明示したときだけ** hearing 注入（`asks_about_sound`） | **済** |
| 標高・気候・積雪 | 現在Y＋気温＋降雪開始高度をコード比較し、閉じた気象事実だけを公開。地表雪は周辺の雪ブロック実測 | **川柳と共通判定で済** |
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
- global の `rain` は現在Yとバイオーム気温で雨／雪へ解決する。降っている雪と、
  実ブロックで確認した地表の積雪は分け、未観測の積雪を断定しない
- Y・Z 座標、気温、降雪開始高度、`downfall` は LLM に渡さない。`downfall` を
  降水確率として解釈させず、現在の降水・雷・降雪環境・実測積雪だけを渡す

**プロンプト (`player_chat_prompts`):**

- `threat_summary` / 分散していた事実を **`observation_summary` に集約**してもよい（段階的で可）  
- 最低限: threat を残しつつ、**passive 1行を追加**  
  `近くの生き物: サケ`（passive があるときだけ）

### 2.3 allowed_speech_labels と観測の一致

- allowed = **コード観測由来 ∪ 現在／過去のplayer発話に実在するカタログ名 ∪（plannerが同定を選んだときだけ usable topic labels）**
- 全通常雑談で enforce を有効にする。assistant履歴だけに現れる種名は許可へ入れず、過去の誤断言を新しい世界根拠にしない
- player発話の種名は、その人の報告として会話を続けるために許可するが、観測済みへは昇格しない
- 動物園: 観測があれば載る → habitat 不要  
- 観測した種に限り、カタログの `observed_speech_aliases` を allowed に加える。現在は商人のラマ→ラマ、洞窟スパイダー→スパイダー、ヒカリイカ→イカ
- 危険な一般化は `observed_speech_rewrite_from_ids` を持つ種だけ、直近10秒の視認・聴取・討伐推定を根拠に白リスト判定前へ戻す。現在は村人ゾンビを「ゾンビ」とした場合だけ「村人ゾンビ」へ修正
- 一般種そのものも同時に観測済み、または同じ一般名に複数の修正先がある場合は曖昧なので修正しない。ガーディアン／エルダーガーディアンのような併存は両方を allowed にし、プレイヤーに合わせる言い方を LLM に任せる

### 2.4 digest（任意・小さめ）

- 既存 `event_digest`（〜8件）はそのまま5往復と併用  
- 変更するなら: ambient「サケを見た」が確実に digest に入っているか確認（既に `〜を見た` あり）  
- **reject / style_mismatch の文は dogido 履歴に載せない**。一回再考へ渡す候補も一時的な同一生成内の文脈だけで、再生完了履歴には載せない

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
- 直近の会話の流れに沿って自然に返す。会話を続けるためだけの質問を足さない
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

- [x] none で **偽 topic / 偽骨子が details に無い**
- [x] 観測があるときだけ **短い事実行**がある
- [x] none でも、観測またはplayer発話にある種名だけを許可し、assistant履歴だけの種名は捨てる
- [x] 会話継続それ自体を目的にせず、確認・相談が必要なときだけ質問する。完了や相槌には問い返しを足さない
- [x] 自然な自己言及・謝罪・方言・比喩は広域禁止語で落とさず、空出力・役割ラベル・英語説明・生成崩れと具体的なgrounding／安全違反だけを検査する
- [x] 不合格時は候補と閉じた理由を同じ会話へ一度返し、意味と人格を残した言い直しを最大1回だけ生成・再検査する。再不合格時だけ、topic hit や identify 骨子を本文にしない**話題非依存の中立 fallback**へ戻す
  - 任意改善: none + unusable のときだけ、もう少し相槌寄りの fallback  
  - 例: 「おう、聞こえてるで」は以前問題になったので使わない。  
    「うん」「そうやな、もうちょい Tra 言って」程度の中立相槌を別キーにしてもよい  

### 3.4 履歴に載せないもの

2026-09-05: 既存の5往復を使うプロンプトに、発言者と返答先を確かめ、訂正・異議・困惑には直前の説明と現在の観測を比較して答え直す方針を追加した。自分の過去の推測を新たな観測事実にせず、分からない点は認める。履歴件数、生成回数、会話以外の操作条件は変更していない。文脈の受け渡しは自動テスト済み、実際の会話品質は実機確認待ち。

| 載せない | 理由 |
|---|---|
| style_mismatch / unusable で捨てた LLM 生文 | サケ蒸し返しループ |
| identify 誤骨子がプレイヤーに出なかった場合 | 同上 |
| cue 擬音（既存） | 維持 |

**実装確認:** player発話は受理時に追加するが、Dogido本文は選択・表示時点では追加しない。`AudioDispatcher` が発話IDつき `completed` を返し、次の直列game eventで回収した本文だけを `add_dogido` する。`failed / cancelled / queue replaced` は履歴と会話由来の川柳材料へ入れない。

---

## データ流（変更後）

```text
user_text
  → 現在ターンの明示予定（return_home のみ。保存しない）
  → 現在フレームの安全方針（地表夕方 or 雷雨。保存しない）
  → completed済み5往復 + 現在入力 + コード観測
  → player_chat_plan（閉じたaction + 発話内evidence + confidence）
  → actionが対象照合のときだけ find_catalog_topics (raw)
  → filter_usable_topic_hits (GENERIC 除去) + 観測ID照合
  → resolve_reply_stance (saw / hypothesis? / clarify / none)
  → observation_summary (visual+buffer+passive+hearing のみ)
  → allowed = 観測 ∪ player発話の実名 ∪ planner同定候補
  → enforce_wl = 全通常雑談
  → hints / skeleton / plausibility = identify + hypothesis 時のみ（条件付き）
  → 未観測の在否・過去誤断言の訂正はコード固定文
  → details → LLM
  → usable sanitize
  → 直近観測に基づく一意な危険一般名の修正
  → style / allowed_speech_labels sanitize
  → 不合格時だけ候補 + コード理由を同じ会話へ返して一回言い直し
  → usable / grounding / style を再検査（再不合格なら固定fallback）
  → player入力を受理
  → 発話IDつき本文を選択・音声queueへ
  → completed された文だけ履歴5往復へ
```

## 有界grounded planner（2026-09-11）

通常 `player_chat` は本文生成の前に、structured kind `player_chat_plan` で一度だけ会話焦点を決める。これは汎用ReActや世界操作toolではなく、次のread-only actionから一件を選ぶ限定plannerである。

- `continue_conversation`
- `check_entity_presence`
- `identify_entity`
- `answer_observation`
- `clarify_reference`
- `correct_previous_reply`

plannerは発話を生成せず、状態変更・保存・assistを実行しない。`action / focus / entity_query / evidence / confidence` を共通structured contractへ通し、evidenceは現在入力を必須にして、直近の実再生済み会話の連続部分だけを許可する。明示在否問い、playerの平叙存在報告、所持品問、音の問いはコード側のrouting hintでactionも検査し、問いを単な相槌へ落としたり、報告を観測照合へ昇格したりさせない。失敗・低信頼・不正JSONは保守的なコードfallbackへ戻る。

談話関係を先に解くため、通常の相槌や評価文はカタログ全件の描写タグ検索へ流さない。対象照合actionだけがカタログを読み、その候補IDをvisual / passive / hearing / 現在構造物 / 乗車中の乗り物 / 視線先entityのコード観測IDへ照合する。候補順位は先に発話だけで決め、関連Mobの視認で別対象へすり替えない。カタログ一致は観測ではない。未観測の在否質問は「現在観測では確認できない」、過去のassistant誤断言は謝罪と現在観測の切り分けをコード固定で返し、「いない」までは断定しない。

5往復、緊急反応、正本DB、assist、workshop、保存条件は変更しない。2026-09-11の全体回帰は **1314 passed、1 skipped、1500 subtests passed**。実Minecraft・実Qwen・実TTSでの自然さと遅延は未確認。

## 発話候補の一回再考（2026-09-11）

`player_chat_plan` の後に作る本文だけを対象に、小さな observe → revise → revalidate を追加した。最初の候補が検査に通れば追加呼び出しはない。不合格時だけ、前の候補をassistant発話、コード由来の一件の理由を次のuser観察として同じプロンプト末尾へ足す。二案目を同じコード検査へ通し、最大2生成で終了する。

採否・fallback・世界事実・状態変更はコードのまま。不合格理由は閉じた種類だけで、モデルに次の行動や保存を選ばせない。このため汎用ReActではなく、通常雑談一文の有界な会話修正である。`repair_requested / repair_accepted / repair_rejected / repair_failed` のログで実会話の効果と追加遅延を比較する。不合格案は再生されず、completed履歴にも入らない。

ローカルQwenの独立テキスト試験では、崩れた匂い質問への初案 `playerはどう？` が `non_japanese_explanation` を受け、日本語だけの二案目へ直った。意図的な `なんか花の匂いがするで。` は `unsupported_olfactory_claim` を受け、非嗅覚の相槌へ一回で戻った。`ドギド:` の無害な話者ラベルは再生成せずcleanだけで外れた。モデル読込後の追加生成は各約0.7秒だった。全Python回帰は **1346 passed、1 skipped、1510 subtests passed**。これは実Minecraft・実TTSや長時間の自然さを証明しない。

### 通常雑談の家らしさ（2026-09-11）

通常雑談へ渡す場所投影では、設定済みリスポーン地点から既存の `home_bed_prompt_distance` 以内にいて、周辺にベッドまたはドアが観測された場合を `home_base` とする。これは所有権や完全な安全を断定せず、「家・拠点らしい場所」という会話上の表現だけを強める。

- 暗さ、低い天井、囲まれ度、洞窟バイオームより `home_base` を先にする。ただし水中と、直近のブロック破壊で確認した採掘中はその現在行動を優先する
- ベッドだけ、ドアだけ、リスポーン地点だけでは足りず、近いリスポーン地点との組み合わせを必須にする
- 窓は家の根拠にしない。窓だけで `home_base` へ昇格させない
- この変更は通常雑談の場所表現だけであり、暗所危険度、洞窟検出、緊急シェルター、安全判定は変更しない

## foreground会話と自動川柳（2026-09-09）

- `casual` が所有している間は友好・中立Mobのambientを止める。敵対警告・雷・夕方注意は止めない
- hostile時は一件だけ話題を保留し、明示再開されなければ戦闘後の受理済みplayer turn 10件で破棄する
- 雑談が無期限に続いても自動川柳は通常10分周期で保留せず、現在のplayer replyの後ろ、または次の安全なqueue境界で割り込む
- 導入は「あっ……ちょっと待って。なんか、浮かんできたかもしれん……。」に固定する
- 再生完了済み直近3 turnを、最大80字・最大3 motif・元turn IDつきの `player_reported_context` soft材料にする。プレイヤー発話由来であり世界の実測事実ではない
- `learning / web` 中は発句周期そのものを凍結する

詳細は [本体の会話所有権・中断・再生確定](main-dialogue-integration.md)。

## 現在ターンの予定と安全方針（2026-08-15）

- `time_phase` と `safety_priority` は別フィールド。`夕方なので帰宅` のような結合文として保存しない
- `safety_priority=seek_safe_place` は、夜警告と共有する地表判定が有効で、現在が夕方または雷雨のときだけ毎フレーム導出する
- 洞窟バイオーム・水中・空が見えない場所・安全な屋内では安全方針を載せない。朝昼や雷雨終了では次フレームから自然に `none` へ戻る
- `player_turn_plan=return_home` は「帰らなくちゃ」「拠点に戻ろう」等、現在発話に根拠があるときだけ。会話メモリや JSONL へ保存しない
- 帰宅予定のターンでは無関係な passive 観測を `observation_summary` から外し、明示予定を優先する
- `respawn_distance` は短い窓の複数サンプルが同方向へ動いたときだけ `approaching / leaving`。単発差分は `unknown`
- 帰宅・避難方針と衝突する追加の遠出提案は style 不合格として、安全な固定 fallback へ戻す
- 「洞窟探検」「家を作る」等の短期目標スロットはまだ設けない。行動ログから目標を推測しない

---

## PR 分割（実装順）

| PR | 柱 | 内容 | 主なファイル |
|---|---|---|---|
| **P1** | 1 | GENERIC フィルタ + stance 入場条件 | `player_chat_policy.py`, tests |
| **P2** | 1 | none/clarify で hints・骨子・plausibility を載せない | `narration.py`, prompts はほぼそのまま |
| **P3** | 2 | `observation_summary`（passive 行含む）を details + プロンプト | `narration.py`, `player_chat_prompts.py` |
| **P4** | 1+2 | 回帰テスト一式（木／ババア／旗／サケ観測／もしもし） | tests |
| **P5** | 3 | topic 非依存の中立 fallback、履歴汚染の確認 | fallbacks, service |
| **P6** | 1+2+3 | 会話焦点を先に解く有界plannerと対象のコード照合 | planner, narration, contracts, tests |
| **P7** | 3 | 広域禁止語の緩和 + 不合格理由を返す一回再考 + 同じ検査で再採否 | llm client, sanitize, prompts, tests |

推奨: **P1 → P2 → P4 の一部 → P3 → P4 完了**。  
P1 だけで「大きい気→シロクマ」は止まる。

---

## 受け入れ（プロダクト）

1. 森で「大きい木／気」→ **シロクマ／スニッファー骨子が出ない**、stance=none  
2. ババア・旗・前哨 → **今どおり identify / plausibility**  
3. サケが近くにいる → 事実 or allowed にサケ。雑談でサケに触れて **style で落ちない**  
4. もしもし等 → **none + 相槌**。ようわからん連打にならない（P5 まで含むとより良い）  
5. 履歴は **5往復のまま**
6. 「大丈夫そう」等の会話継続 → **描写タグから種名を補わない**
7. 在否問い → **カタログ一致だけで存在断言せず、現在観測と照合する**

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
| P5 topic 非依存の中立 fallback | ✅ |
| P6 有界grounded planner + 対象のコード照合 | ✅（コード・自動テスト。実モデル未確認） |

### 変更ファイル（要約）

- `dogido_server/player_chat_policy.py` … usable filter / stance  
- `dogido_server/state_machine/mixins/narration.py` … 経路分岐 + 観測サマリ  
- `dogido_server/llm/player_chat_prompts.py` … observation 節  
- `dogido_server/dialogue/player_chat_planner.py` … 会話焦点と一件のread action、対象照合
