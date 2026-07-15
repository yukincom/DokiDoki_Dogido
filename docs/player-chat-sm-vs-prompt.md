# player_chat: 状態機械とプロンプトの分担

**日付:** 2026-07-15  
**状態:** **実装計画の正本**（優先順・PR 定義）  
**関連:** [pillager-banner-chat-plan.md](pillager-banner-chat-plan.md)（観測・トピック・structure の詳細）、[sound-identity-plan.md](sound-identity-plan.md)、[research/code-review-player-reactivity-2026-07-02.md](research/code-review-player-reactivity-2026-07-02.md)

---

## 優先判断（確定）

| 選択 | 内容 |
|---|---|
| **優先する** | プロンプト規則の厚みより **状態機械（＋ sanitize）へ判断を寄せる** |
| **後回し** | 旧 PR-B の「プロンプト仕上げ」、旧 PR-E の「規則追加型の磨き」 |
| **再利用する** | PR-B 途中成果の **照合エンジン**（`find_catalog_topics`） |

**旧 PR-B 以降を予定どおり完走しない。**  
順序と各 PR の中身を本ドキュメントの **S1 → S2 → C → S3 → F′ → E′** に切り替える。  
観測・カタログ詳細の仕様は引き続き [pillager-banner-chat-plan.md](pillager-banner-chat-plan.md) を参照する。

---

## PR 進行状況

| 項目 | 状態 |
|---|---|
| **PR-A** | ✅ 完了（中立 chat fallback + `player_chat_visual` ログ） |
| **PR-B** | ⏸ **途中停止**（照合エンジンのみ採用。プロンプト厚みは打ち切り） |
| **S1** | ✅ 完了（`reply_stance` + プロンプト規則カット） |
| **S2 以降** | 未着手 ← **次はここ** |
| 旧 PR-C / F / E / D | 下表の **C / F′ / E′ / D** に再定義 |

### PR-B を止めている理由

1. `find_catalog_topics` と details 載せ・§2.5b プロンプト骨子までは **入れた**。  
2. `prompts.py` 分割は実施済み。  
3. これ以上プロンプト規則を足すより、**stance / 白リスト / intent 直答**を先に固める。  
4. **PR-B の残り（体感磨き・規則追加）は再開しない。** 相当ニーズは S1〜S3 で満たす。

**PR-B 成果物（残す・捨てない）:**

- `entry_catalog.find_catalog_topics` / `format_catalog_topic_hints`
- player_chat details の `catalog_topic_hints` / `catalog_topic_ids` とログ
- §2.5b 相当のプロンプト骨子（**S1 で薄くする**前提）
- `tests/test_catalog_topics.py`

**やらない（プロンプト増強に寄せない）:**

- 話題規則のさらなる長文化  
- 旧 PR-E を PR-B に前倒しすること  
- LLM に biome×structure を推論させること  

---

## 結論（要約）

- **事実・可否・候補集合・否定してよいか** → 状態機械（＋ sanitize）  
- **関西弁・相棒感・自然な一言** → LLM  

規則＝コード／テンプレ、口調＝LLM。

---

## いまの分担（事実）

| 層 | やっていること |
|---|---|
| **状態機械 / routing** | hush・slash、敵の数、ドラゴン方向、川柳系、inventory 必要時だけ注入、place_context、hearing バッファ、topic 照合、tactics、character_mode |
| **プロンプト** | 上記を再掲＋行動規則 十数本（見えんけど／音捏造禁止／場所決めつけ禁止 等） |
| **事後** | unusable → fallback、禁止助言チェック（一部） |

---

## 理想の 1 往復

```text
route_player_input
  → intent: hush / count / dragon / haiku / inventory? / identify? / free

状態機械が「答えの骨格」を決める
  stance: saw | hypothesis | clarify | none
  facts: threat_summary, place_line, hearing_names[]
  candidates: catalog labels（白リスト）
  policy_line: 1行だけ

LLM（必要なときだけ）
  骨格＋白リスト内でセリフ化

sanitize
  白リスト外の種名 / 禁止助言 → fallback
```

---

## 実装 PR 定義（新・正本）

```text
A ✅ ── B ⏸（エンジンのみ）
              │
              ▼
            S1  reply_stance + プロンプト規則カット
              │
              ▼
            S2  出力白リスト sanitize
              │
              ▼
             C  visual バッファ（stance=saw の材料）
              │
              ▼
            S3  identify 高信頼 → SM 骨子（LLM 任意）
              │
              ▼
            F′  structure×biome を SM 1 行（旧 F）
              │
              ▼
            E′  プロンプト縮退の仕上げ（旧 E の意味変更）
              │
              ╰─ D  adapter LOS/装備（実測で痛いときだけ前倒し可）
```

| PR | 内容 | 依存 | 旧 PR との対応 |
|---|---|---|---|
| **S1** ✅ | `reply_stance`（`saw` / `hypothesis` / `clarify` / `none`）を narration で決定し details へ。user プロンプトから **mode_hint 重複・冗長な place/hearing/topic 長文規則**を削り、**stance 依存の短い policy_line** に置換 | A, B エンジン | 旧 B 残り・旧 E の一部を前倒し |
| **S2** | 出力に許す種名の **白リスト**（topic ∪ visual ∪ hearing named の union）。sanitize でリスト外種名を reject → 中立 fallback | S1（stance と同時でも可） | 新規。プロンプトの「捏造するな」をコード担保 |
| **C** | ①-D `recent_visual_memos` + threat_summary「ついさっき」。stance=`saw` の材料 | A（S1 の直後推奨） | 旧 PR-C そのまま |
| **S3** | topic 高信頼（例: ババア→witch）かつ必要なら **SM 固定骨子**（「見えんけどウィッチかもしれん」系）。LLM は言い回しだけ or スキップ | S1, S2、（C あれば尚良） | 旧 B 体感・identify intent |
| **F′** | structure `related_mobs` + **SM が** `plausibility_hint` 1 行を details に載せる。LLM に生成可否を推論させない | S1、topic id（B エンジン） | 旧 PR-F。手段を SM 直計算に固定 |
| **E′** | tactics 合成の整理、fallback 磨き、**プロンプトは口調＋骨格言い換えのみ**になっていることの確認 | S1〜S3, C,（F′ あれば尚良） | 旧 PR-E。「規則を足す」から「規則を削ったあと」へ |
| **D** | adapter LOS/confirm・`equipment_hints`（実測後） | ログ | 旧 PR-D。独立。LOS 酷いとき前倒し |

### 各 PR の受け入れ（要約）

| PR | 受け入れ |
|---|---|
| **S1** | details に `reply_stance`（と短い policy）。user 規則 bullet が常時十数本より明らかに減る。hypothesis 時に「おらへん」で落とす指示が promp 長文に依存しない |
| **S2** | 白リスト外の種名（例: プロンプトが NPC と言った）が unusable / fallback になるテスト |
| **C** | 直近に pillager がいたフレームで chat 時 visual 0 でも「ついさっき」が threat_summary に残る |
| **S3** | 「なんだあのババア」+ visual 0 で、骨子または白リスト内のウィッチに安定して触れる（LLM オフでも最低限） |
| **F′** | taiga +「前哨ある？」→ SM 生成の「ありうる」行。ピリ視認のみで前哨確定しない |
| **E′** | プロンプトが stance/policy 中心。旧 §2.5b 長文が残っていない／最小 |
| **D** | 実測で正面視認が載る率の改善（任意） |

### 推奨実施順

1. **S1 → S2**（正しさの土台）  
2. **C**（観測の穴埋め・stance 材料）  
3. **S3**（旗・ババアの体感）  
4. **F′**（前哨会話）  
5. **E′**（締め）  
6. **D** は「見えてるのに 0」が実測で続くとき前倒し  

---

## プロンプト → SM に寄せる対応表

| いまプロンプト | SM / sanitize | 担当 PR |
|---|---|---|
| 見えんけど候補かも／否定するな | `reply_stance` + policy_line | S1 |
| 候補は JSON 内だけ | 白リスト sanitize | S2 |
| 音が空なら種名を当てるな | 白リスト（hearing 空なら名前なし） | S2 |
| mode 口調の二重 | user mode_hint 削除 | S1 |
| 場所の長文ルール | place_line を正とし規則縮小 | S1 |
| あれ何／ババア | identify 骨子 | S3 |
| 前哨ありうる？ | `plausibility_hint` SM 計算 | F′ |

---

## LLM に残すもの

- 関西弁・相棒感・文字数感  
- 曖昧雑談  
- 骨格があるときの言い回し差  

---

## 旧 PR 表との対応（pillager 計画）

| 旧 | 新 |
|---|---|
| PR-A | A ✅ |
| PR-B 全部 | **エンジンのみ残置。厚みは S1〜S3 に再配置** |
| PR-C | **C**（順は S2 のあと） |
| PR-F | **F′**（SM 1 行。LLM 推論禁止を明記） |
| PR-E | **E′**（規則削減後の仕上げ） |
| PR-D | **D**（変更なし・独立） |

---

## 成功条件

1. user プロンプト規則が **stance 依存の短い policy** になる  
2. 種名・見えた／見えてへんが **コードと sanitize で担保**される  
3. 旗・ババア等高信頼 topic は **LLM オフでも最低限破綻しない**（S3）  
4. structure×biome は **SM の1行**で答え、プロンプトに推論を書かない（F′）  
5. 旧「PR-B を厚く完走」パスには戻らない  
