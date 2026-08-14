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
- **LLM / OS AI**は言い回し生成と、閉じた型の限定抽出（workshop intent / findings / 一行置換 / pending採否）まで。状態変更・保存判断はコード
- **記憶**は JSONL（few-shot 山盛りや Hermes 系汎用エージェントは使わない）

汎用チャットボットや「なんでもできるエージェント」に改造しない。

---

## 2. 触る場所の地図

| パス | 役割 |
|---|---|
| `dogido_server/service.py` | セッション、player 入力、workshop / memory 配線 |
| `dogido_server/state_machine/` | 本体判断。mixin 分割済み。**巨大ロジックを haiku mixin に足し続けない** |
| `dogido_server/state_machine/precipitation.py` | 現在Y・気温・天気・雪ブロック実測から雨／降雪／積雪根拠を確定。LLMには数値を伏せた閉じた気象事実だけを共有 |
| `dogido_server/haiku/workshop.py` | 句 pin（open/close）、意図分類、soft 返事、lesson 生成 |
| `dogido_server/haiku/edit_contract.py` | workshop 行差分の compare-and-swap 検証（生成・採用・保存で共有） |
| `dogido_server/dialogue/chat_policy.py` | 雑談トピック stance（none を守る等）。`player_chat_policy.py` は re-export |
| `dogido_server/llm/` | prompts / client / haiku 音数・usable / route |
| `dogido_server/llm/character_mode.py` | 冒険の怖がり役と workshop の共同編集者役 |
| `dogido_server/platform_ai.py` | Apple Foundation Models / Foundry Local / chat fallback の限定 structured router |
| `dogido_server/player_activity.py` | 乗車中だけ存在する vehicle 状態を、主語付きの雑談・川柳材料へ変換 |
| `dogido_server/memory.py` | JSONL 長期記憶（entries / revisions / critiques / lessons） |
| `dogido_server/player_input/` | 正規化・`直し:`・ガード・現在語彙だけのSTT音近傍補正 |
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
- OS AI 出力から直接 close / lesson解除 / revision保存しない。enum・行番号・発話中evidence・confidence・現在pending・CASをコード検証する
- STT文脈補正は `source=voice` と現在候補だけ。`raw/normalized` は保持して明示操作の正、`interpreted/semantic` は会話理解と限定意味抽出に使う。意味抽出から保存するときも原文・evidence・CASを検証する
- platform provider は設定と可用性だけで選ぶ。Foundry のモデル自動 download は既定 off を守る
- 乗り物は乗車中だけ `player.vehicle` を送る。LLM には必ず「プレイヤーはXXに乗って…」の主語付き事実として渡す

### 3.2 川柳 lesson は soft

- player lessons は **参考行**（「強制ではない」）
- **道具・読みの allowed/forbidden だけ hard**（例: シャベルなのにつるはし禁止）
- lesson の `forbidden_fragments` を hard 禁止に合流しない
- **praise（いい句）→ lesson は触らない**（過去の指摘をキープ。critique 保存のみ）
- **「気にせんで」→ `polarity: loosen` + `lesson_type: "*"`**（全軸抑止。明示リセットのみ）
- TTL: 日数 + 発句回数で自然減衰（`memory.list_recent_haiku_lessons`）
- **strength 段階は当面使わない**（フィールドはあるが list 未参照）

### 3.3 H6 固定語 materials 突合は撤回済み

- 「うみ」等の **drift 単語リストで句を reject しない**
- 場外れはプレイヤー workshop 講評 or 生成品質で見る
- 湖の隣で「うみ」が自然なこともある。材料＝プレイヤー視界ではない

### 3.4 雑談は overfit しない

- 弱い topic で偽 identify しない（**none を守る**）
- 詳細: [docs/player-chat-casual-plan.md](docs/player-chat-casual-plan.md)

### 3.4b ambient / モブ反応トーンは公式に合わせる

- 友好・資源モブに「触るな」系の操作禁止を言わない
- 中立は「触るな」より **優しく・怒らせない**（Be nice to animals）
- 正: [docs/mob-interaction-tone.md](docs/mob-interaction-tone.md)

### 3.5 記憶の載せ方

- 発句は基本 auto-save（entries）
- revision / critique / lesson は JSONL
- **プロンプトに過去 revision を常時 few-shot しない**
- 想起は明示クエリ時（「句思い出して」等）

### 3.6 完成度の本丸（機能追加の前に）

1. 観測 materials をプレイヤー視界に近づける  
2. 外したあとも関係を壊さない（workshop / soft）  
3. 飛び道具（VLM 常時 / Vector RAG / workshop 全域の LLM 制御）は後回し

→ [docs/companion-maturity.md](docs/companion-maturity.md)

---

## 4. よく触るドメイン詳細

### 川柳 workshop（H1–H5.2 + H7-lite / 修正案1本 / 連続局所編集）

- pin: `SessionInfo.haiku_workshop`（会話 5 往復とは別）
- open: 発句後 / close: drift・timeout・praise・完成三行のformal/conversational revise・明示 close・次の句。pending案の明示採用は現在句へ昇格してopen維持
- 意図: close / clear_lessons / 明示praise / 完成三行revision / 明示reading はコードが正。それ以外の自然文はOS AI優先の閉じたschemaでintent・対象行・断片・problemを抽出し、コードが永続化と実行条件を決める
- `request_repair`: OS AIが高信頼に修正要求を抽出し、コードが検証済みfindingを確定できたときだけ、大きいhaiku routeが `expected_text` / `replacement_text` つき差分で修正。コードが元行一致・対象外不変を確認し、別structured評価で意味保持・自然さを照合、出典ID・重複・音数・発句時hard制約を検証。不合格理由と案を次の試行へ返し、同一案は評価前に棄却する。案は採用まで保存せず、採用時にも同じ元句へ適用できるか再確認する。提示文は句本文・採用案内をコード固定し、前置き一言だけ共同編集者leaf
- プレイヤー局所編集: 自然な提案はOS AIが発話中の置換語・evidenceと句中target fragmentを先に抽出し、従来の閉じた文字列解析は利用不可・低信頼時のfallbackに限る。finding／明示行／一意なfragmentに加え、「旧句より新句」の発話中にある現在句の一行でも対象を固定する。句フレーズ指定はSTTが漢字化しても読みへ戻し、現在の三行へ一意に一致するときだけ採用する。コードでひらがな化・正確な5/7/5音・hard制約・重複を検査して未保存三行へ連続CASする。AIが発話にない語を補作したら捨てる。本文・現在句照会はLLMに生成させない
- pending採否: 専用OS AI schemaで accept / reject / modify / show / discuss 等を意味抽出。confidence・evidence・現在pending・CASをコード検証し、合格時だけ保存／破棄する。利用不可時は代表的な完全一致規則へfallback。採用後は句を次の基準へ昇格しpinを維持
- 自然文直し: `extract_conversational_revise`
- 明示緩め: `wants_clear_haiku_lessons`（workshop 外でも可）
- ロジックの本体は `haiku/workshop.py`。`mixins/haiku.py` は発句と制約注入フックまで

### 発句制約

- `_haiku_constraint_details`: 道具・読み hard + `player_lessons` soft（空ならキー省略）
- `haiku_lessons_provider` は service が memory に bind
- scene は見どころ発話の補助。`found=false` / 契約不合格でも一次 source atom が足りれば共通生成器へ進み、固定川柳カタログは LLM 利用不可時だけ使う
- 漢字混じり候補は既存UniDicが使える場合だけコードでかな化し、同じ行を共通検査へ戻す。未知語や辞書無しで読みを推測しない

### LLM routes

- `chat` … 雑談・助言  
- `haiku` … 句（irony/scene 経由のことも）  
- 低レイテンシ戦況は LLM なし  
- route ごとに provider を分けられる（`.env` / Settings）
- platform structured は `auto=Apple Foundation Models → Foundry Local → chat`。任意依存で、失敗しても workshop を壊さない

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
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"   # ランタイム + pytest。サーバーだけなら pip install -e .
# 任意: TTS 読み補正（UniDic）… pip install -e ".[tts-reading]"
cp .env.example .env   # 必要なら LLM / TTS を設定（DOGIDO_TTS_READING_ENGINE 等）
python -m dogido_server
python -m pytest tests/test_haiku*.py tests/test_tts_reading.py -q
```

player テキスト注入（開発用・**アクティブセッション必須**）は
[docs/adapter-api.md §21](docs/adapter-api.md#21-post-apiv1player-input) を参照。

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
| ボイス速度・間・コール断片 | `docs/voice-delivery-plan.md`（#13、うしろ named §11） |
| TTS 読み・UniDic | `docs/tts-reading-unidic-plan.md`（Phase 1–2 済・optional `[tts-reading]`） |
| 複数ユーザー（交代） | `docs/multi-user-tenancy.md`（#20） |
| イベント形 | `docs/event-schema.md` · `docs/adapter-api.md` |
| 状態機械 | `docs/state-machine.md` |

計画書に「済 / 撤回」と書いてある項目を、古い記述のまま再実装しないこと。

---

## 9. 現在の実装スナップショット（目安）

- workshop H1〜H5.2 + H7-lite + 修正案1本 + 連続局所編集: **済**（soft lesson / loosen / TTL / 明示「気にせんで」/ OS AI優先の限定 intent・findings・一行置換・pending採否抽出 / AIのLocate→Edit→Test / プレイヤー語のひらがなCAS / 採用後も継続）
- H1.1 materials 厚み（motifs/held/nearby + short candidates + fragment_links）: **済**（#28 phase 0–1）  
- H6 materials 固定語: **撤回**  
- 雑談 P1〜P4: **済**（P5 任意）  
- TTS 読み: 例外表 + optional UniDic（`[tts-reading]`）**Phase 1–2 済**  
- 川柳 preface: **見どころ→ここで一句→句** + 自分の世界（pending 中 chat 抑止）**済**  
- 川柳 source atom 品質ゲート: カタログ原文snapshot + 節単位preface provenance/主張範囲の別評価 + 行別出典 + 出典確定後の一意なカタログ名かな訂正（全文必須ではない）+ UniDicによる漢字候補の事前かな化 + 4生成方式の固定比較 + 行別失敗理由つき最大6回再生成 + 既出候補即時棄却 + fail-closed **済**
- 川柳実測中の発句間隔は一時3分。**比較・開発終了時は設定既定・`.env.example`・docsを10分（600000ms）へ必ず戻す**
- 降雪・積雪材料: 現在Y×バイオーム気温/降雪高度をコード判定。Y/Z・気温・閾値・downfallはLLMへ出さず、閉じた降水/雷/降雪環境と実測地表雪だけを共有 **済**
- 乗り物材料: 乗車中のみ種別・操縦・実移動を観測し、主語付き事実として川柳・雑談で共有 **済**（エリトラは別課題）
- ambient: プレイヤー入力優先（priority mute 共通 + pending キュー中禁止）**済**  
- 完成度の次の本丸: **観測 materials の解像度**（水辺・旗・地下など）  
- 任意: OS AI・chat fallback・修正案の実ログ評価、Phase E 整理、VLM、TTS 読み Phase 3 実測、5-7-5 分割読み

更新したらこの節と `companion-maturity.md` §6 を揃える。
