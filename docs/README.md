# Documentation

DokiDoki Dogido の設計・仕様ドキュメントです。

| 入口 | 対象 |
|---|---|
| [../README.md](../README.md) | 製品コンセプト・クイックスタート |
| [../AGENTS.md](../AGENTS.md) | 実装エージェント向けの制約と作業ガイド |
| 本ページ | 仕様・設計ドキュメントの索引 |

---

## 文書の役割（読む前に）

| 役割 | 意味 | 例 |
|---|---|---|
| **正本** | いまの仕様・方針の基準 | `event-schema` · `state-machine` · `adapter-api` · `dialogue-design` |
| **完成度ハブ** | 「何を足すか」の優先軸 | `companion-maturity` |
| **計画・方針** | 設計・PR 経緯・技術判断。**状態は各文書のヘッダ／表を正**（本索引では断定しない） | workshop · casual · voice · `rag` · `technical-risks` |
| **参照メモ** | 実装の横で使う一覧・調査メモ（現役） | `mob_list` · `debug-checklist` |
| **バグ / 観測メモ** | 切り分け。正本を置き換えない | `bug-player-chat-observation-gaps` |
| **調査 (`research/`)** | 追加の作業メモ。**仕様の正本ではない**が、捨てた資料ではない | `research/haiku` · TTS 地図 · `research/mob_list` |

**状態（済 / 未 / 一部）は各ドキュメント本体と GitHub issue を正とする。**  
索引で横断の「済」表を置かない。

実装上の禁止事項は [../AGENTS.md](../AGENTS.md) を正とする。

### オープン issue とセットで読むもの（整理時に触りすぎない）

作業中・観察中の計画は、**issue とセットで状態が動く**。ドキュメント整理だけで状態欄や本文を書き換えない。

| Issue | 主題 | 主ドキュメント |
|---|---|---|
| [#8](https://github.com/yukincom/DokiDoki_Dogido/issues/8) | workshop 入り口（soft 既定・詩人 leaf） | **closed** · `haiku-workshop-intake-patterns` · `haiku/workshop` |
| [#37](https://github.com/yukincom/DokiDoki_Dogido/issues/37) | workshop チェックポイント記憶（薄くためて発句のみ） | [haiku-workshop-checkpoint-plan.md](haiku-workshop-checkpoint-plan.md) · `memory-architecture` |
| [#13](https://github.com/yukincom/DokiDoki_Dogido/issues/13) | ボイス速度・間 | `voice-delivery-plan` |
| [#20](https://github.com/yukincom/DokiDoki_Dogido/issues/20) | 複数ユーザー・記憶境界 | `multi-user-tenancy` · `memory-architecture` |
| [#12](https://github.com/yukincom/DokiDoki_Dogido/issues/12) · [#14](https://github.com/yukincom/DokiDoki_Dogido/issues/14) · [#15](https://github.com/yukincom/DokiDoki_Dogido/issues/15) | うみれおんさんアドバイス | 製品・UI 寄り（対応 doc は issue 本文） |
| [#28](https://github.com/yukincom/DokiDoki_Dogido/issues/28) | 材料説明と句の不一致・長い口上 | `haiku-player-improvement-plan` · [dogido-display-overlay-plan](dogido-display-overlay-plan.md) |
| [#29](https://github.com/yukincom/DokiDoki_Dogido/issues/29) | STT が感圧板を誤変換 | [research/minecraft-ja-stt-dictionary-2026-07.md](research/minecraft-ja-stt-dictionary-2026-07.md) |
| [#30](https://github.com/yukincom/DokiDoki_Dogido/issues/30) | 視線先（クロスヘア）観測 | [look-target-observation-plan.md](look-target-observation-plan.md) |

---

## Recommended reading order

用途ごとに最短経路を示します。番号は推奨順です。

### 1. Product and architecture overview

| # | Document | Summary |
|---|---|---|
| 1 | [concept.md](concept.md) | 製品コンセプト |
| 2 | [project-overview.md](project-overview.md) | システム概要・スコープ境界 |
| 3 | [companion-maturity.md](companion-maturity.md) | 完成度の段階と改善の優先軸 |
| 4 | [current-spec.md](current-spec.md) | 現行仕様の要約 |
| 5 | [integration-architecture.md](integration-architecture.md) | コンポーネント連携 |
| 6 | [future-assistance-and-senryu-app-plan.md](future-assistance-and-senryu-app-plan.md) | 支援アクション・マイクラ句集UI・あんちょこ・OS 連携の将来構想 |

**つながり:** `concept` → 体験の核 · `project-overview` / `current-spec` → 何を作るか · `companion-maturity` → 次に何を厚くするか。

### 2. Integration and operations

| # | Document | Summary |
|---|---|---|
| 1 | [event-schema.md](event-schema.md) | ゲームイベントのスキーマ |
| 2 | [adapter-api.md](adapter-api.md) | サーバー受信 API |
| 3 | [sample-event-log-cases.md](sample-event-log-cases.md) | イベントログの代表ケース |
| 4 | [runtime-dependencies.md](runtime-dependencies.md) | 実行時依存関係 |
| 5 | [debug-checklist.md](debug-checklist.md) | デバッグ手順 |

Minecraft クライアント側の手順は [adapter/minecraft-fabric/README.md](../adapter/minecraft-fabric/README.md) を参照してください。

**つながり:** adapter → HTTP → `event-schema` → `state-machine`。endpoint 形は `adapter-api`、payload 中身は `event-schema`。

### 3. Behavior and decision-making

| # | Document | Summary |
|---|---|---|
| 1 | [state-machine.md](state-machine.md) | 状態機械 |
| 2 | [behavior-spec.md](behavior-spec.md) | 挙動仕様 |
| 3 | [py-trees-integration.md](py-trees-integration.md) | アクション方針（py_trees） |
| 4 | [dialogue-design.md](dialogue-design.md) | 対話モード（peace / battle 等） |
| 5 | [voice-delivery-plan.md](voice-delivery-plan.md) | ボイス速度・間・川柳の呼吸（#13） |
| 6 | [tts-reading-unidic-plan.md](tts-reading-unidic-plan.md) | TTS 誤読補正・UniDic 方針 |
| 7 | [monster-schema.md](monster-schema.md) | 敵対エンティティ定義 |
| 8 | [skeleton-spec.md](skeleton-spec.md) · [boss-spec.md](boss-spec.md) · [environmental-hostile-spec.md](environmental-hostile-spec.md) | 脅威種別ごとの仕様 |
| 9 | [mob_list.md](mob_list.md) | モブ日英・反応メモ（人間向け。runtime は catalogs） |

**つながり:** SM = 優先制御 · dialogue = どう喋るか · behavior = 場面例。

### 4. Player conversation

| # | Document | Summary |
|---|---|---|
| 1 | [player-chat-casual-plan.md](player-chat-casual-plan.md) | 雑談の設計原則 |
| 2 | [player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md) | 状態機械とプロンプトの分担（PR 順の正本） |
| 3 | [player-chat-topic-overfit-plan.md](player-chat-topic-overfit-plan.md) | トピック過適合の抑制 |
| 4 | [mob-interaction-tone.md](mob-interaction-tone.md) | モブ反応トーン（公式 Tips 準拠） |
| 5 | [villager-context-plan.md](villager-context-plan.md) | 村人の職業・子供・日課 |
| 6 | [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md) | 観測ギャップの既知課題 |
| 7 | [pillager-banner-chat-plan.md](pillager-banner-chat-plan.md) | 構造物・旗まわりの会話 |
| 8 | [sound-identity-plan.md](sound-identity-plan.md) | 音源の同定 |

**つながり（改修時の読み順）:**

```text
casual（原則） ──┬── sm-vs-prompt（PR 順）
                 ├── topic-overfit（弱い手がかり）
                 └── pillager-banner（観測・structure 詳細）
                        ├── bug-observation-gaps
                        └── sound-identity
```

状態・受け入れ条件は **各計画 doc と issue** を見る。

### 5. Senryu (haiku) and memory

| # | Document | Summary |
|---|---|---|
| 1 | [haiku-architecture.md](haiku-architecture.md) | 発句パイプライン |
| 2 | [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md) | プレイヤー主導の改善（workshop） |
| 3 | [haiku-workshop-intake-patterns.md](haiku-workshop-intake-patterns.md) | 取り込みパターン調査（観察の正本は **#8**） |
| 4 | [haiku-feedback-plan.md](haiku-feedback-plan.md) | フィードバックと長期保存 |
| 5 | [memory-architecture.md](memory-architecture.md) | 記憶モデル |
| 6 | [multi-user-tenancy.md](multi-user-tenancy.md) | 複数ユーザー・記憶境界（**#20**） |
| 7 | [senryu-roadmap.md](senryu-roadmap.md) | ロードマップ |
| 8 | [senryu-rag-plan.md](senryu-rag-plan.md) | カタログ直引きと RAG 方針 |
| 9 | [rag.md](rag.md) | RAG 検討メモ（`senryu-rag-plan` と併用） |
| 10 | [dogido-display-overlay-plan.md](dogido-display-overlay-plan.md) | ゲーム内セリフ表示 UI（計画・#28 関連） |

**つながり:**

```text
architecture（どう詠む）
    → improvement / workshop（一緒に直す）
    → intake-patterns + Issue #8（観察）
    → feedback（読み・想起）
    → memory / multi-user（#20）
    → roadmap · rag-plan · rag
```

### 6. Server structure and risk

| # | Document | Summary |
|---|---|---|
| 1 | [server-package-layout-proposal.md](server-package-layout-proposal.md) | パッケージ構成案 |
| 2 | [server-reorg-and-workshop-order.md](server-reorg-and-workshop-order.md) | 再配置と実装順序 |
| 3 | [technical-risks.md](technical-risks.md) | 技術課題・設計判断（現役の論点メモ） |

---

## Catalog by topic

### Product

- [concept.md](concept.md)
- [project-overview.md](project-overview.md)
- [companion-maturity.md](companion-maturity.md)
- [current-spec.md](current-spec.md)
- [future-assistance-and-senryu-app-plan.md](future-assistance-and-senryu-app-plan.md)

### Interface and runtime

- [event-schema.md](event-schema.md)
- [adapter-api.md](adapter-api.md)
- [sample-event-log-cases.md](sample-event-log-cases.md)
- [integration-architecture.md](integration-architecture.md)
- [runtime-dependencies.md](runtime-dependencies.md)
- [debug-checklist.md](debug-checklist.md)

### Behavior

- [state-machine.md](state-machine.md)
- [behavior-spec.md](behavior-spec.md)
- [py-trees-integration.md](py-trees-integration.md)
- [dialogue-design.md](dialogue-design.md)
- [voice-delivery-plan.md](voice-delivery-plan.md)
- [tts-reading-unidic-plan.md](tts-reading-unidic-plan.md)
- [monster-schema.md](monster-schema.md)
- [skeleton-spec.md](skeleton-spec.md)
- [boss-spec.md](boss-spec.md)
- [environmental-hostile-spec.md](environmental-hostile-spec.md)
- [mob_list.md](mob_list.md)

### Conversation

- [player-chat-casual-plan.md](player-chat-casual-plan.md)
- [player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md)
- [player-chat-topic-overfit-plan.md](player-chat-topic-overfit-plan.md)
- [mob-interaction-tone.md](mob-interaction-tone.md)
- [villager-context-plan.md](villager-context-plan.md)
- [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md)
- [pillager-banner-chat-plan.md](pillager-banner-chat-plan.md)
- [sound-identity-plan.md](sound-identity-plan.md)

### Poetry and memory

- [haiku-architecture.md](haiku-architecture.md)
- [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md)
- [haiku-workshop-intake-patterns.md](haiku-workshop-intake-patterns.md)
- [haiku-feedback-plan.md](haiku-feedback-plan.md)
- [memory-architecture.md](memory-architecture.md)
- [multi-user-tenancy.md](multi-user-tenancy.md)
- [senryu-roadmap.md](senryu-roadmap.md)
- [senryu-rag-plan.md](senryu-rag-plan.md)
- [rag.md](rag.md)

### Engineering notes

- [server-package-layout-proposal.md](server-package-layout-proposal.md)
- [server-reorg-and-workshop-order.md](server-reorg-and-workshop-order.md)
- [technical-risks.md](technical-risks.md)

---

## Research notes

調査・レビュー用の作業メモです。**仕様の正本ではありません。** ただし現役の検討材料として残っています。

- [research/haiku.md](research/haiku.md)
- [research/biome.md](research/biome.md)
- [research/guardrail.md](research/guardrail.md)
- [research/mob_list.md](research/mob_list.md)
- [research/mobs/](research/mobs/)
- [research/code-review-player-reactivity-2026-07-02.md](research/code-review-player-reactivity-2026-07-02.md)
- [research/tts-landscape-2026.md](research/tts-landscape-2026.md) … TTS 地図・コミュニティ・適性・権利
- [research/minecraft-ja-stt-dictionary-2026-07.md](research/minecraft-ja-stt-dictionary-2026-07.md) … 日本語 MC × STT 辞書調査（#29）

---

## Conventions

- 設計変更を文書化する場合は、**対象ドキュメント本体の状態表記**をあわせて更新する（索引だけで「済」にしない）
- **オープン issue・進行中実装に紐づく計画 doc は、整理目的だけで状態・本文を書き換えない**
- 「古いからアーカイブ」は **実装・issue と照合してから**。現役の論点・一覧・方針を入口スタブにしない
- 新規ドキュメントを追加する場合は、本ページの **Recommended reading order** または **Catalog by topic** に登録する
- 実装上の制約・禁止事項は [../AGENTS.md](../AGENTS.md) を正とする
- 絶対パス（特定マシンのホーム）を docs に書かない
- `research/` は仕様の根拠にしない。方針が固まったら正本側へ要約して移す
