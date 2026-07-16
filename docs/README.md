# ドキュメント案内

ドギドの仕様・計画・メモの目次です。  
人間向けの製品説明はリポジトリ直下の [README.md](../README.md)、  
AI エージェント向けの注意は [AGENTS.md](../AGENTS.md) を先に。

---

## 読む順番（おすすめ）

全部通読しなくてよい。目的に応じて枝分かれする。

### A. はじめて触る人（概念 → 全体像）

1. [concept.md](concept.md) — 製品コンセプト  
2. [project-overview.md](project-overview.md) — 何を作っているか・周辺方針  
3. [companion-maturity.md](companion-maturity.md) — 完成度の段階と「次に何を厚くするか」  
4. [current-spec.md](current-spec.md) — 現行仕様の要約  

### B. サーバーを動かし・デバッグする人

5. [event-schema.md](event-schema.md) — イベントの形  
6. [adapter-api.md](adapter-api.md) — 受信 API  
7. [sample-event-log-cases.md](sample-event-log-cases.md) — ログ収集ケース  
8. [debug-checklist.md](debug-checklist.md) — デバッグ手順  
9. [runtime-dependencies.md](runtime-dependencies.md) — 実行時依存  
10. [integration-architecture.md](integration-architecture.md) — 連携構成  

### C. 挙動・状態機械を触る人

11. [state-machine.md](state-machine.md) — 状態機械  
12. [behavior-spec.md](behavior-spec.md) — 挙動仕様  
13. [py-trees-integration.md](py-trees-integration.md) — action policy  
14. [dialogue-design.md](dialogue-design.md) — peace / battle / 対話モード  
15. [monster-schema.md](monster-schema.md) — モンスター定義  
16. [skeleton-spec.md](skeleton-spec.md) · [boss-spec.md](boss-spec.md) · [environmental-hostile-spec.md](environmental-hostile-spec.md) — 個別脅威  

### D. 雑談・プレイヤー発話を触る人

17. [player-chat-casual-plan.md](player-chat-casual-plan.md) — 雑談 3 本柱（none を守る等）**実装寄り**  
18. [player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md) — 状態機械 vs プロンプト  
19. [player-chat-topic-overfit-plan.md](player-chat-topic-overfit-plan.md) — トピック過適合・VLM 将来枠  
20. [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md) — 観測ギャップ  
21. [pillager-banner-chat-plan.md](pillager-banner-chat-plan.md) — 旗・ピリジャー  
22. [sound-identity-plan.md](sound-identity-plan.md) — 音の正体  

### E. 川柳・記憶を触る人

23. [haiku-architecture.md](haiku-architecture.md) — 川柳アーキテクチャ  
24. [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md) — **workshop / soft lesson（H1–H5.2）**  
25. [haiku-feedback-plan.md](haiku-feedback-plan.md) — フィードバック実装メモ  
26. [senryu-roadmap.md](senryu-roadmap.md) — 進捗・ロードマップ  
27. [senryu-rag-plan.md](senryu-rag-plan.md) — カタログ直引き優先・RAG は任意  
28. [memory-architecture.md](memory-architecture.md) — 記憶  
29. [rag.md](rag.md) — RAG 初期メモ  

### F. パッケージ整理・リスク

30. [server-reorg-and-workshop-order.md](server-reorg-and-workshop-order.md) — 編集順・workshop 実装順  
31. [server-package-layout-proposal.md](server-package-layout-proposal.md) — パッケージ案  
32. [technical-risks.md](technical-risks.md) — 技術課題  

### G. データ・リサーチ（必要になったら）

- [mob_list.md](mob_list.md)  
- [research/](research/) — biome / haiku / guardrail / レビューメモなど  

---

## テーマ別インデックス

### コンセプト・方針

| ファイル | 内容 |
|---|---|
| [concept.md](concept.md) | 製品コンセプト・コピー |
| [project-overview.md](project-overview.md) | 目的・外部連携の後回し・先に固めること |
| [companion-maturity.md](companion-maturity.md) | 完成段階 A/B/C・観測本丸・やらないこと |
| [current-spec.md](current-spec.md) | 現行仕様 |

### 入出力・連携

| ファイル | 内容 |
|---|---|
| [event-schema.md](event-schema.md) | イベントスキーマ |
| [adapter-api.md](adapter-api.md) | 受信 API |
| [sample-event-log-cases.md](sample-event-log-cases.md) | サンプルログケース |
| [integration-architecture.md](integration-architecture.md) | 連携構成 |
| [runtime-dependencies.md](runtime-dependencies.md) | 依存ライブラリ |
| [debug-checklist.md](debug-checklist.md) | デバッグ |

### 状態機械・脅威・挙動

| ファイル | 内容 |
|---|---|
| [state-machine.md](state-machine.md) | 状態機械 |
| [behavior-spec.md](behavior-spec.md) | 挙動仕様 |
| [py-trees-integration.md](py-trees-integration.md) | py_trees |
| [monster-schema.md](monster-schema.md) | モンスター定義 |
| [skeleton-spec.md](skeleton-spec.md) | スケルトン |
| [boss-spec.md](boss-spec.md) | ボス |
| [environmental-hostile-spec.md](environmental-hostile-spec.md) | 環境脅威 |
| [mob_list.md](mob_list.md) | モブ一覧 |

### 対話・雑談

| ファイル | 内容 |
|---|---|
| [dialogue-design.md](dialogue-design.md) | 対話モード設計 |
| [player-chat-casual-plan.md](player-chat-casual-plan.md) | 雑談 3 本柱 |
| [player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md) | SM vs プロンプト |
| [player-chat-topic-overfit-plan.md](player-chat-topic-overfit-plan.md) | 過適合・VLM 枠 |
| [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md) | 観測ギャップ |
| [pillager-banner-chat-plan.md](pillager-banner-chat-plan.md) | 旗チャット |
| [sound-identity-plan.md](sound-identity-plan.md) | 音の正体 |

### 川柳・記憶・RAG

| ファイル | 内容 |
|---|---|
| [haiku-architecture.md](haiku-architecture.md) | 川柳アーキテクチャ |
| [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md) | プレイヤー主導改善・workshop |
| [haiku-feedback-plan.md](haiku-feedback-plan.md) | フィードバック |
| [senryu-roadmap.md](senryu-roadmap.md) | ロードマップ |
| [senryu-rag-plan.md](senryu-rag-plan.md) | Senryu-RAG プラン |
| [memory-architecture.md](memory-architecture.md) | 記憶アーキテクチャ |
| [rag.md](rag.md) | RAG メモ |

### サーバー構成・リスク

| ファイル | 内容 |
|---|---|
| [server-reorg-and-workshop-order.md](server-reorg-and-workshop-order.md) | 再配置・workshop 編集順 |
| [server-package-layout-proposal.md](server-package-layout-proposal.md) | パッケージレイアウト案 |
| [technical-risks.md](technical-risks.md) | 技術リスク |

### リポジトリ直下（docs 外だが関連）

| ファイル | 内容 |
|---|---|
| [../README.md](../README.md) | 人間向け・コンセプト中心 |
| [../AGENTS.md](../AGENTS.md) | AI 向け・導入と注意 |
| [../adapter/minecraft-fabric/README.md](../adapter/minecraft-fabric/README.md) | Fabric アダプタ |

### research/

探索メモ・レビュー。仕様の正本ではない。必要になったら参照。

| ファイル | 内容 |
|---|---|
| [research/haiku.md](research/haiku.md) | 川柳調査 |
| [research/biome.md](research/biome.md) | バイオーム |
| [research/guardrail.md](research/guardrail.md) | ガードレール |
| [research/mob_list.md](research/mob_list.md) | モブ調査 |
| [research/code-review-player-reactivity-2026-07-02.md](research/code-review-player-reactivity-2026-07-02.md) | 反応性レビュー |

---

## いま特に効くドキュメント（2026-07 時点）

実装が動いている／方針の正としてよく使うもの:

1. [companion-maturity.md](companion-maturity.md) — 何を足すと完成に近づくか  
2. [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md) — workshop 済範囲と撤回事項（H6 等）  
3. [player-chat-casual-plan.md](player-chat-casual-plan.md) — 雑談の実装方針  
4. [server-reorg-and-workshop-order.md](server-reorg-and-workshop-order.md) — コードを動かすときの編集順  
5. [event-schema.md](event-schema.md) / [state-machine.md](state-machine.md) — 基盤  

計画書の「済 / 未 / 撤回」と実装がズレていたら、**どちらかを直してから**作業する。

---

## ファイル追加時

- この README の **読む順番** と **テーマ別インデックス** の両方に行を足す  
- ルート [README.md](../README.md) の「読みどころ」は入口だけ。詳細リストはここを正とする  
