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
- **LLM**は言い回し生成と、閉じた型の限定抽出（workshop intent / findings / 句評価 / 一行置換 / pending採否 / 終了意図）まで。**OS AI**は戦闘中断中の小さな5分類だけに限る。状態変更・保存判断はコード
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
| `dogido_server/haiku/workshop_context.py` | 一句の直近対話・見どころ・材料・照合先・修正結果の読み取り用共有文脈。採用・保存の権限は持たない |
| `dogido_server/haiku/combat_pause.py` | 戦闘中の句保持pause、勝利／離脱後の再開、安定した単独敵の暫定継続 |
| `dogido_server/haiku/edit_contract.py` | workshop 行差分の compare-and-swap 検証（生成・採用・保存で共有） |
| `dogido_server/haiku/verse.py` | 一行の表示・確定ひらがな読み・行概念・出典を同じ正本オブジェクトへ束ねる |
| `dogido_server/dialogue/chat_policy.py` | 雑談トピック stance（none を守る等）。`player_chat_policy.py` は re-export |
| `dogido_server/llm/` | prompts / client / haiku 音数・usable / route |
| `dogido_server/llm/structured_contracts.py` | workshop・assist等の現行JSON外形。自動川柳6種は8月完成版のドメイン検査と最大6回再生成を維持 |
| `dogido_server/llm/character_mode.py` | 冒険の怖がり役と workshop の共同編集者役 |
| `dogido_server/platform_ai.py` | Apple Foundation Models / Foundry Local / chat fallback の限定 structured router |
| `dogido_server/player_activity.py` | 乗車中だけ存在する vehicle 状態を、主語付きの雑談・川柳材料へ変換 |
| `dogido_server/memory.py` | JSONL 長期記憶（entries / revisions / critiques / lessons） |
| `dogido_server/episode_log.py` | 非重複イベント1件につき1行の評価用決定記録。`.dogido_memory/eval/episodes.jsonl`（会話・川柳記憶とは別） |
| `dogido_server/assist/` | 型付きの限定世界操作。明示registry・AUTO/CONFIRM/DENY gate・現在snapshotのavailable・`select_sword`。LLM toolsではない |
| `dogido_server/player_input/` | 正規化・`直し:`・ガード・現在語彙だけのSTT音近傍補正 |
| `adapter/minecraft-fabric/` | ゲーム観測 → イベント送信 + 許可済みtyped commandのクライアント実行 |
| `docs/` | 方針の正。実装とズレたら **docs を直すか実装を直すか**を明示 |
| `tests/` | 変更時は関連 `test_haiku*` / `test_player_chat*` / `test_assist*` 等を回す |

パッケージ移動時は **新場所に置いて → 旧は re-export → import 置換**。一発削除しない。

---

## 3. 設計の不変条件（破ると方針と衝突）

### 3.1 キャラクター判断はコード

- panic / 警告の優先、発話抑制、いつ川柳かは **状態機械側**
- LLM に「今パニックすべきか」を委ねない
- leaf 失敗時はカタログ fallback がある前提を壊さない
- AI 出力から直接 close / lesson解除 / revision保存しない。評価極性・評価範囲・終了scope・enum・行概念ID・行番号・発話中evidence・confidence・現在pending・CASをコード検証する
- workshop・assist等の structured 出力は kind ごとに `structured_contracts.py` の現行外形を通す。自動川柳の `haiku_draft` / `haiku_irony` / `haiku_scene` / `haiku_line_grounding` / `haiku_line_regeneration` は、2026-08-16完成版のドメイン検査を正とし、欠けた行の個別再照合と最大6回の内容再生成を途中の共通schemaで打ち切らない。新kind追加時は呼出箇所・prompt例・fallback・consumer・テストを同時に揃える
- STT文脈補正は `source=voice` と現在候補だけ。`raw/normalized` は保持して明示操作の正、`interpreted/semantic` は会話理解と限定意味抽出に使う。意味抽出から保存するときも原文・evidence・CASを検証する。剣支援の実測誤変換 `県に持ち替え/変えて/ハインコ（変更）/チェンに変更` は voice-only・操作語直結の閉じた規則で解釈面だけ補正し、typed・単独の候補語・その語の会話は対象外
- platform provider は設定と可用性だけで選ぶ。Foundry のモデル自動 download は既定 off を守る
- 乗り物は乗車中だけ `player.vehicle` を送る。LLM には必ず「プレイヤーはXXに乗って…」の主語付き事実として渡す
- 世界操作はLLMへtools一覧として渡さない。代表命令はコード、自然形は閉じたintent/evidence/confidence抽出まで。実行capability・現在snapshot・slot・期限・期待item・重複はコード検証する
- `select_sword` は明示依頼だけ。非戦闘中の明示依頼は可だが自動持ち替えは禁止。通常Qwenの限定抽出を使い、OS AIの用途を広げない

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

### 3.5b エピソード決定記録は記憶ではない

- `episode_log.py` は `trigger → observation → state_before → decision → action → result` を schema version 付きで追記する
- 発話なしも含む非重複イベント1件を1行とし、重複受信は新しい判断として記録しない
- `eval/episodes.jsonl` を `MemoryStore`、会話文脈、川柳生成へ読み戻さない
- `result.scope=service_decision` はserviceが選んだ結果であり、TTS／スピーカーの実再生成功ではない。adapter結果を受けた行だけ `adapter_execution_observed` とcommand IDで記録する
- serialize・ディレクトリ作成・追記失敗をリアルタイム処理へ伝播させない

### 3.6 完成度の本丸（機能追加の前に）

1. 観測 materials をプレイヤー視界に近づける  
2. 外したあとも関係を壊さない（workshop / soft）  
3. 飛び道具（VLM 常時 / Vector RAG / workshop 全域の LLM 制御）は後回し

→ [docs/companion-maturity.md](docs/companion-maturity.md)

---

## 4. よく触るドメイン詳細

### 川柳 workshop（H1–H5.2 + H7-lite / 修正案1本 / 連続局所編集 / 戦闘中断）

- pin: `SessionInfo.haiku_workshop`（会話 5 往復とは別）
- open: 発句後 / close: drift・timeout・praise・完成三行のformal/conversational revise・自然な終了意図・次の句。pending案の明示採用は現在句へ昇格してopen維持
- 意図: clear_lessons / 固定規則に一致する明示praise / 完成三行revision / 明示reading と代表的なclose fallbackはコードが正。それ以外の自然文は常駐する会話モデルの閉じたschemaでintent・対象行・断片・problem・句評価・終了scopeを抽出し、コードが永続化と実行条件を決める。句全体へのpositive評価は即closeせずコード固定の終了確認、negative／mixedは共同編集者へ改善方向を一問だけ尋ねさせる。終了はscopeがworkshop全体または次の句、発話中evidenceあり、confidence 0.85以上の場合だけコードが実行する
- 意味説明後の納得: `ask_meaning` 返答後だけ会話段階を保持し、「そうなんだ」等の意味的ackを会話モデルで抽出する。ackターンはfinding・critique・lessonへ流さずコード固定の終了確認へ進み、次の肯定でclose、続行意思ならopenへ戻す
- `request_repair`: 会話モデルが高信頼に修正要求を抽出し、コードが検証済みfindingを確定できたときだけ、大きいhaiku routeが `expected_text` / `replacement_text` つき差分で修正。コードが元行一致・対象外不変を確認し、別structured評価で意味保持・自然さを照合、出典ID・重複・音数・発句時hard制約を検証。不合格理由と案を次の試行へ返し、同一案は評価前に棄却する。案は採用まで保存せず、採用時にも同じ元句へ適用できるか再確認する。提示文は句本文・採用案内をコード固定し、前置き一言だけ共同編集者leaf
- プレイヤー局所編集: 三行は安定ID `line_1/2/3`、概念番号1/2/3、配列index 0/1/2、位置upper/middle/lower、正規名上五/中七/下五を持つ。各行は表示表記・確定ひらがな読み・出典・provenanceも同じオブジェクトに持ち、表記と読みを別の句にしない。自然な提案では会話モデルが「上の句」「二の句」「真ん中」「後ろのパート」等をこの概念へ対応させ、発話中の行呼称evidence・置換語・句中target fragmentを抽出する。コードが既知呼称・finding・fragmentとの衝突と一意性を検証し、従来の閉じた文字列解析は利用不可・低信頼時のfallbackに限る。finding／明示行／検証済み行概念／一意なfragmentに加え、「旧句より新句」の発話中にある現在句の一行でも対象を固定する。句フレーズ指定はSTTが漢字化しても読みへ戻し、現在の三行へ一意に一致するときだけ採用する。コードでひらがな化・正確な5/7/5音・hard制約・重複を検査し、対象行の表示と読みを同時に置換して未保存三行へ連続CASする。AIが発話にない語を補作したら捨てる。本文・現在句照会はLLMに生成させない。意味質問は保存済み出典を手がかりに、句・当時の材料・見どころ・直近対話を会話モデルが比較して説明する。対応や前の説明の取り違えは認めるが、句・出典記録を自動で書き換えず、不明な意味や由来を作らない。現在はプレイヤーの呼称を訂正せず、生の呼称・正規名・概念IDを将来learning版のフックとしてログへ残す
- pending採否: 常駐する会話モデルの専用schemaで accept / reject / modify / show / discuss 等と終了意図を別々に意味抽出。confidence・evidence・現在pending・CASをコード検証し、`accept+close` は保存後、`reject+close` は破棄後にcloseする。採否なしのclose要求はコード固定文で確認する。利用不可時は代表的な完全一致規則へfallback。closeを伴わない採用後は句を次の基準へ昇格しpinを維持
- 戦闘中断: visual／auditory脅威・直近被弾で句とpendingを保持したままpauseし、workshop用ASR補正・timeout・driftを止める。通常敵はadapterの論理サーバー死亡イベント／クライアント死亡状態／実爆発パケットを使い、プレイヤー撃破／爆発死／その他死亡／クリーパー爆散／離脱で復帰文を分ける。死亡音・経験値・攻撃履歴・観測範囲からの消失だけではプレイヤー撃破扱いにしない。死亡は `hostile_defeated`、爆散は `creeper_detonated` で即時に一度だけ反応し、後の `combat_ended` は安全確認の安堵へ分ける。導火開始と通常／帯電クリーパー爆散は慌て方を変え、一度話した個体IDは後の敵離脱へ持ち越さない。戦闘音声後の静かなnormalフレームで三行を再掲して継続確認する。中断中発話はOS／端末内AI優先（失敗時はchat fallback）で `resume_workshop / workshop_input / close_workshop / unrelated / uncertain` だけを根拠つき抽出し、全AIが利用不可・低信頼・不正出力のときだけ閉じた規則へfallbackする。安全判定と実際の再開／closeはコード。単独敵が8秒以上非接近・無被弾でも、再開意思を確定できたときだけ暫定再開。再接近・被弾・敵数／個体変化で即pause
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
- platform structured は戦闘中断中の小分類だけに使い、`auto=Apple Foundation Models → Foundry Local → chat`。任意依存で、失敗しても workshop を壊さない

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
python -m pytest tests/test_haiku*.py tests/test_tts_reading.py tests/test_episode_log.py -q
```

player テキスト注入（開発用・**アクティブセッション必須**）は
[docs/adapter-api.md §21](docs/adapter-api.md#21-post-apiv1player-input) を参照。

永続化ルートは設定の `memory_dir`（多くの場合 `.dogido_memory` 系）。`long_term/` 等は記憶の正本、`eval/episodes.jsonl` は読み戻さない評価ログ。JSONL を手で壊すと履歴・集計がおかしくなる。

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

- 2026-09-06: `language_dialogue/` に国語の対象・観点・曖昧さの抽出、確認後の検索、根拠付き説明、一時文脈と模擬中断を持つ **独立テキスト試験経路** を追加。ユーザー境界例15件＋短い模擬会話6件。関連自動テスト84件と実モデル21会話＋対象3会話の再確認を実施。説明の付け足し・学習対象の分類に課題が残り、**service・workshop・音声には未接続**。詳細は `docs/language-dialogue-text-test.md` と試験結果文書。

- 所持品質問の省略形は、アイテム語に有無・数量の述語が続く場合に限定。「石炭じゃない？」等の同定や意味質問を、単なる「ない」「何」の部分一致で所持品へ流さない。明示の所持品確認は維持。句の意味質問→通常の所持品確認を続けたサービス経路は **自動テスト済み**。実機音声は未確認。

- 2026-09-05: 一句専用4往復＋見どころ・材料・行別照合・修正結果の共通文脈、再試行への不合格案と具体的コメントの受け渡しは **コード・自動テスト済み**。通常会話は既存5往復で訂正・困惑に答え直す方針を追加。生成回数・採用・保存・戦闘優先は維持。意味説明もユーザー承認のうえ既存の共同編集者leafへ移し、保存済み対応と材料・見どころ・対話を比較する。出典記録は自動変更せず、生成失敗時も旧材料名固定文へ戻さない。実モデルの品質・速度、音声は未確認。

- workshop H1〜H5.2 + H7-lite + 修正案1本 + 連続局所編集 + 戦闘中断: **済**（soft lesson / loosen / TTL / 明示「気にせんで」/ 常駐会話モデルの限定 intent・findings・句評価・行呼称→`line_1/2/3`・一行置換・pending採否・自然な終了意図、OS AI優先は戦闘中断中の再開／終了意思抽出のみ / AIのLocate→Edit→Test / プレイヤー語のひらがなCAS / 採用後も継続 / 戦闘中は句とpendingを保持してpause→コード安全確認後に再掲・継続確認。通常敵もプレイヤー撃破／爆発死／その他死亡／クリーパー爆散／離脱を分離し、戦闘結果は一回消費）
- H1.1 materials 厚み（motifs/held/nearby + 全インベントリからの代表候補 + short candidates + fragment_links）: **済**（#28 phase 0–1）。周辺ブロック・落下物・手持ち・所持品を背景より優先し、空が見えない場面では対話・川柳とも時刻／天候と非洞窟バイオームを投影しない。直近の実破壊＋採掘道具を主根拠に「採掘中」、静止環境だけなら「坑道らしい場所」と分ける
- H6 materials 固定語: **撤回**  
- 雑談 P1〜P5 + 現在ターン予定／安全方針: **済**（帰宅予定は現在発話だけ、地表夕方／雷雨は毎フレーム導出、洞窟オフ）
- 出典付き知識質問: 国語・日本／世界詩形・Minecraft 1.21.11公式技術資料を、現在ターンの明示質問だけ遅延検索し、最大3事実＋出典をコード固定で返す経路は **コード・自動テスト済み**。枕詞では正式な定義と短い関西弁の対話本文を分離し、詳説を読み上げず教科書・資料集へ誘導する。手整備DBの正式名・別名と文型800正式名を完全一致で同期し、差替えproviderの事実一式は正本DBと照合。alert・panic・戦闘・死亡・assist・保存・workshop状態変更から分離し、高優先発話時は有界の待ち列へ保留する。2026-09-02にMinecraft接続下で枕詞回答の4文配送計画、ゲーム外画面、参考資料分離、本文コピーを確認。ほかの項目の対話本文・再生完了通知・通常wheel配置は未対応
- ゲーム外診断ログ: `/dogido` で発言・参考資料に加え、LLM／川柳／STT／TTSの上限付きプロセス内ログを表示し、全ログをコピーできる経路は **コード・自動テスト済み**。音声認識原文・棄却理由・配送結果を区別する。成功した高頻度APIアクセスは端末と画面で省略し、400以上は残す。音声波形・認証情報・内部プロンプトは保存しない
- TTS 読み: 例外表 + optional UniDic（`[tts-reading]`）**Phase 1–2 済**  
- 川柳 preface: **見どころ→ここで一句→句** + 自分の世界（pending 中 chat 抑止）**済**  
- 川柳 source atom 品質ゲート: カタログ原文snapshot + 短い詩的解釈を関西弁で発話 + 節単位preface provenance/主張範囲をコード検査 + 見どころ明示要素から一次atomへの再結合 + 詩的解釈を句全体で共有する行別出典 + 表示／読み／行概念を束ねた一行正本 + 出典確定後の一意なカタログ名かな訂正（全文必須ではない）+ UniDicによる漢字候補の事前かな化 + 4生成方式の固定比較 + 行別失敗理由つき最大6回再生成 + 既出候補即時棄却 + fail-closed **済**。自動川柳の一節／一行objectと配列外形はconsumerの同じドメイン検査へ通し、外形だけで創作経路を打ち切らない
- 発句間隔は通常の10分（600000ms）へ復帰済み。短期比較ではローカル環境変数だけを一時変更し、終了後は10分へ戻す
- 降雪・積雪材料: 現在Y×バイオーム気温/降雪高度をコード判定。Y/Z・気温・閾値・downfallはLLMへ出さず、閉じた降水/雷/降雪環境と実測地表雪だけを共有 **済**
- 乗り物材料: 乗車中のみ種別・操縦・実移動を観測し、主語付き事実として川柳・雑談で共有 **済**（エリトラは別課題）
- ambient: プレイヤー入力優先（priority mute 共通 + pending キュー中禁止）+ 地表雷雨中の友好・中立 Mob 抑止（洞窟は維持）**済**
- 通常敵の視認索敵: 見通しあり16ブロック以内。現在視認中に「どっち？」と聞かれたら絶対8方位＋概算距離をコード固定で返す **済**
- エピソード決定記録 A: 非重複イベントごとに発話あり／なしを `eval/episodes.jsonl` へbest-effort追記 **済**（記憶へは混ぜない）
- 支援 B/C `select_sword`: hotbar 0〜8実測 + 実行capability分離 + game-event応答のtyped command + Fabricメインスレッド再検証 + result/ack + episode相関まで **コード・自動テスト・Minecraft実機確認済み**（2026-08-16。自動持ち替え・救助・馬は未）
- 完成度の次の本丸: **観測 materials の解像度**（水辺・旗など。地下での地表背景抑止・落下物・採掘文脈は済）
- 任意: 戦闘中断用 OS AI・chat fallback、通常workshop抽出・修正案の実ログ評価、Phase E 整理、VLM、TTS 読み Phase 3 実測、5-7-5 分割読み

更新したらこの節と `companion-maturity.md` §6 を揃える。
