# AGENTS.md — AI コーディングエージェント向け

このリポジトリで作業する AI 向けの導入メモ。  
人間向けのコンセプトは [README.md](README.md)、完成度の方針は [docs/companion-maturity.md](docs/companion-maturity.md)。

---

## 1. これは何か（30 秒）

Minecraft の状況イベントを受け取り、怖がり相棒 **ドギド** が警告・雑談・川柳を返す **リアルタイム相棒サーバー**。

```text
adapter/minecraft-fabric  →  dogido_server (FastAPI + 状態機械 + LLM leaf)  →  TTS / テキスト
```

- **判断の主**はコード（状態機械 / py_trees / policy）
- **LLM**は雑談・川柳など「言い回し」生成に限定
- **記憶**は JSONL（few-shot 山盛りや Hermes 系汎用エージェントは使わない）

汎用チャットボットや「なんでもできるエージェント」に改造しない。

---

## 2. 触る場所の地図

| パス | 役割 |
|---|---|
| `dogido_server/service.py` | セッション、player 入力、workshop / memory 配線 |
| `dogido_server/state_machine/` | 本体判断。mixin 分割済み。**巨大ロジックを haiku mixin に足し続けない** |
| `dogido_server/haiku/workshop.py` | 句 pin（open/close）、意図分類、soft 返事、lesson 生成 |
| `dogido_server/dialogue/chat_policy.py` | 雑談トピック stance（none を守る等）。`player_chat_policy.py` は re-export |
| `dogido_server/llm/` | prompts / client / haiku 音数・usable / route |
| `dogido_server/memory.py` | JSONL 長期記憶（entries / revisions / critiques / lessons） |
| `dogido_server/player_input/` | 正規化・`直し:`・ガード |
| `adapter/minecraft-fabric/` | ゲーム → イベント送信 |
| `docs/` | 方針の正。実装とズレたら **docs を直すか実装を直すか**を明示 |
| `tests/` | 変更時は関連 `test_haiku*` / `test_player_chat*` 等を回す |

パッケージ移動時は **新場所に置いて → 旧は re-export → import 置換**。一発削除しない。

---

## 3. 設計の不変条件（破ると方針と衝突）

### 3.1 キャラクター判断はコード

- panic / 警告の優先、発話抑制、いつ川柳かは **状態機械側**
- LLM に「今パニックすべきか」を委ねない
- leaf 失敗時はカタログ fallback がある前提を壊さない

### 3.2 川柳 lesson は soft

- player lessons は **参考行**（「強制ではない」）
- **道具・読みの allowed/forbidden だけ hard**（例: シャベルなのにつるはし禁止）
- lesson の `forbidden_fragments` を hard 禁止に合流しない
- praise / 「気にせんで」→ `polarity: loosen`（全軸抑止可）
- TTL: 日数 + 発句回数で自然減衰（`memory.list_recent_haiku_lessons`）
- **strength 段階は当面使わない**（フィールドはあるが list 未参照）

### 3.3 H6 固定語 materials 突合は撤回済み

- 「うみ」等の **drift 単語リストで句を reject しない**
- 場外れはプレイヤー workshop 講評 or 生成品質で見る
- 湖の隣で「うみ」が自然なこともある。材料＝プレイヤー視界ではない

### 3.4 雑談は overfit しない

- 弱い topic で偽 identify しない（**none を守る**）
- 詳細: [docs/player-chat-casual-plan.md](docs/player-chat-casual-plan.md)

### 3.5 記憶の載せ方

- 発句は基本 auto-save（entries）
- revision / critique / lesson は JSONL
- **プロンプトに過去 revision を常時 few-shot しない**
- 想起は明示クエリ時（「句思い出して」等）

### 3.6 完成度の本丸（機能追加の前に）

1. 観測 materials をプレイヤー視界に近づける  
2. 外したあとも関係を壊さない（workshop / soft）  
3. 飛び道具（VLM 常時 / Vector RAG / H7 LLM 分類）は後回し  

→ [docs/companion-maturity.md](docs/companion-maturity.md)

---

## 4. よく触るドメイン詳細

### 川柳 workshop（H1–H5.2）

- pin: `SessionInfo.haiku_workshop`（会話 5 往復とは別）
- open: 発句後 / close: drift・timeout・praise・revise・明示 close・次の句
- 意図: `classify_workshop_intent` / 自然文直し: `extract_conversational_revise`
- 明示緩め: `wants_clear_haiku_lessons`（workshop 外でも可）
- ロジックの本体は `haiku/workshop.py`。`mixins/haiku.py` は発句と制約注入フックまで

### 発句制約

- `_haiku_constraint_details`: 道具・読み hard + `player_lessons` soft（空ならキー省略）
- `haiku_lessons_provider` は service が memory に bind

### LLM routes

- `chat` … 雑談・助言  
- `haiku` … 句（irony/scene 経由のことも）  
- 低レイテンシ戦況は LLM なし  
- route ごとに provider を分けられる（`.env` / Settings）

---

## 5. やってはいけないこと

| NG | 理由 |
|---|---|
| mixin 巨大ファイルに workshop / lesson をベタ書き | パッケージ方針に反する |
| soft lesson を hard 禁止に昇格 | H5.1 方針破壊 |
| 材料固定語リスト（旧 H6）の復活 | 撤回済み・誤検知とメンテ地獄 |
| プロンプト肥大（履歴・revision 山盛り） | 設計上やらない |
| 無関係なリファクタ・docs 大量生成を PR に混ぜる | 差分が追えなくなる |
| ユーザー依頼以外のコミット / push / 破壊的 git | 明示依頼があるまでしない |
| Hermes 等の汎用エージェント基盤導入 | プロジェクト方針で不要 |
| VLM を必須経路にする | 将来・イベント駆動のみ想定 |

---

## 6. 変更時の作法

1. **既存方針 docs を先に確認**（haiku-player-improvement / companion-maturity / casual-plan）  
2. 小さな単位で直す。テストを通す  
   - 例: `python -m pytest tests/test_haiku*.py tests/test_player_chat*.py -q`  
3. 挙動を変えたら **docs の状態表記も合わせる**（「実装前」のままにしない）  
4. 絶対パス（特定マシンのホーム）を README や docs に書かない  
5. 秘密情報・`.env` の実キーをコミットしない  

### テストの心構え

- 川柳・workshop・雑談 policy はユニットで守られている  
- LLM 実呼び出しに依存するテストを増やしすぎない  
- 失敗が「chat の usable」など別領域なら、無関係に「直したことにしない」

---

## 7. 起動・確認（最短）

```bash
pip install -e .
cp .env.example .env   # 必要なら LLM / TTS を設定
python -m dogido_server
python -m pytest tests/test_haiku*.py -q
```

player 入力:

```bash
curl -X POST http://127.0.0.1:5055/api/v1/player-input \
  -H 'Content-Type: application/json' \
  -d '{"text": "おはようさん"}'
```

記憶ディレクトリは設定の `memory_dir`（多くの場合 `.dogido_memory` 系）。JSONL を手で壊すと lesson/entry がおかしくなる。

---

## 8. ドキュメント優先度（迷ったら）

**目次・読む順番の正:** [docs/README.md](docs/README.md)

| 優先 | ドキュメント |
|---|---|
| コンセプト | `docs/concept.md` · README |
| 完成度・何を足すか | `docs/companion-maturity.md` |
| 川柳 workshop | `docs/haiku-player-improvement-plan.md` |
| パッケージ編集順 | `docs/server-reorg-and-workshop-order.md` |
| 雑談 | `docs/player-chat-casual-plan.md` |
| イベント形 | `docs/event-schema.md` · `docs/adapter-api.md` |
| 状態機械 | `docs/state-machine.md` |

計画書に「済 / 撤回」と書いてある項目を、古い記述のまま再実装しないこと。

---

## 9. 現在の実装スナップショット（目安）

- workshop H1〜H5.2: **済**（soft lesson / loosen / TTL / 明示「気にせんで」）  
- H6 materials 固定語: **撤回**  
- 雑談 P1〜P4: **済**（P5 任意）  
- 完成度の次の本丸: **観測 materials の解像度**（水辺・旗・地下など）  
- 任意: 直し案 1 本、H7、Phase E 整理、VLM  

更新したらこの節と `companion-maturity.md` §6 を揃える。
