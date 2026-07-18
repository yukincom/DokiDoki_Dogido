# Documentation

DokiDoki Dogido の設計・仕様ドキュメントです。

| 入口 | 対象 |
|---|---|
| [../README.md](../README.md) | 製品コンセプト・クイックスタート |
| [../AGENTS.md](../AGENTS.md) | 実装エージェント向けの制約と作業ガイド |
| 本ページ | 仕様・設計ドキュメントの索引 |

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

### 2. Integration and operations

| # | Document | Summary |
|---|---|---|
| 1 | [event-schema.md](event-schema.md) | ゲームイベントのスキーマ |
| 2 | [adapter-api.md](adapter-api.md) | サーバー受信 API |
| 3 | [sample-event-log-cases.md](sample-event-log-cases.md) | イベントログの代表ケース |
| 4 | [runtime-dependencies.md](runtime-dependencies.md) | 実行時依存関係 |
| 5 | [debug-checklist.md](debug-checklist.md) | デバッグ手順 |

Minecraft クライアント側の手順は [adapter/minecraft-fabric/README.md](../adapter/minecraft-fabric/README.md) を参照してください。

### 3. Behavior and decision-making

| # | Document | Summary |
|---|---|---|
| 1 | [state-machine.md](state-machine.md) | 状態機械 |
| 2 | [behavior-spec.md](behavior-spec.md) | 挙動仕様 |
| 3 | [py-trees-integration.md](py-trees-integration.md) | アクション方針（py_trees） |
| 4 | [dialogue-design.md](dialogue-design.md) | 対話モード（peace / battle 等） |
| 5 | [monster-schema.md](monster-schema.md) | 敵対エンティティ定義 |
| 6 | [skeleton-spec.md](skeleton-spec.md) · [boss-spec.md](boss-spec.md) · [environmental-hostile-spec.md](environmental-hostile-spec.md) | 脅威種別ごとの仕様 |

### 4. Player conversation

| # | Document | Summary |
|---|---|---|
| 1 | [player-chat-casual-plan.md](player-chat-casual-plan.md) | 雑談の設計原則 |
| 2 | [player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md) | 状態機械とプロンプトの分担 |
| 3 | [player-chat-topic-overfit-plan.md](player-chat-topic-overfit-plan.md) | トピック過適合の抑制 |
| 4 | [mob-interaction-tone.md](mob-interaction-tone.md) | モブ反応トーン（公式 Tips 準拠） |
| 5 | [villager-context-plan.md](villager-context-plan.md) | 村人の職業・子供・日課 |
| 6 | [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md) | 観測ギャップの既知課題 |
| 7 | [pillager-banner-chat-plan.md](pillager-banner-chat-plan.md) | 構造物・旗まわりの会話 |
| 8 | [sound-identity-plan.md](sound-identity-plan.md) | 音源の同定 |

### 5. Senryu (haiku) and memory

| # | Document | Summary |
|---|---|---|
| 1 | [haiku-architecture.md](haiku-architecture.md) | 発句パイプライン |
| 2 | [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md) | プレイヤー主導の改善（workshop） |
| 3 | [haiku-feedback-plan.md](haiku-feedback-plan.md) | フィードバックと長期保存 |
| 4 | [memory-architecture.md](memory-architecture.md) | 記憶モデル |
| 5 | [senryu-roadmap.md](senryu-roadmap.md) | ロードマップ |
| 6 | [senryu-rag-plan.md](senryu-rag-plan.md) | カタログ直引きと RAG 方針 |
| 7 | [rag.md](rag.md) | RAG 初期検討 |

### 6. Server structure and risk

| # | Document | Summary |
|---|---|---|
| 1 | [server-package-layout-proposal.md](server-package-layout-proposal.md) | パッケージ構成案 |
| 2 | [server-reorg-and-workshop-order.md](server-reorg-and-workshop-order.md) | 再配置と実装順序 |
| 3 | [technical-risks.md](technical-risks.md) | 技術的リスク |

---

## Catalog by topic

### Product

- [concept.md](concept.md)
- [project-overview.md](project-overview.md)
- [companion-maturity.md](companion-maturity.md)
- [current-spec.md](current-spec.md)

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
- [haiku-feedback-plan.md](haiku-feedback-plan.md)
- [memory-architecture.md](memory-architecture.md)
- [senryu-roadmap.md](senryu-roadmap.md)
- [senryu-rag-plan.md](senryu-rag-plan.md)
- [rag.md](rag.md)

### Engineering notes

- [server-package-layout-proposal.md](server-package-layout-proposal.md)
- [server-reorg-and-workshop-order.md](server-reorg-and-workshop-order.md)
- [technical-risks.md](technical-risks.md)

---

## Research notes

調査・レビュー用の作業メモです。**仕様の正本ではありません。**

- [research/haiku.md](research/haiku.md)
- [research/biome.md](research/biome.md)
- [research/guardrail.md](research/guardrail.md)
- [research/mob_list.md](research/mob_list.md)
- [research/code-review-player-reactivity-2026-07-02.md](research/code-review-player-reactivity-2026-07-02.md)

---

## Conventions

- 設計変更を文書化する場合は、関連ドキュメントの状態表記をあわせて更新する
- 新規ドキュメントを追加する場合は、本ページの **Recommended reading order** または **Catalog by topic** に登録する
- 実装上の制約・禁止事項は [../AGENTS.md](../AGENTS.md) を正とする
