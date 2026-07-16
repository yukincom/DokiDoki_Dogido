# dogido_server パッケージ構成の整理案

**日付:** 2026-07-16  
**状態:** 提案（未実装）  
**背景:** ルート直下がフラットに増え、川柳・player_chat・カタログ・記憶が散在。`haiku_workshop` 等を足す前に置き場の原則を決めたい。

---

## いまの違和感（事実）

```text
dogido_server/
  app.py, service.py, config.py, models.py   … 入口・ハブ
  entry_catalog.py (1270)                    … 巨大・何でも屋
  player_chat_policy.py                      … ルート直下の新顔
  dialogue_context.py, catalog_readings.py
  memory.py, memory_types.py
  audio.py, voice_input.py, cues.py
  player_input/                              … 意図ルーティング（整理済み寄り）
  llm/                                       … プロンプト分割は比較的きれい
  state_machine/
    mixins/haiku.py (848), narration.py (1148), common.py (880) …
    haiku_context.py, haiku_catalog.py
```

| 問題 | 例 |
|---|---|
| **ルートがゴミ捨て場** | chat 方針・カタログ・読み・対話が同階層 |
| **川柳が4か所** | mixin / context / catalog / `llm/haiku*` |
| **player_chat が3か所** | policy / narration の巨大メソッド / `llm/player_chat_prompts` |
| **発句後 workshop の置き場が無い** | 足すとまたルート or mixin に増える |
| **docs も計画だらけ** | 実装と1:1の地図が弱い |

良い先例: **`llm/`**（facade + 分割）、**`player_input/`**（意図だけまとまる）。

---

## 分け方の原則

1. **ドメイン単位**（川柳 / 対話 / カタログ / 記憶 / 音声 / 実行時）  
2. **「いつ動くか」より「何の知識か」**  
   - 発句タイミング → state_machine  
   - 句の直し対話 → haiku ドメイン  
   - 種名白リスト → dialogue か haiku ではなく **catalog + dialogue ポリシー**  
3. **巨大 mixin に新機能を足さない**（薄いフックだけ）  
4. **移動は段階的**。旧パスに re-export を残して import を壊さない  
5. **docs** は `docs/plans/` と `docs/specs/` 程度に分ける（任意・後で可）

---

## 提案: 目標ツリー

```text
dogido_server/
  app.py                      # FastAPI 入口（薄く）
  __main__.py
  config.py
  models.py                   # イベント schema（共有）
  minecraft_ids.py            # 極小ユーティリティ

  runtime/                    # セッション・オーケストレーション
    service.py                # 旧 service（段階移動）
    replay.py
    smoke_test.py
    py_tree_policy.py         # または policy/

  state_machine/              # 「いつ・どの反応を出すか」（維持・スリム化）
    machine.py
    types.py
    mixins/                   # 既存。新機能の本拠にしない
    # haiku 発句フックだけ残す

  catalog/                    # ゲーム知識の正本アクセス
    __init__.py               # 旧 entry_catalog の公開 API re-export
    entries.py                # load / all_mob / structure（旧 entry_catalog 分割）
    topics.py                 # find_catalog_topics
    plausibility.py           # structure×biome
    readings.py               # 旧 catalog_readings
    ambient_voice.py          # entity_voice 等

  dialogue/                   # プレイヤーとの話し方（非川柳）
    __init__.py
    context.py                # 旧 dialogue_context（5往復）
    player_input/             # 現行移動
    chat_policy.py            # 旧 player_chat_policy（stance 等）
    chat_observation.py       # observation_summary 組み立て（narration から切り出し任意）

  haiku/                      # 川柳ドメイン一式
    __init__.py
    context.py                # 旧 haiku_context
    emit.py                   # 発句フロー（mixin から段階移植 or 呼び出し先）
    catalog_helpers.py        # 旧 haiku_catalog
    workshop.py               # pin / open-close / 意図 ★新規はここ
    feedback.py               # 読み・直し・critique のサービス層（任意）

  memory/                     # 長期・短期 I/O
    __init__.py
    store.py                  # 旧 memory.py
    types.py                  # 旧 memory_types

  llm/                        # 現状維持でよい（既に整理済み）
    prompts.py                # facade
    player_chat_prompts.py
    haiku_prompts.py
    reaction_prompts.py
    ...

  audio/                      # 任意パッケージ化
    playback.py               # 旧 audio.py
    voice_input.py
    cues.py
```

**ポイント:**  
- **`haiku/workshop.py`** … pin はここ。`haiku.py` mixin に混ぜない  
- **`dialogue/chat_policy.py`** … 雑談3本柱は dialogue  
- **`catalog/`** … entry_catalog 肥大の受け皿  
- **`state_machine`** … スケジューラ／反応のオーケストレーションに戻す  

---

## 依存関係（理想）

```text
app → runtime.service
        ├→ state_machine
        ├→ dialogue (input + chat_policy + context)
        ├→ haiku (emit hook + workshop)
        ├→ catalog
        ├→ memory
        ├→ llm
        └→ audio

state_machine → catalog, (llm via 薄い generate)
haiku → catalog, memory, llm
dialogue → catalog, memory(optional)
workshop → memory, haiku context snapshot
```

循環を避ける:

- `catalog` は他ドメインに依存しない  
- `workshop` が `state_machine.mixins` を import しない（型とデータだけ受け取る）  

---

## 移行フェーズ（破壊を抑える）

### Phase 0 — 原則だけ決める（今）

- 新規は **ドメインディレクトリに置く**  
- `haiku_workshop` → 実装時は **`haiku/workshop.py`**（または暫定 `haiku_workshop.py` でも、最終は haiku/ 配下）  

### Phase 1 — 新規を正しい場所に（低リスク）

| 新規 | 置き場 |
|---|---|
| workshop / pin | `haiku/workshop.py` |
| critique 保存 API | `memory/` にメソッド、または `haiku/feedback.py` |

ルートに `haiku_workshop.py` を増やさない。

### Phase 2 — 既存の「迷子」を寄せる（re-export）

```text
player_chat_policy.py  → dialogue/chat_policy.py
  旧ファイル: from dogido_server.dialogue.chat_policy import *  # 互換

dialogue_context.py    → dialogue/context.py
catalog_readings.py    → catalog/readings.py
```

import 一括置換は rg で段階的。テストが通る単位で PR 分割。

### Phase 3 — entry_catalog 分割

```text
entry_catalog.py
  → catalog/entries.py   (load, mob, structure, biome)
  → catalog/topics.py    (find_catalog_topics)
  → catalog/plausibility.py
旧 entry_catalog.py は re-export のみの facade に
```

### Phase 4 — narration / haiku mixin のダイエット（任意・時間かかる）

- `narration._render_player_chat_reply` → `dialogue/chat_render.py` へ  
- `mixins/haiku.py` の「材料集め」は context 側、「タイミング」だけ mixin に残す  

一気にやらない。ファイルが 400 行超えたら切る、くらいでよい。

### Phase 5 — docs（任意）

```text
docs/
  specs/          # event-schema, architecture
  plans/          # *-plan.md, roadmap
  guides/         # debug-checklist
```

または現状のまま、**plans だけ `docs/plans/`** に寄せる。

---

## やらないこと

- 全ファイルを1 PR で mv（レビュー不能・import 地獄）  
- state_machine を廃止して全部 service に戻す  
- llm/ をまたルートにバラす  
- 名前に「new」「utils2」を増やす  

---

## 命名の目安

| ドメイン | パッケージ |
|---|---|
| 川柳 | `haiku` |
| 雑談・入力意図 | `dialogue` |
| ゲーム知識 | `catalog` |
| 永続化 | `memory` |
| 実行ハブ | `runtime`（急がなくてよい） |
| LLM | `llm`（現状維持） |

---

## 直近の実務おすすめ

1. **今すぐ:** workshop は `dogido_server/haiku/workshop.py` で始める（空の `haiku/__init__.py` + 既存 haiku_context は後で移動）  
2. **次の整理 PR:** `player_chat_policy` → `dialogue/chat_policy`（re-export）  
3. **その次:** `entry_catalog` から topics/plausibility 切り出し  
4. **mixin 分割は急がない**（動いているものを壊しやすい）  

---

## 成功条件

1. 「川柳の直し」を探すと `haiku/` に辿り着く  
2. 「雑談 stance」は `dialogue/`  
3. ルート直下にポリシーファイルが増えない  
4. 旧 import がしばらく動く（re-export）  
5. 新規機能が 800 行 mixin に生えない  

---

## 次の合意

- このツリー方向でよいか  
- Phase 1 だけ先に進めるか（workshop 実装時）  
- Phase 2 を独立の「整理 PR」にするか  

**実行チェックリスト（編集順・workshop 込み）:**  
[server-reorg-and-workshop-order.md](server-reorg-and-workshop-order.md)
