# ドキュメント案内

DokiDoki Dogido の設計・仕様ドキュメントです。

本体の現行入口は [Rust本体の案内](../dogido-rust/README.md) です。旧Python本体および比較oracleの運用は終了しました。現在Python側には、起動設定・辞書トークン・音声入出力機器・ModernBERT推論の補助を残しています。OS AI接続は撤去しました。残存モジュールと資料の一覧は [Rust本体の補助一覧](../dogido-rust/README.md#残すpython補助と資料) を参照してください。過去に完了・終了した文書については冒頭のステータスと後継リンクを確認し、過去の起動コマンドや未接続の記述を現在の指示として扱わないようご注意ください。

| 入口 | 対象 |
|---|---|
| [../README.md](../README.md) | 製品コンセプト・クイックスタート |
| [../AGENTS.md](../AGENTS.md) | 実装エージェント向けの制約と作業ガイド |
| [ローカルの軽量処理優先とLLM呼出監査](2026-10-18-dialogue-model-call-audit.md) | コード・分類器を優先する方針、呼出回数、分類追加と移行の状況 |
| [ModernBERT 23分類の本体接続](2026-10-23-modernbert-23class-integration.md) | 旧890件版seed42の接続記録。後継は川柳・通常のBERT分離 |
| [川柳と通常対話のBERT分離](2026-10-24-bert-mode-routing.md) | 実装済み。川柳seed42・通常seed48-mobの採用、一入力一分類器、切替中の取消 |
| [旧実装・後方互換経路の削除](2026-10-24-legacy-path-removal.md) | 削除範囲、現行契約、検証結果 |
| [../dev_tools/README.md](../dev_tools/README.md) | 表示プレビュー・音声診断・対話評価・独立試作 |
| [通常会話のドギド視点画像](2026-10-21-chat-image.md) | 一枚撮影・192px・同時生成・一時画像の保持 |
| [Minecraftのテキスト会話](2026-10-09-minecraft-text-dialogue.md) | `/dogido`、音声の切替、表示確認の仕様と確認状況 |
| [2026-10-11の全体検査](2026-10-11-checkpoint-verification.md) | コミット整理前の成功・失敗内訳と実機確認報告 |
| [実機チェック](../dogido-rust/manual-dialogue-check.md) | 起動条件、最初の一巡、個別の確認、結果の記録 |
| 本ページ | 仕様・設計ドキュメントの索引 |

---

## 文書の役割（読む前に）

| 役割 | 意味 | 例 |
|---|---|---|
| **現行仕様** | いまの仕様・方針の基準 | `event-schema` · `state-machine` · `adapter-api` · `dialogue-design` |
| **完成度ハブ** | 「何を足すか」の優先軸 | `companion-maturity` |
| **計画・方針** | 設計・PR 経緯・技術判断。**状態は各文書のヘッダ／表を正**（本索引では断定しない） | workshop · casual · voice · `senryu-rag-plan` · `technical-risks` |
| **参照メモ** | 実装の横で使う一覧・調査メモ（現役） | `mob_list` · 詩の基礎知識データベース · Minecraft Java 公式技術データベース |
| **バグ / 観測メモ** | 切り分け。現行仕様を置き換えない | `bug-player-chat-observation-gaps` |
| **終了・履歴** | 過去の実装・試験記録。現行の起動・編集手順には使わない | 旧Python再配置・py_trees・独立国語試験・`debug-checklist`・初期RAG案 |
| **調査 (`research/`)** | 追加の作業メモ。**現行仕様ではない**が、捨てた資料ではない | `research/haiku` · TTS 地図 · `research/mob_list` |

**機能のステータス（完了 / 未着手 / 一部実装）は、各ドキュメント本体および GitHub Issue の記述を正とします。**
本索引ページで横断的な「完了」一覧表を設けて管理することはしません。

実装上の制約・禁止事項は [../AGENTS.md](../AGENTS.md) を正として遵守してください。

### 関連issueと読む文書

Issueの開閉（Open/Close）状態はリンク先にて確認してください。ドキュメント本文を更新する際は、現行のコード実装および検証記録と厳格に照合し、過去の実機試験結果を変更後の本体での確認完了として読み替えないでください。

| Issue | 主題 | 主ドキュメント |
|---|---|---|
| [#8](https://github.com/yukincom/DokiDoki_Dogido/issues/8) | workshop 入り口（BERT分類・共同編集） | [haiku-workshop-intake-patterns.md](haiku-workshop-intake-patterns.md) · [共同編集](haiku-player-improvement-plan.md) |
| [#37](https://github.com/yukincom/DokiDoki_Dogido/issues/37) | workshop チェックポイント記憶（薄くためて発句のみ） | [haiku-workshop-checkpoint-plan.md](haiku-workshop-checkpoint-plan.md) · `memory-architecture` |
| [#13](https://github.com/yukincom/DokiDoki_Dogido/issues/13) | ボイス速度・間 | `voice-delivery-plan` |
| [#20](https://github.com/yukincom/DokiDoki_Dogido/issues/20) | 複数ユーザー・記憶境界 | `multi-user-tenancy` · `memory-architecture` |
| [#12](https://github.com/yukincom/DokiDoki_Dogido/issues/12) · [#14](https://github.com/yukincom/DokiDoki_Dogido/issues/14) · [#15](https://github.com/yukincom/DokiDoki_Dogido/issues/15) | うみれおんさんアドバイス | 製品・UI 寄り（対応 doc は issue 本文） |
| [#28](https://github.com/yukincom/DokiDoki_Dogido/issues/28) | 材料説明と句の不一致・長い口上 | `haiku-player-improvement-plan` · [dogido-display-overlay-plan](dogido-display-overlay-plan.md) |
| [#29](https://github.com/yukincom/DokiDoki_Dogido/issues/29) | STT が感圧板を誤変換 | [research/minecraft-ja-stt-dictionary-2026-07.md](research/minecraft-ja-stt-dictionary-2026-07.md) |
| [#30](https://github.com/yukincom/DokiDoki_Dogido/issues/30) | 視線先（クロスヘア）観測 | [look-target-observation-plan.md](look-target-observation-plan.md) |

---

## 用途別の読む順番

用途ごとに最短経路を示します。番号は推奨順です。

### 1. コンセプトと全体構成

| # | 文書 | 内容 |
|---|---|---|
| 1 | [concept.md](concept.md) | 製品コンセプト |
| 2 | [project-overview.md](project-overview.md) | システム概要・スコープ境界 |
| 3 | [companion-maturity.md](companion-maturity.md) | 完成度の段階と改善の優先軸 |
| 4 | [current-spec.md](current-spec.md) | 現行仕様の要約 |
| 5 | [integration-architecture.md](integration-architecture.md) | コンポーネント連携 |
| 6 | [future-assistance-and-senryu-app-plan.md](future-assistance-and-senryu-app-plan.md) | 支援アクション・マイクラ句集UI・あんちょこ・OS 連携の将来構想 |
| 7 | [assist-action-architecture.md](assist-action-architecture.md) | 支援アクションの実装箱、episode決定ログ、実装済み `select_sword` 縦切り |
| 8 | [rust-migration-plan.md](rust-migration-plan.md) | Rust移行の到達点と過去の段階・検証記録 |
| 9 | [rust-review-followup-2026-10-04.md](rust-review-followup-2026-10-04.md) | 移行後レビューの照合・設定統合・検証範囲 |

**つながり:** `concept` → 体験の核 · `project-overview` / `current-spec` → 何を作るか · `companion-maturity` → 次に何を厚くするか。

### 2. 接続と運用

| # | 文書 | 内容 |
|---|---|---|
| 1 | [event-schema.md](event-schema.md) | ゲームイベントのスキーマ |
| 2 | [adapter-api.md](adapter-api.md) | サーバー受信 API |
| 3 | [sample-event-log-cases.md](sample-event-log-cases.md) | イベントログの代表ケース |
| 4 | [Rust本体の案内](../dogido-rust/README.md) | 現行のビルド・起動・補助依存 |
| 5 | [実機チェック](../dogido-rust/manual-dialogue-check.md) | Rust本体の一巡・個別確認・結果記録 |
| 6 | [shared-llm-profile.md](shared-llm-profile.md) | 共有MLX endpointと設定プロファイルの選択 |
| 7 | [voice-echo-cancellation.md](voice-echo-cancellation.md) | macOSの任意AEC、独立導入、権限と実機試験 |
| 8 | [debug-checklist.md](debug-checklist.md) | 旧Python版のデバッグ履歴。現在の実機結果とは区別 |
| 9 | [3D身体とRust本線の連携境界](../dogido-rust/docs/body-integration-boundary.md) | Body API v3、自他位置のP1接続、身体と本線の分担・残る接続 |
| 10 | [身体統合の作業計画](2026-10-11-body-integration-plan.md) | 開発・統合履歴、観測地点の移行、P1受け入れ条件 |
| 11 | [3D身体P1の本線導入記録](2026-10-12-body-p1-install.md) | 導入版・検査結果・起動確認・次の実機操作・復旧点 |
| 12 | [現在観測と位置関係の変化](2026-10-15-body-position-observation.md) | 両視点・上下・距離を現在観測へ入れ、変化した結果を既存changesへ渡す |
| 13 | [身体の注意範囲と集中](2026-10-17-body-attention.md) | 向き付き探索範囲、3秒の判断間隔、実到着後5秒の観察 |
| 13a | [発話中の対象を見る](2026-10-18-body-speech-gaze.md) | 実再生中のモブ・音源への注視と終了時の解除 |

Minecraft クライアント側の手順は [adapter/minecraft-fabric/README.md](../adapter/minecraft-fabric/README.md) を参照してください。

**つながり:** adapter → HTTP観測 → `event-schema` → `state-machine`。限定支援だけgame-event応答のtyped commandでadapterへ戻る。endpointと往復形は `adapter-api`、観測payloadは `event-schema`。

### 3. 振る舞いと判断

| # | 文書 | 内容 |
|---|---|---|
| 1 | [state-machine.md](state-machine.md) | 状態機械 |
| 2 | [behavior-spec.md](behavior-spec.md) | 挙動仕様 |
| 3 | [py-trees-integration.md](py-trees-integration.md) | 終了したPython版の優先制御記録。現行はstate-machineとRust本体 |
| 4 | [dialogue-design.md](dialogue-design.md) | 対話モード（base / normal / tension / workshop） |
| 5 | [smell-policy.md](smell-policy.md) | 近接源・温度・天候をコードで解決するスメルバトル |
| 6 | [voice-delivery-plan.md](voice-delivery-plan.md) | ボイス速度・間・川柳の呼吸（#13） |
| 7 | [tts-reading-unidic-plan.md](tts-reading-unidic-plan.md) | TTS 誤読補正・UniDic 方針 |
| 8 | [main-dialogue-integration.md](main-dialogue-integration.md) | 本体の会話所有権・戦闘／雷／川柳中断・再生確定。旧Web検索は撤去済みの履歴 |
| 9 | [monster-schema.md](monster-schema.md) | 敵対エンティティ定義 |
| 10 | [skeleton-spec.md](skeleton-spec.md) · [boss-spec.md](boss-spec.md) · [environmental-hostile-spec.md](environmental-hostile-spec.md) | 脅威種別ごとの仕様 |
| 11 | [mob_list.md](mob_list.md) | モブ日英・反応メモ（人間向け。runtime は catalogs） |

**つながり:** SM = 優先制御 · dialogue = どう喋るか · behavior = 場面例。

### 4. 通常会話

| # | 文書 | 内容 |
|---|---|---|
| 1 | [player-chat-casual-plan.md](player-chat-casual-plan.md) | 雑談の設計原則 |
| 2 | [player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md) | 現行の責務分担と、旧Python版のPR記録 |
| 3 | [player-chat-topic-overfit-plan.md](player-chat-topic-overfit-plan.md) | 弱い語による過適合の抑制・初期設計の記録 |
| 4 | [mob-interaction-tone.md](mob-interaction-tone.md) | モブ反応トーン（公式 Tips 準拠） |
| 5 | [villager-context-plan.md](villager-context-plan.md) | 村人の職業・子供・日課 |
| 6 | [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md) | 観測ギャップの既知課題 |
| 7 | [pillager-banner-chat-plan.md](pillager-banner-chat-plan.md) | 構造物・旗まわりの会話 |
| 8 | [sound-identity-plan.md](sound-identity-plan.md) | 音源の同定・音メモの現行仕様と履歴 |
| 9 | [look-target-observation-plan.md](look-target-observation-plan.md) | 視線先観測・指差しとSTT補正の接続範囲 |
| 10 | [rust-chat-grounding-boundary.md](rust-chat-grounding-boundary.md) | 観測・一般知識・プレイヤー報告の境界 |
| 11 | [conversation-repair.md](conversation-repair.md) | 本人の言い直しと短期履歴の訂正 |

**つながり（改修時の読み順）:**

```text
casual（原則） ──┬── sm-vs-prompt（分担・履歴）
                 ├── topic-overfit（弱い手がかり）
                 └── pillager-banner（観測・structure 詳細）
                        ├── bug-observation-gaps
                        └── sound-identity
```

状態・受け入れ条件は **各計画 doc と issue** を見る。

### 5. 川柳と記憶

| # | 文書 | 内容 |
|---|---|---|
| 1 | [haiku-architecture.md](haiku-architecture.md) | 発句パイプライン |
| 1a | [川柳の材料選びの修正記録](2026-10-20-haiku-material-selection.md) | 周辺材料の候補順、身体観測待ち、見どころの指示文と検証 |
| 2 | [2026-10-18-temporary-senryu-data-collection.md](2026-10-18-temporary-senryu-data-collection.md) | 川柳データ採取中の一時設定と終了後の復帰手順 |
| 3 | [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md) | プレイヤー主導の改善（workshop・OS／端末内AIの限定意味抽出） |
| 4 | [haiku-workshop-intake-patterns.md](haiku-workshop-intake-patterns.md) | 取り込みパターン調査（観察の正本は **#8**） |
| 5 | [haiku-feedback-plan.md](haiku-feedback-plan.md) | フィードバックと長期保存 |
| 6 | [memory-architecture.md](memory-architecture.md) | 記憶モデル |
| 7 | [multi-user-tenancy.md](multi-user-tenancy.md) | 複数ユーザー・記憶境界（**#20**） |
| 8 | [senryu-roadmap.md](senryu-roadmap.md) | ロードマップ |
| 9 | [senryu-rag-plan.md](senryu-rag-plan.md) | カタログ直引きと RAG 方針 |
| 10 | [rag.md](rag.md) | 初期RAG案の履歴。現行方針は`senryu-rag-plan` |
| 11 | [dogido-display-overlay-plan.md](dogido-display-overlay-plan.md) | ゲーム内セリフ表示 UI（計画・#28 関連） |
| 12 | [knowledge-query-integration.md](knowledge-query-integration.md) | 詩・Minecraft公式技術DBを明示質問だけに接続する境界 |
| 13 | [language-learning-progress-plan.md](language-learning-progress-plan.md) | 終了: 別リポジトリで保持する旧learnの計画 |
| 14 | [dogido-utterance-display.md](dogido-utterance-display.md) | ゲーム外の発言履歴、参考資料、STT・LLM・川柳の診断ログ、夜の実機確認手順 |

**つながり:**

```text
architecture（どう詠む）
    → improvement / workshop（一緒に直す）
    → intake-patterns + Issue #8（観察）
    → feedback（読み・想起）
    → memory / multi-user（#20）
    → roadmap · rag-plan（初期案は rag に保存）
```

### 6. 本体の構成と技術課題

| # | 文書 | 内容 |
|---|---|---|
| 1 | [Rust本体の案内](../dogido-rust/README.md) | 現行の責務・起動・検証 |
| 2 | [rust-migration-plan.md](rust-migration-plan.md) | Rust移行の経緯と残る実機確認 |
| 3 | [technical-risks.md](technical-risks.md) | 技術課題・設計判断（現役の論点メモ） |
| 4 | [Fabricアダプター分割計画](2026-10-08-fabric-adapter-split-plan.md) | P1〜P17の構造分割・自動検証完了。状態所有と検証条件 |
| 5 | [Fabric分割の実施記録](2026-10-09-fabric-adapter-split-results.md) | 最終配置、Java・Rust検証、復帰資料、実機未確認項目 |

---

## 分野別の一覧

### 製品

- [concept.md](concept.md)
- [project-overview.md](project-overview.md)
- [companion-maturity.md](companion-maturity.md)
- [current-spec.md](current-spec.md)
- [future-assistance-and-senryu-app-plan.md](future-assistance-and-senryu-app-plan.md)
- [assist-action-architecture.md](assist-action-architecture.md)

### 接続と実行環境

- [event-schema.md](event-schema.md)
- [adapter-api.md](adapter-api.md)
- [sample-event-log-cases.md](sample-event-log-cases.md)
- [integration-architecture.md](integration-architecture.md)
- [runtime-dependencies.md](runtime-dependencies.md)
- [debug-checklist.md](debug-checklist.md)

### 振る舞い

- [Minecraft Java 公式技術データベース](../reference/minecraft_technical/README.md)（1.21.11固定、公式変更事項・レジストリ・データパック・タグ、日本語検索、ローカル生成）
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

### 会話

- [main-dialogue-integration.md](main-dialogue-integration.md)
- [knowledge-query-integration.md](knowledge-query-integration.md)
- [language-learning-progress-plan.md](language-learning-progress-plan.md)
- [dogido-utterance-display.md](dogido-utterance-display.md)
- [player-chat-casual-plan.md](player-chat-casual-plan.md)
- [rust-chat-grounding-boundary.md](rust-chat-grounding-boundary.md)
- [conversation-repair.md](conversation-repair.md)
- [look-target-observation-plan.md](look-target-observation-plan.md)
- [player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md)
- [player-chat-topic-overfit-plan.md](player-chat-topic-overfit-plan.md)
- [mob-interaction-tone.md](mob-interaction-tone.md)
- [villager-context-plan.md](villager-context-plan.md)
- [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md)
- [pillager-banner-chat-plan.md](pillager-banner-chat-plan.md)
- [sound-identity-plan.md](sound-identity-plan.md)

### 川柳と記憶

- [詩の基礎知識データベース](../reference/language_education_and_poetry/README.md)（枕詞・日本と世界の詩形、出典、JSON索引）
- [haiku-architecture.md](haiku-architecture.md)
- [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md)
- [haiku-workshop-intake-patterns.md](haiku-workshop-intake-patterns.md)
- [haiku-feedback-plan.md](haiku-feedback-plan.md)
- [memory-architecture.md](memory-architecture.md)
- [rust-memory-api-and-episodes.md](rust-memory-api-and-episodes.md)
- [multi-user-tenancy.md](multi-user-tenancy.md)
- [senryu-roadmap.md](senryu-roadmap.md)
- [senryu-rag-plan.md](senryu-rag-plan.md)
- [rag.md](rag.md)

### 設計メモ

- [server-package-layout-proposal.md](server-package-layout-proposal.md)
- [server-reorg-and-workshop-order.md](server-reorg-and-workshop-order.md)
- [technical-risks.md](technical-risks.md)

---

## 終了した手順・過去の記録

- [Pythonパッケージ構成案](server-package-layout-proposal.md)・[再配置手順](server-reorg-and-workshop-order.md)：旧本体の整理計画は終了。現行の編集先はRust本体。
- [py_trees統合](py-trees-integration.md)：旧Python版の優先制御記録。
- [初期依存整理](runtime-dependencies.md)：2026年5月の導入検討。現行セットアップはRust本体の案内。
- [初期RAG案](rag.md)：未採用のPython配置・ベクトル化案。現行のカタログ利用と将来条件は[senryu-rag-plan.md](senryu-rag-plan.md)。
- [旧Python版のデバッグ記録](debug-checklist.md)：過去の実プレイ確認。Rust本体の実機結果とは区別。
- [独立国語テキスト試験](language-dialogue-text-test.md)・[手動台本](language-dialogue-manual-test.md)・[独立音声試験](language-dialogue-voice-test.md)：旧Python試験経路は終了。結果は当時の記録として保持。

## 調査記録

調査・レビュー用の作業メモです。**仕様の正本ではありません。** ただし現役の検討材料として残っています。

- [身体視点・座標枠・192pxの単発診断](research/2026-10-21-body-view-probe.md) … 実機の一枚で撮影・枠・受信を確認、Gemmaが中央のプレイヤーと右の村人を区別
- [村人とプレイヤーの画像判別](research/2026-10-21-vision-player-villager.md) … 提供2枚・3解像度の6回答、取り違えと細部・構図依存の確認
- [視野進入の先読み・初回実測](research/2026-10-21-vision-entry-run-01.md) … 飛行を含む180秒の予備試験、的中・外れと待ち時間試算の限界
- [画像の遅延・changes・予測補正の接続監査](research/2026-10-21-vision-temporal-integration.md) … LLM一回・予測補正はコードの合意、次回の座標実測手順と未接続の範囲
- [Minecraft画像の解像度・バイオーム・人物判別](research/2026-10-20-vision-resolution.md) … 提供4枚・48回の直接Gemma試験、一時画像枠の要件と未接続の境界
- [安心ゲージ導入後の対人アライメント失敗](research/2026-10-20-reassurance-alignment.md) … 変更経緯、2026-10-20の実機発話、ログ照合、画像入力に関する仮説
- [research/haiku.md](research/haiku.md)
- [research/biome.md](research/biome.md)
- [research/guardrail.md](research/guardrail.md)
- [research/mob_list.md](research/mob_list.md)
- [research/mobs/](research/mobs/)
- [research/code-review-player-reactivity-2026-07-02.md](research/code-review-player-reactivity-2026-07-02.md)
- [research/tts-landscape-2026.md](research/tts-landscape-2026.md) … TTS 地図・コミュニティ・適性・権利
- [research/minecraft-ja-stt-dictionary-2026-07.md](research/minecraft-ja-stt-dictionary-2026-07.md) … 日本語 MC × STT 辞書調査（#29）

---

## 更新時の約束

- 設計変更を文書化する場合は、**対象ドキュメント本体の状態表記**をあわせて更新する（索引だけで「済」にしない）
- 状態は現行実装・検証記録・関連issueに照らして更新する。実装済み、自動検証済み、実機確認済み、未実装を分ける
- 「古いからアーカイブ」は **実装・issue と照合してから**。現役の論点・一覧・方針を入口スタブにしない
- 新規ドキュメントを追加する場合は、本ページの **用途別の読む順番** または **分野別の一覧** に登録する
- 実装上の制約・禁止事項は [../AGENTS.md](../AGENTS.md) を正とする
- 絶対パス（特定マシンのホーム）を docs に書かない
- `research/` は仕様の根拠にしない。方針が固まったら正本側へ要約して移す
