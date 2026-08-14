# 支援アクションの操縦席 — Hermes Agent の設計パターン評価

**日付:** 2026-08-14  
**状態:** 方針メモ・未実装（現行仕様ではない。完成度の本丸を置き換えない）  
**参照元:** [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)（2026-08-14 参照）。公開実装から設計パターンを調査するが、コードや agent loop は取り込まない。

関連:

- [支援アクション・句集UIの将来構想](future-assistance-and-senryu-app-plan.md)（機能の正。剣→救助→馬）
- [相棒としての完成度](companion-maturity.md)
- [記憶アーキテクチャ](memory-architecture.md)
- [連携構成](integration-architecture.md)
- [AGENTS.md](../AGENTS.md)（Hermes 等の汎用エージェント基盤は使わない）

---

## 1. 位置づけ

「剣！」「脱出して」「馬呼んで」のような世界操作がこれから増える。  
増える前に、各能力を次の形で独立させておくと後が楽になる。

```text
schema + handler + available + risk policy
```

Hermes Agent には似た部品がある。ただしドギドは汎用エージェントではない。  
**参考にするのは設計思想と関心の分割。ファイルと `run_agent` は持ってこない。**

支援の中身（何を補助するか、冒険を代行しない）は [future-assistance](future-assistance-and-senryu-app-plan.md) が正。  
この文書は「どう箱に入れるか」と、Hermes の設計パターンのうち何を採用しないかを整理する。

---

## 2. 判定一覧

| 候補 | 判定 | 理由 |
|---|---|---|
| registry と approval の関心分離（Dogido 固有に再構成） | **設計パターンのみ参考** | `schema` / `handler` / availability / policy を分離する考え方は有用。Hermes の実装は plugin / MCP / TTL など汎用要件を多く含む |
| ActionBudget | **後で小さく自前** | Hermes の IterationBudget はカウンタだけ。ドギドの本体は「考える→tool」ループではない |
| interrupt / 世代破棄 | **思想だけ。今ある層を伸ばす** | 音声 epoch は既にある。足りないのは LLM 世代と、生成が service 単一ワーカーを塞ぐこと |
| MemoryManager / MemoryProvider | **今は取らない** | prefetch / sync は chat-agent 用。分割案は「川柳以外は忘れがち」と衝突 |
| trajectory（1エピソード JSONL） | **参考にする価値あり。場合によっては registry より先** | 今のログ改善段階に直結。[companion-maturity §3.2](companion-maturity.md) |
| approval.py | **実装は採用しない。AUTO / CONFIRM / DENY だけ自前** | 汎用エージェントのコマンド承認要件は過剰。世界操作の出口は1本 |
| background review | **発想だけ参考。自動適用しない** | 川柳教師の候補だが、ドギドでは通常会話から分離したオフライン評価に限定する |
| AIAgent / subagent / cron / gateway / 汎用 terminal・browser | **採用しない** | リアルタイム制御が汎用 agent loop に引っ張られる |

いちばん大事な違い:

> Hermes の registry は、利用可能な tools をモデルへ公開し、実行へ dispatch する汎用基盤の一部。  
> ドギドの registry は tools を LLM へ公開せず、「今押してよい世界操作」をコードが検証して1経路で実行する装置。

似たフィールド分割でも、**誰がボタンを押すか**が違う。ここを混ぜると「判断の主はコード」が崩れる。

---

## 3. いまのドギド（移植先）

### 3.1 すでに下地がある

- 判断: `state_machine` + `py_tree_policy`
- 入力意図: `player_input/routing.py`（閉じたフラグ。workshop intent も同じ思想）
- 発話割り込み: `audio.py` の `_epoch`。割り込みでキュー破棄、古いバッチは再生しない
- イベント stale: `SessionInfo.is_stale_sequence`
- workshop CAS: 古い句への適用を `stale_edit` で棄却
- 脅威割り込み: `threat_interrupts.py`
- 記憶: `MemoryStore` が JSONL の単一 facade
- 支援の機能順: [future-assistance §8](future-assistance-and-senryu-app-plan.md)（剣 → 検証/確認/ログ → 安全地点 → 救助 → 馬）

「ボタンが増えるから registry が要る」は正しいが、ゼロから invent する話ではない。  
既存の「意図は閉じた型、実行はコード」を世界操作へ伸ばす。

### 3.2 まだ無いもの（registry より先に効く）

1. **server → adapter の逆チャネルが無い**  
   adapter はイベントを POST するだけ。capabilities は観測種別（`inventory`, `visual_threats` など）。  
   ホットバー選択・ワープ・召喚の受け口は Java にも受信 API にも無い。  
   registry だけ先に置くと、押しても世界が動かない空の操縦席になる。

   実行権限はワールド種別で異なる。ホットバー選択は client 側で完結するが、プレイヤーの移動や entity の再配置は logical server 側の操作になる。シングルプレイでは同一プロセスの integrated server 上で実行でき、LAN 公開は不要。リモートのマルチプレイではプレイヤー権限を偽装せず、server-side mod または許可済みコマンドなど、サーバーが明示的に認める経路が必要になる。

2. **LLM 生成が service を塞ぐ**  
   `app.py` は `ThreadPoolExecutor(max_workers=1)`。川柳は preface の次イベントで `generate_grounded_haiku` を同期実行（最大6再生成）。  
   この間、hostile イベントはキュー待ち。音声 epoch は「生成が終わったあと」にしか効かない。

3. **決定ログが JSONL になっていない**  
   `action_emit` / `haiku_decision` / `_log_darkness_decision` は uvicorn 警告。  
   壁越し100件の誤警報率は、手で tail しないと取れない。

### 3.3 既存方針との衝突

| やってはいけない寄せ方 | 既存正本 | 扱い |
|---|---|---|
| 好きなバイオームへのワープを第一波のボタンにする | 支援計画は「好きな場所へ移動しない」。救助は最後の安全地表／ベッド | **生存支援の第一波に入れない。** チート専用は別クラスで後回し |
| WorldMemory として発見地点・名付け Mob・安全地点を長期記憶に混ぜる | 川柳以外の長期エピソードは精密に持たない。checkpoint は workshop のみ | 安全地点は **記憶ではなく assist 用ワールド状態**。冒険日記にしない |
| MemoryProvider ABC で prefetch する | Hermes 等は使わない。revision を few-shot しない | facade 分割は `MemoryStore` が壊れてから |
| LLM に今押せるボタン一覧を function-calling で渡す | LLM は言い回しと閉じた抽出まで | **世界操作を tools 配列でモデルに渡さない** |

「○○バイオーム行きたい」は、チート世界の便利機能としてはありうる。  
サバイバルの相棒能力としては、「冒険を代行しない」と正面衝突する。

---

## 4. Hermes を見たうえでの注記

### 4.1 [`tools/registry.py`](https://github.com/NousResearch/hermes-agent/blob/main/tools/registry.py) — 形は良い。中身は移植しない

self-register + `check_fn` + schema + handler、という理解で足りる。ただし現行ファイル自体は:

- plugin override / scope / MCP nuke-and-repave
- 発見用 AST スキャン + disk cache
- `check_fn` の約30秒 TTL + 失敗猶予（外部デーモンが落ちても一時的に True）

ドギドが欲しい `available` は「今この snapshot で合法か」。戦闘は数百 ms で変わる。  
**TTL キャッシュは禁止。** Hermes の check_fn は「そのツールがインストールされているか」向けで、状況ゲートではない。

self-register（import 時に `registry.register`）は多数の tools を扱う構成向け。ドギドは当面1〜5個なので、明示リストの方がテストしやすい。

### 4.2 [`IterationBudget`](https://github.com/NousResearch/hermes-agent/blob/main/agent/iteration_budget.py) — コピーする価値がない

`consume` / `refund` / `remaining` だけ。参考にするなら「incident ごとに上限を持つ」という考え方で足りる。

Hermes では反復的な agent loop の上限として使われる。ドギドの剣は **1回の世界操作**。救助も「確認 → 再検証 → 実行」の短い手順で、LLM が tool を連打する構造ではない。

ActionBudget が生きるのは:

- 同一 combat incident で剣を連打しない
- 確認待ちの救助がタイムアウトする
- （将来）複数ステップ支援が暴走しない

「LLM が自律的に何回行動できるか」という枠は、**自律ループを作らない限り不要**。自律ループは作らない。

### 4.3 MemoryProvider — ドギドの逆

`prefetch` / `sync_turn` / `get_tool_schemas` / `handle_tool_call` は、毎ターン記憶を system prompt に足し、記憶ツールをモデルに渡すためのもの。  
ドギドは危険イベントに memory を入れない、と既に決めている。interface を入れると、次の人が prefetch を雑談に繋ぎたくなる。

### 4.4 trajectory — 名前は大げさ、中身は欲しい

Hermes は訓練用 batch trajectory。ドギドが要るのは **1反応の決定レコード**:

```text
trigger → observation → state_before → decision → action → result
```

新しい記憶システムではない。いまの LOGGER を機械可読にしただけ。  
評価（誤警報率、panic 上げ忘れ、ロジック変更の前後比較）に直結する。

### 4.5 [`approval.py`](https://github.com/NousResearch/hermes-agent/blob/main/tools/approval.py) — 実装は採用しない

AUTO / CONFIRM / DENY を ActionSpec のフィールドにする。  
Hermes の approval は汎用コマンド実行を対象にした設計で、ドギドの限定された世界操作とは要件が異なる。ドギドは **世界操作の出口を1つ**にし、状況と入力元をコードで検証する。

---

## 5. 推奨形（ドギド版）

パッケージ名は `tools/` にしない。LLM tool-calling に見える。  
既存支援計画に合わせて **`dogido_server/assist/`**。

```text
dogido_server/assist/
  types.py       # ActionSpec, ActionContext, ActionResult, RiskPolicy
  registry.py    # 明示登録。dispatch は必ず gate 経由
  gate.py        # AUTO / CONFIRM / DENY + 実行直前の再 available
  select_sword.py
  # 後: escape_to_safe_point.py / summon_mount.py
```

空の `teleport_biome.py` は置かない。mixin に handler を書かない。

### 5.1 ActionSpec

- `name` — 閉じた識別子（`select_sword`）
- `schema` — 引数検証用。OpenAI tools 配列ではない
- `handler` — コード。Minecraft コマンド文字列を LLM から受け取らない
- `available(ctx) -> bool` — **今の snapshot**。キャッシュしない
- `policy` — `auto` / `confirm` / `deny`
- `capability` — adapter が宣言した **実行**能力（観測 capabilities とは別。例: `client.hotbar.select.v1` / `integrated_server.player.teleport.v1`）

Hermes 名の `check_fn` は使わない。状況ゲートだと分かる `available` にする。

### 5.2 誰が押すか

```text
player 発話 /（ごく稀に）状態機械の提案
        ↓
閉じた intent（enum）。LLM を使うなら抽出だけ
        ↓
ActionRegistry.propose(name, args)
        ↓
available(ctx)     … 今押せるか
        ↓
ActionGate         … AUTO / CONFIRM / DENY
        ↓
adapter 逆チャネル … 世界を変える唯一の出口
        ↓
world result
        ↓
発話はカタログ（言い回しだけ leaf 可）
```

| アクション | policy の目安 | 備考 |
|---|---|---|
| 剣へ持ち替え | AUTO | プレイヤーが「剣！」と言ったとき。ドギドが勝手に持ち替えない |
| 危険を知らせる | （既存 SM。assist ではない） | 発話は今どおり状態機械 |
| 安全地点へ脱出 | CONFIRM | 曖昧な「怖い」では飛ばない |
| 所有する馬を呼び戻す | AUTO または短い確認 | 所有権・安全な召喚先が揃ったとき |
| 好きなバイオームワープ | DENY | チート設定が明示されたときだけ別アクション |
| アイテム生成 | DENY | 同上 |

状態機械は「今ピンチ」を言い、ボタンを勝手に連打しない。LLM はボタン一覧を見ない。

### 5.3 既存経路との接続

- 意図の入口は `player_input` に閉じたフラグを足す（workshop と同じ）
- 発話優先・panic 抑止は今の状態機械が勝つ
- CONFIRM 待ち中に hostile が来たら確認を捨てる
- 実行直前に `available` を再評価する（確認中に状況が変わる）

---

## 6. やる順

完成度の本丸は観測のまま。これは **支援を始めるときの箱の順** であり、今すぐ全部足す話ではない。

```text
A. エピソード JSONL（今のログ改善。ボタン無しでも効く）
B. adapter 逆チャネル + 実行 capabilities（押した先）
C. assist registry + gate + select_sword（最初の1ボタン）
D. LLM 世代 ID（生成中の脅威で結果破棄。可能なら生成を service 外へ）
E. ActionBudget（2ステップ支援＝救助の確認フローができたとき）
F. 安全地点のワールド状態ファイル（救助の直前）
G. 川柳オフライン review（critique が溜まってから。自動適用なし）
```

MemoryManager の ABC 分割はこの列に入れない。`MemoryStore` にメソッドを足す方が先。

A と D は支援が無くても今の相棒に効く。  
C を空箱だけで先行させるより、**剣1本で A+B+C を貫通**した方が腐らない。

### A. エピソード JSONL

1反応1行。川柳エントリや short_term の正本に混ぜない。  
例: `.dogido_memory/eval/episodes.jsonl`

評価に要るのは trigger / observation 要約 / mode / decision / 出した layer。発話本文は任意。  
最初の勝ちは、壁越し誤警報の集計。

### B. 逆チャネル

heartbeat 応答に pending commands を載せるか、短い poll。  
最初の command は `select_hotbar` だけ。server がスロット番号を決め、adapter は選択するだけ。  
チートコマンドを文字列で送らない。

### C. 剣

`available`: ホットバーに剣（なければ [支援計画のフォールバック順](future-assistance-and-senryu-app-plan.md)）があり、adapter が `hotbar_select` を出している。  
戦闘中のみにするかは実装時に決める（誤認識抑制）。連続実行はここで抑える。  
これが ActionBudget の最初の実体（incident あたり1回）で、汎用 IterationBudget は不要。

### D. LLM 世代

脅威・player 割り込みで世代を進める。leaf / 川柳完了時に世代が違えば破棄（会話にも workshop pin にも載せない）。  
本丸は「単一ワーカーを長時間占有しない」こと。haiku 完了を executor 外に出すのは別チケット。破棄だけでも価値がある。

### E 以降

C が動いてから。安全地点ファイルは記憶アーキテクチャの「思い出」にしない。

---

## 7. やらないこと

- `run_agent.py` / AIAgent / subagent / cron / gateway / MCP tools の導入
- Hermes を submodule / vendor する
- OpenAI function-calling で世界操作する
- `check_fn` の TTL キャッシュ
- 空の `teleport_biome.py` / `summon_mount.py` を先に量産する
- soft lesson や MemoryProvider を危険経路に繋ぐ
- background review の自動 skill 書き込み
- mixin に assist ロジックをベタ書き
- LangChain / LangGraph など、特定の汎用ワークフロー基盤を前提にすること

AGENTS.md の「Hermes 導入禁止」は維持する。実装に入るとき、必要なら一文足す:

> 汎用エージェントは入れない。世界操作は `assist/` の型付きアクションと ActionGate のみ。

---

## 8. 最初の実装単位（支援に入るとき）

川柳・観測・workshop の PR に混ぜない。1本の縦貫通:

1. `assist/types.py` + `registry.py` + `gate.py`（短い。DENY / unavailable / confirm のテスト）
2. adapter: `hotbar_select` capability + command 実行
3. `select_sword` のみ
4. その1回を episode JSONL に書く

go サインと adapter 調査（ホットバー選択の可否）が揃うまでコードは書かない。
