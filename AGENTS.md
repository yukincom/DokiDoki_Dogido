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
- **LLM**は言い回し生成、通常workshopの有界な次手選択、閉じた型の限定抽出まで。**OS AI**は戦闘中断中の小さな5分類だけに限る。状態変更・保存判断はコード
- **記憶**は JSONL（few-shot 山盛りや Hermes 系汎用エージェントは使わない）

汎用チャットボットや「なんでもできるエージェント」に改造しない。

---

## 2. 触る場所の地図

| パス | 役割 |
|---|---|
| `dogido_server/service.py` | セッション、player 入力、workshop / memory 配線 |
| `dogido_server/state_machine/` | 本体判断。mixin 分割済み。**巨大ロジックを haiku mixin に足し続けない** |
| `dogido_server/state_machine/precipitation.py` | 現在Y・気温・天気・雪ブロック実測から雨／降雪／積雪根拠を確定。LLMには数値を伏せた閉じた気象事実だけを共有 |
| `dogido_server/haiku/workshop.py` | 句 pin（open/close）、旧分類fallback、soft lesson、行概念と採否の検証 |
| `dogido_server/haiku/workshop_agent.py` | 現在句・pending・直近対話を読む有界共同編集step、実検査、発話・根拠検証。状態変更・保存権限は持たない |
| `dogido_server/haiku/workshop_context.py` | 一句の直近対話・見どころ・材料・照合先・修正結果の読み取り用共有文脈。採用・保存の権限は持たない |
| `dogido_server/haiku/hud.py` | Minecraft掛け軸用の読み取り専用投影cache。正本／未採用案／表示専用選択を分離し、GETから判断・保存しない |
| `dogido_server/haiku/combat_pause.py` | 戦闘中の句保持pause、勝利／離脱後の再開、安定した単独敵の暫定継続 |
| `dogido_server/haiku/edit_contract.py` | workshop 行差分の compare-and-swap 検証（生成・採用・保存で共有） |
| `dogido_server/haiku/verse.py` | 一行の表示・確定ひらがな読み・行概念・出典を同じ正本オブジェクトへ束ねる |
| `dogido_server/dialogue/chat_policy.py` | 雑談トピック stance（none を守る等）。`player_chat_policy.py` は re-export |
| `dogido_server/dialogue/player_chat_planner.py` | 通常雑談の会話焦点と一件のread actionを閉じた型で選び、カタログ候補をコード観測IDへ照合。発話・操作・保存はしない |
| `dogido_server/dialogue/light_source_comment_planner.py` | 照明器具所持数の増加に一言が必要かを閉じた型で選ぶ。クラフト／設置を推定せず、発話・暗所状態解除・操作はしない |
| `dogido_server/dialogue/foreground.py` | 本体sessionの会話所有権、戦闘保留、再生完了済み会話から作るsoft川柳材料 |
| `dogido_server/dialogue/main_runtime.py` | 本体の限定国語対話をgame-event worker外で処理し、発話IDの実再生結果へ結ぶ |
| `dogido_server/language_dialogue/main_web.py` | 本体起動時に副作用なく専用Chrome前提を確認し、同意済み検索用providerを休眠構築 |
| `dogido_server/llm/` | prompts / client / haiku 音数・usable / route |
| `dogido_server/llm/structured_contracts.py` | workshop・assist等の現行JSON外形。自動川柳6種は8月完成版のドメイン検査と最大6回再生成を維持 |
| `dogido_server/llm/character_mode.py` | 冒険の怖がり役と workshop の共同編集者役 |
| `dogido_server/platform_ai.py` | Apple Foundation Models / Foundry Local / chat fallback の限定 structured router |
| `dogido_server/player_activity.py` | 乗車中だけ存在する vehicle 状態を、主語付きの雑談・川柳材料へ変換 |
| `dogido_server/memory.py` | JSONL 長期記憶（entries / revisions / critiques / lessons / workshop action-result records） |
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
- 通常workshopの自然な相談は、一つの有界stepが現在句・pending・直近対話から `respond / explain / ask / inspect / propose_revision / compare / show_current / stage_player_edit / accept_pending / reject_pending / close_workshop / unrelated` の次手を選べる。読み・音数・出典はコードの実検査後だけ断言し、修正検証後の再判断は一度だけ。句正本・編集対象・CAS・音数・hard制約・採否・保存・戦闘中断はコード所有を維持する。局所編集・採否・終了は音声認識原文にも行為を示す連続evidenceがあることを必須とし、疑問・否定・条件・引用・伝聞をコードで棄却する。同じ発話の採否＋終了は各意思をそれぞれ検証して一つのtransactionとして実行する
- workshop・assist等の structured 出力は kind ごとに `structured_contracts.py` の現行外形を通す。自動川柳の `haiku_draft` / `haiku_irony` / `haiku_scene` / `haiku_line_grounding` / `haiku_line_regeneration` は、2026-08-16完成版のドメイン検査を正とし、欠けた行の個別再照合と最大6回の内容再生成を途中の共通schemaで打ち切らない。新kind追加時は呼出箇所・prompt例・fallback・consumer・テストを同時に揃える
- STT文脈補正は `source=voice` と現在候補だけ。`raw/normalized` は保持して明示操作の正、`interpreted/semantic` は会話理解と限定意味抽出に使う。意味抽出から保存するときも原文・evidence・CASを検証する。剣支援の実測誤変換 `県に持ち替え/変えて/ハインコ（変更）/チェンに変更` は voice-only・操作語直結の閉じた規則で解釈面だけ補正し、typed・単独の候補語・その語の会話は対象外
- platform provider は設定と可用性だけで選ぶ。Foundry のモデル自動 download は既定 off を守る
- 乗り物は乗車中だけ `player.vehicle` を送る。LLM には必ず「プレイヤーはXXに乗って…」の主語付き事実として渡す
- 本体の一般雑談は既存 `player_chat`、国語・語句の明示質問と学習中の続きだけを有界workerへ渡す。正本DBの明示知識回答、戦況、assist、workshopは状態機械側に残す。利用前提が揃うMacのWeb調査は、同意→案内音声の実再生 `completed`→専用Chromeの一度だけの検索に閉じ、検索自体も同じbackground workerで行う。調査中はWeb用30分期限まで `web` foregroundを保持し、復帰時は話題一件だけを短期文脈へ渡す。ページ本文・URLは本体会話や長期記憶へ持ち帰らない。foreground中は非敵対ambientを止めるが、敵対警告・雷・夕方を止めない。assistant履歴と会話由来の川柳材料は発話IDの実再生 `completed` 後だけ確定し、失敗・取消・古いepochを混ぜない。2026-09-14にユーザー環境のMinecraft自動ポーズと川柳カウント停止を確認済み。追加のpause contractは不要
- 通常 `player_chat` は本文生成前に、実再生済み5往復・現在入力・コード観測から閉じたread actionを一件だけ選ぶ。通常の相槌を全カタログ検索へ流さず、対象照合時だけ候補IDを現在観測へコードで突合する。player報告とassistant履歴は世界観測ではない。未観測の在否・過去誤断言の訂正はコード固定。plannerへ世界操作・保存・戦況判断を渡さない
- 通常 `player_chat` の採否は自然な自己言及・謝罪・方言・比喩を単語だけで落とさない。空出力・役割ラベル・英語説明・生成崩れと、危険助言・未観測名・嗅覚補作・突き放し・現在方針との衝突だけをコードで検査する。不合格時は候補と閉じた理由を同じ会話モデルへ一度だけ返して言い直させ、二案目にも同じ検査を通す。採用・fallback・状態変更はコードが決め、不合格案を会話履歴へ入れない。これは汎用ReActや世界操作loopではない
- 嗅覚は一般LLMセンサーにしない。Fabricが指定した近接源・hotbar・バイオーム・温度・天候をコードのスメルバトルで一件へ解決し、`none / present / suppressed` を明示する。方向・距離・個数・entity IDはserverへ渡さず、匂い単独で戦闘mode／workshop pauseを立てない。現在の匂いへの問いはコード固定文、通常雑談の嗅覚断言は生成後に棄却する。正本は `docs/smell-policy.md`
- 照明器具のinventory増加はクラフト／設置とは断定しない。半スタック以上＋暗所警告外、5分以内の重複、継続中の危険な暗さはコードで無言にし、それ以外だけ有界 `light_source_comment_plan` に発話要否を選ばせる。暗所状態の停止は現在観測でコード確定し、発話leafへ正確な本数を渡さない
- 雑談中の自動川柳は通常10分周期を維持し、現在のplayer replyの後ろまたは次の安全なqueue境界で始める。再生完了済みの直近3 turnだけを、最大80字・最大3 motif・source turn IDつきの `player_reported_context` soft材料として使う。学習・Web中は発句時計そのものを凍結する
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
3. 飛び道具（VLM 常時 / Vector RAG / 状態・保存まで含む汎用エージェント化）は後回し

→ [docs/companion-maturity.md](docs/companion-maturity.md)

---

## 4. よく触るドメイン詳細

### 川柳 workshop（H1–H5.2 + H7-lite + H9 / 検証付き共同編集 / 連続局所編集 / 戦闘中断）

- pin: `SessionInfo.haiku_workshop`（会話 5 往復とは別）
- open: 発句後 / close: drift・timeout・praise・完成三行のformal/conversational revise・自然な終了意図・次の句。pending案の明示採用は現在句へ昇格してopen維持
- 進行: clear_lessons / 固定規則に一致する明示praise / 完成三行revision / 明示reading と代表的なclose・pending採否はコードの速い経路。それ以外の自然文は常駐する会話モデルが現在句・未採用案・直近対話・当該ターンの実検査結果をまとめて読み、説明・質問・検査・提案・比較等から一手を選ぶ。構造不正・低信頼・棄権時は旧intent/evaluation/pending分類器へ戻す。close／採否／局所編集は今回発話の連続evidence、confidence 0.85以上、疑問・否定・条件・引用・伝聞でないこと、現在pending、CASをコード検証して初めて実行する。`unrelated` は同じ入力を通常雑談へ渡し、既存の二回driftへ参加させる
- ループ: 説明だけなら一手で返す。`inspect` は読み／音数／保存済み出典をコード測定して一度だけ再計画する。`propose_revision` は既存editorの最大2回検証を使い、結果コードを見せて一度だけ返答を再計画するが、同じplayer turnでeditorを再実行しない。発話は実行済み観測にない保存・採用・修正・音数・出典を断言できない
- 意味説明後の納得: `ask_meaning` 返答後だけ会話段階を保持し、「そうなんだ」等の意味的ackを会話モデルで抽出する。ackターンはfinding・critique・lessonへ流さずコード固定の終了確認へ進み、次の肯定でclose、続行意思ならopenへ戻す
- `request_repair`: 会話モデルが高信頼に修正要求を抽出し、コードが検証済みfindingを確定できたときだけ、大きいhaiku routeが `expected_text` / `replacement_text` つき差分で修正。コードが元行一致・対象外不変を確認し、別structured評価で意味保持・自然さを照合、出典ID・重複・音数・発句時hard制約を検証。不合格理由と案を次の試行へ返し、同一案は評価前に棄却する。案は採用まで保存せず、採用時にも同じ元句へ適用できるか再確認する。提示文は句本文・採用案内をコード固定し、前置き一言だけ共同編集者leaf
- プレイヤー局所編集: 三行は安定ID `line_1/2/3`、概念番号1/2/3、配列index 0/1/2、位置upper/middle/lower、正規名上五/中七/下五を持つ。各行は表示表記・確定ひらがな読み・出典・provenanceも同じオブジェクトに持ち、表記と読みを別の句にしない。自然な提案では会話モデルが「上の句」「二の句」「真ん中」「後ろのパート」等をこの概念へ対応させ、発話中の行呼称evidence・置換語・句中target fragmentを抽出する。コードが既知呼称・finding・fragmentとの衝突と一意性を検証し、従来の閉じた文字列解析は利用不可・低信頼時のfallbackに限る。finding／明示行／検証済み行概念／一意なfragmentに加え、「旧句より新句」の発話中にある現在句の一行でも対象を固定する。句フレーズ指定はSTTが漢字化しても読みへ戻し、現在の三行へ一意に一致するときだけ採用する。コードでひらがな化・正確な5/7/5音・hard制約・重複を検査し、対象行の表示と読みを同時に置換して未保存三行へ連続CASする。AIが発話にない語を補作したら捨てる。本文・現在句照会はLLMに生成させない。意味質問は保存済み出典を手がかりに、句・当時の材料・見どころ・直近対話を会話モデルが比較して説明する。対応や前の説明の取り違えは認めるが、句・出典記録を自動で書き換えず、不明な意味や由来を作らない。現在はプレイヤーの呼称を訂正せず、生の呼称・正規名・概念IDを将来learning版のフックとしてログへ残す
- pending採否: 通常は同じ共同編集stepが採用／破棄／比較／継続を選び、利用不可時だけ旧pending専用schemaへfallbackする。採否を示す原文中evidence・confidence・現在pending・CASをコード検証し、`accept+close` は保存後、`reject+close` は破棄後にcloseする。採否なしのclose要求はコード固定文で確認する。closeを伴わない採用後は句を次の基準へ昇格しpinを維持
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

- 2026-09-12本体Chrome統合: 限定国語対話へ、専用MCP実行ファイル・MCP SDK・可視設定・Google Chromeの副作用なし確認と、休眠providerを接続。同意確認→「ほな一緒にいこか！」の実再生 `completed`→game-event worker外の一度だけの検索を本体台帳へ結び、失敗・取消・開始前の戦闘・古いepoch・満杯では開かない。既に読書中なら敵対警告後も調査文脈を保持する。成功後は `web` foregroundと30分の読書期限でambient／発句時計を抑止し、明示復帰で調査話題一件だけを本体短期digestへ戻す。専用clientはsession終了時に閉じ、通常Chrome・ページ本文・URL・長期記憶・世界操作を触らない。**コード・自動テスト・非起動preflight済み。実Minecraft／Qwen／TTS／Chrome E2E、OS前面復帰は未確認。2026-09-14にユーザー環境のMinecraft自動ポーズと川柳カウント停止を確認済み。** [詳細](docs/main-dialogue-integration.md)。

- 2026-09-12川柳workshop共同編集: 自然な句相談を、現在句・未採用案・直近4往復・保存済み出典・当該ターンの実検査結果を読む一つの有界agent stepへ統合。説明／質問／読み・音数・出典検査／修正提案／比較／表示／局所編集／採否／終了から一手を選び、検査・editor結果後は一度だけ返答を再判断する。同一turnのeditor再実行、未実行の成功断言、直接の正本・pending・保存変更は禁止。正本、行対象、CAS、音数、hard制約、採否、保存、戦闘中断はコード。局所編集・採否・終了は音声認識原文中の行為evidenceを必須にし、疑問・否定・条件・引用・伝聞を棄却する。採否＋終了は両意思を検証したtransaction、`unrelated`は通常雑談の返答が成立した同じ入力だけを二回driftへ数える。初手不成立は旧分類器、実観測後はコード固定fallbackへ戻す。相談目的・action・outcome・checks・validation code・発話evidence・原文／解釈・正本／pending前後だけを`haiku_workshop_turns.jsonl`へ残し、思考文・agent speech・長期会話は保存／常時注入しない。**コード・自動テスト済み。ローカルQwen独立stepで意味説明、inspect後の返答、修正方向の質問、合成validator不合格後の再質問、schema再試行後の採用＋終了を確認。実Minecraft・実TTS・editor込みE2Eは未確認。**

- 2026-09-11通常雑談の一回再考: `player_chat` の旧い広域禁止語から自然な「ドギド」自己言及・謝罪・「例」「本番」と表面上の方言差を外した。無害な `ドギド:` 話者ラベルは除去して採用する。最初の候補が外形またはgrounding検査に落ちた場合だけ、同じ会話へ候補とコード由来の理由を返し、意味と人格を残した言い直しを最大1回生成する。二案目も危険助言・未観測名・現在嗅覚の補作・突き放し・移動方針衝突を再検査し、再不合格なら従来どおり固定fallback。不合格案は履歴・状態・保存へ入れず、結果をログで区別する。**コード・全Python自動テスト済み。ローカルQwenの独立テキスト試験で、英語ラベル混入と未観測嗅覚の二経路は一回再考後に採用、warm時の追加生成は各約0.7秒。実Minecraft・実TTS・長時間の採用率／自然さは未確認。**

- 2026-09-11スメルバトル: Fabricが実近接源・hotbar 9slot・現在バイオーム・温度・天候を閉じた規則で競わせ、`smell_observation` の `none / present / suppressed` 一件へ解決する。同種非加算、分類tie、腐った肉によるゾンビmask、温度減衰、焚き火の実調理slot、雨上がり180秒、雨雪雷・水中抑止をコードで確定。serverは2観測安定後、同状態一度＋全体2分クールダウンの固定文だけを話す。方向・距離・個数・entity IDは渡さず、匂い単独でcombat／panic／alert／workshop pauseへ入れない。旧 `zombie_scent_clues` は移行互換。1.21.11に実IDがない硫黄ブロック・金のタンポポは保留。**コード・Python/Java自動テスト済み、実Minecraft・実Qwen・実TTSは未確認。** [詳細](docs/smell-policy.md)。

- 2026-09-11照明コメント判断: inventory snapshot の照明器具増加は所持数の増加としてだけ扱い、有界plannerが無言／備え増加への相槌／実際の暗所回復後の安堵から一件だけ選ぶ。半スタック以上＋暗所警告外、同種コメント後5分以内、継続中の危険な暗さはコードで即時に無言。`dark_push` は所持数だけでは止めず、現在の明るさ・危険度による回復判定を維持する。最終leafへ正確な本数を渡さず、入手方法・本数の補作も棄却する。**コード・自動テスト済み、実Minecraft・実Qwen・実TTSは未確認。**

- 2026-09-11通常雑談grounding: 本文生成前に、発話内evidence付きの有界 `player_chat_plan` が会話継続・在否照合・対象同定・観測回答・参照確認・過去誤断言訂正から一件だけ選ぶ。会話継続ではカタログを読まず、対象照合時だけ候補IDをvisual／passive／hearing／現在構造物／乗車／視線先entityのコード観測へ照合する。候補は発話だけで先に決め、関連Mob観測で別対象へすり替えない。明示在否問いとplayerの平叙存在報告もコードrouting hintでactionを再検査する。カタログ一致、player報告、assistant履歴は観測へ昇格しない。全通常雑談の種名白リストも現在観測・player発話・planner同定候補だけへ限定した。未観測の在否は不在断定を避けた固定文、過去誤断言は固定の謝罪・現在観測の切り分け。世界操作・保存・戦況・assist・workshopは従来どおりコード。**コード・自動テスト済み、実Minecraft・実Qwen・実TTSは未確認。**

- 2026-09-11通常雑談の家らしさ: 設定済みリスポーン地点から既存距離内にいて、周辺にベッドまたはドアがあるときだけ場所投影を `home_base` にする。暗い拠点を単なる洞窟へ落とさない一方、水中と実破壊根拠のある採掘中を優先する。窓、リスポーン地点単独、遠いベッド／ドアは家の根拠にせず、暗所危険度・洞窟検出・安全判定は変えない。**コード・自動テスト済み、実Minecraft・実Qwen・実TTSは未確認。**

- 2026-09-09本体会話更新: `none / casual / learning / web / haiku_workshop` のforeground所有権を維持し、通常雑談・正本DB回答・限定国語workerのID付き完了履歴を共有。一般話題は独立試験文脈で答えず元turnを本体 `player_chat` へ一度だけ戻す。学習中の突然の別話題は2分以上または明示名指し／転換なら即時移管、2分未満の宛先不明入力は無言で一件・5分保留し、呼び直しの固定確認が実再生完了した後の肯定でだけ元turnを移管する。有効な本人入力は本体TTSへbarge-inし、取得済みbatch末尾もterminal化するが、読み終えた実 `completed` は生成取消で上書きしない。純粋な音声叫声は通常履歴へ入れず、原文を非永続診断、コード観測を状況メモにする。危険前5往復は危険後3通常turn目まで保護し、`player_died` でforeground戦闘を解放。一件だけの戦闘話題保留10 accepted player turn、雷・夕方の入力再queue、ambient抑止、雑談中10分周期川柳、学習・Web中の周期凍結は維持。固定の会話中川柳導入では未発話解釈をspoken provenanceにしない。**この時点では実Minecraft・実モデル・実TTS・家庭音声は未確認。本体Webとゲームpauseは未接続、Fabric変更なし。** [詳細](docs/main-dialogue-integration.md)。

- 2026-09-09対話・参加予測: 独立音声試験は、起動／明示リセット後の最初の入力を必ず受理し、受理後に常駐chatモデルで次の意味上の発話型を5件だけ予測する。明示名指し・話題転換語・質問・Minecraft話題はコードで必ず通す。それ以外も、予測不一致の大きな話題断絶を発話内根拠つき・信頼度0.85以上で `possibly_not_addressed` と抽出できた場合だけ通常履歴外へ保留し、失敗・低信頼・迷いは受理側へ倒す。保留は上限5件をログに残し、「待たせたね」等は `side_conversation_resolved` として「ええんやで。」、明示訂正は直近1件だけ再処理する。`handoff` は静音契機にせず、5分無活動後の `QUIET` と `/listen` の `MIC_OFF` は分離する。StackChanの未検証scene分類器は移植していない。解釈契約は情報要求・雑談・その他を分離し、一般雑談を本体既存 `player_chat` leafへ渡す。参加予測は返答を生成しない。独立音声hostは `turn_id` と発話IDを結ぶ5往復・5分の台帳を持ち、assistant発話を実再生 `completed` 後だけ履歴へ確定する。生成・参加分類・Google処理は有界直列workerで行い、完了時にepochを再検証する。次の音声は生成・再生中に直近1件だけ保留し、自動barge-inはせず `/interrupt` を明示手段とする。実Google概要は15秒後の1回だけ読み、自動再取得なし。研究中の別質問推定は確認を挟む。ドギド関連380件＋87 subtests成功、Chromeモック50件は直前の15秒化で成功。**通常会話・完了履歴・worker・予測保留の変更後は、実モデル・実家庭音声で未確認**。

- 2026-09-09閲覧集中: 16:34のユーザー実試験で通常会話→Web同意→15秒後の単発取得→取得内容を使う後続対話まで通った。検索完了直後の追加TTSは読む集中を妨げたため削除し、`awaiting_report`・取得内容・Web診断はログへ残したまま可視ページを静かに読めるようにした。無音化後の実音声再確認は未実施。

- 2026-09-09起動修正: 独立音声試験がAEC形式判定で二度停止。形式表示はUSBマイク16kHz／tap48kHzだが、実コールバックは同じI/O周期として双方512フレームだった。一度目のrate比フレーム数の仮定を撤回し、同じフレーム位置のマイク＋参照左右を3chで一つの常駐AudioConverterへ渡す方式へ変更。機器設定は不変。合成インパルス位置・左右・frame不一致停止はnative自己検査済み。helper差替え・旧版退避済み、**再修正後の実キャプチャ・音響品質は再試験待ち**。[詳細](docs/voice-echo-cancellation.md)。

- 2026-09-09: ユーザー操作用の独立 `language_dialogue.voice_test` を追加。本体と共通のAEC/RMS/Silero/Whisperをcallbackで国語対話へ渡し、設定済みTTSを再生する。再生中もマイクを維持し、実再生プロセスの正常終了と発話IDで同意済みの実Chrome起動へ接続。約58分の実行とフエラムネ／タイマー音試験は完走し自己音再帰なし。一方、宛先のない家族会話を拾う問題が見つかり、上記参加状態を追加した。**修正後の再試験、本体・workshop・実OS前面検知への接続は未実施**。[読み上げ台本と起動](docs/language-dialogue-voice-test.md)。

- 2026-09-08: macOSの `voice_input` に任意のCore Audio同期参照＋WebRTC AEC3を追加。Mac再生音全体を一時参照し、RMS/VAD/STTより前に処理する。独立環境・既定off・障害時は生マイクへ自動復帰しない。非録音native自己検査と関連88件＋49 subtests成功。架空TTSの漏れ音のみ約42dB減衰、重なり近端成分は約8dB減衰が残り、自前疑似音声の重なり検査は不合格。**実マイク・実スピーカー・Minecraftとの同時試験は未確認**。Web対話の本体接続も未実施。[導入と実機試験](docs/voice-echo-cancellation.md)。

- 2026-09-08同意ゲート: 独立 `language_dialogue` のWeb起動を、指定文による同意確認→「ほな一緒にいこか！」→発話ID付き正常再生完了通知→一度だけ起動へ変更。拒否・撤回・中断・失効・音声失敗では開かない。関連202件＋32 subtests、ローカルQwenの合成4会話を確認。本体のID付き再生完了通知は未実装で、今回は明示的な模擬完了入力。**本体・実音声には未接続**。

- 2026-09-08待ち時間更新（履歴）: 初回8秒・未完成時の同一タブ再読を実装していたが、2026-09-09に15秒・自動再取得なしへ置き換えた。

- 2026-09-08: 独立 `language_dialogue --web` をGoogle AI概要の対話引き継ぎへ変更。子どもの問いをユーザー提案の関西弁依頼文で検索し、完成概要と紹介文を分離。再読はMinecraft復帰時の歓迎配送後に同じタブだけ一度（前面イベント非対応時は次の発話）。実検索はロボット判定のユーザー報告で停止したまま、`--virtual-web` で取得済み金床概要を再生し、模擬復帰→関西弁の歓迎→概要注入→後続会話を確認。Qwenで4発話＋制御4件、引用一致2件。ドギド168件＋30 subtests、Chrome120件は前段の結果。新文型＋再読の実概要取得、多様な問いの品質は未確認。**実OS前面検知・本体・workshop・音声には未接続**。詳細は `docs/research/language-overview-dialogue-2026-09-08.md`。以下の教材カタログ経路は旧試作で診断用に残す。

- 2026-09-07比較後: 独立 `language_dialogue/` の検索結果に規則・適用範囲を保持。確定かなの音数は既存計数器、一字の学年は検索済み配当を使い、再検索・回答LLMを省く。資料不足と本人の文脈不足を分離。関連115件＋30 subtests、同じQwenで54ターンを確認し数値11件は正答。ただし不要検索・辞書質問の誤分類・意味説明の誤りは残る。**本体・workshop・音声には未接続**。詳細は `docs/research/language-retrieval-comparison-2026-09-07.md` 末尾。

- 2026-09-07: `language_dialogue/` の独立テキスト試験に任意chrome-web接続を追加。裏の根拠資料は非表示、確認済みの対象学年別教材だけ表示し、両本文を読み取りに渡す。通常復帰時は調べた質問1件だけ残して本文と学習中の履歴を外す。関連自動試験90件＋30 subtests、実MCPで非表示資料と小学3・4年向け方位ページの取得を確認。国語の子ども用教材は未充足、Google実検索はCAPTCHA、Geminiと後続の閲覧追跡は未実装。**service・workshop・音声には未接続**。詳細は `docs/research/language-web-dialogue-evaluation-2026-09-07.md`。

- 2026-09-06: `language_dialogue/` に国語の対象・観点・曖昧さの抽出、確認後の検索、根拠付き説明、一時文脈と模擬中断を持つ **独立テキスト試験経路** を追加。ユーザー境界例15件＋短い模擬会話6件。関連自動テスト84件と実モデル21会話＋対象3会話の再確認を実施。説明の付け足し・学習対象の分類に課題が残り、**service・workshop・音声には未接続**。詳細は `docs/language-dialogue-text-test.md` と試験結果文書。

- 所持品質問の省略形は、アイテム語に有無・数量の述語が続く場合に限定。「石炭じゃない？」等の同定や意味質問を、単なる「ない」「何」の部分一致で所持品へ流さない。明示の所持品確認は維持。句の意味質問→通常の所持品確認を続けたサービス経路は **自動テスト済み**。実機音声は未確認。

- 2026-09-05: 一句専用4往復＋見どころ・材料・行別照合・修正結果の共通文脈、再試行への不合格案と具体的コメントの受け渡しは **コード・自動テスト済み**。通常会話は既存5往復で訂正・困惑に答え直す方針を追加。生成回数・採用・保存・戦闘優先は維持。意味説明もユーザー承認のうえ既存の共同編集者leafへ移し、保存済み対応と材料・見どころ・対話を比較する。出典記録は自動変更せず、生成失敗時も旧材料名固定文へ戻さない。実モデルの品質・速度、音声は未確認。

- workshop H1〜H5.2 + H7-lite + H9検証付き共同編集 + 連続局所編集 + 戦闘中断: **済**（現在句・pending・直近対話から相談目的と次手を一つ選び、必要時だけ読み／音数／出典を実検査、既存editor検証結果を見て一度だけ返答を再判断。実行したstepと検証結果を`haiku_workshop_turns.jsonl`へ有界記録するが思考文は保存・常時注入しない。soft lesson / loosen / TTL / 行概念→`line_1/2/3` / ひらがなCAS / 明示採用後も継続 / 状態・保存・戦闘安全はコード。戦闘中は句とpendingを保持してpauseし、通常敵の撃破／死亡／爆散／離脱も分離）
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
- ambient: プレイヤー入力優先（priority mute 共通 + pending キュー中禁止）+ player主体foreground中の友好・中立 Mob 抑止 + 地表雷雨中の抑止（洞窟は維持）**済**
- 通常敵の視認索敵: 見通しあり16ブロック以内。現在視認中に「どっち？」と聞かれたら絶対8方位＋概算距離をコード固定で返す **済**
- エピソード決定記録 A: 非重複イベントごとに発話あり／なしを `eval/episodes.jsonl` へbest-effort追記 **済**（記憶へは混ぜない）
- 支援 B/C `select_sword`: hotbar 0〜8実測 + 実行capability分離 + game-event応答のtyped command + Fabricメインスレッド再検証 + result/ack + episode相関まで **コード・自動テスト・Minecraft実機確認済み**（2026-08-16。自動持ち替え・救助・馬は未）
- 完成度の次の本丸: **観測 materials の解像度**（水辺・旗など。地下での地表背景抑止・落下物・採掘文脈は済）
- 任意: 戦闘中断用OS AI・chat fallback、通常workshop agent・修正案の実ログ評価、Phase E整理、VLM、TTS読みPhase 3実測、5-7-5分割読み

更新したらこの節と `companion-maturity.md` §6 を揃える。
