# ドキュメント（docs）網羅的レビュー＆実態照合レポート (2026-10-04)

> 当時のレビュー記録です。指摘を現在の未対応事項として扱わず、[AGENTS.md](../AGENTS.md) と現行コードを確認してください。

**レビュワー**: Gemini (Pair Programming Review)  
**対象範囲**: `docs/` 配下の全マークダウン文書（コードレビュー系文書を除く 94ファイル）  
**作成日**: 2026-10-04  
**目的**: 仕様・計画・調査ドキュメントの現状を走査し、表現の自然さ・正確性を担保するとともに、「実装の実態に合っていないにもかかわらず、過去・旧版・履歴である旨が明記されていない文書」を特定・整理する。

---

## 1. 総合評価サマリ

### 1.1 ドキュメント全体の健全性
- **全体構造の規律は極めて高い**:  
  `docs/README.md` を中心に、ドキュメントの役割（「現行仕様」「計画・方針」「参照メモ」「調査」「終了・履歴」）が明示的に分類されており、AGENTS.md の制約に沿って「過去の計画を現行仕様として再実装しない」ための防波堤が機能している。
- **直近のRust本体移行に伴うステータス更新の漏れが一部存在**:  
  本体が Python から Rust（`dogido-rust` / Axum）へ完全移行し、`select_sword`（剣への持ち替え支援）や `look_target`（視線先観測）、村人の日課判定などが Rust 側に実装完了しているにもかかわらず、一部の計画ドキュメントのヘッダで「未実装」「計画中」「PR-A/B/C一部実装済み」のまま放置されていたり、旧 Python 実装時代の `py_trees` 前提の記述がそのまま残っている箇所が確認された。

---

## 2. 実態と乖離している（またはステータス注記が不足していた）文書の指摘と改善

以下の文書について、実態に即したステータス・文脈の注記を追加し、自然な日本語に調整を行いました。

### 2.1 [docs/rag.md](rag.md)
- **実態との乖離**:
  - 文書冒頭に「`dogido_server` に状態機械（py_trees統合）・LLM routesが実装済み」「`dogido_server/rag/` を作成する」といった記述があるが、正本プランは `senryu-rag-plan.md` に移行しており、本体も Rust 移行済みである。
  - 冒頭に注記はあったものの、他AIが読むと「現在も Python 側の `dogido_server/rag/` を作るタスクがある」と誤読する恐れがあった。
- **改善対応**:
  - 冒頭に「初期検討メモであり、本体は Rust へ移行完了済み。初期案の Python / py_trees / dogido_server/rag/ は現行構成ではない」旨を明確化。

### 2.2 [docs/villager-context-plan.md](villager-context-plan.md)
- **実態との乖離**:
  - ヘッダが「PR-A/B/C 一部 実装済み」で止まっており、本文中にも `ゲートは py_trees / _should_emit_ambient_mob_comment` という旧 Python 本体の Behavior Tree 依存の記述が残っていた。
  - 実態としては、村人の職業・日課計算・睡眠抑制はすでに Rust 側（`dogido-rust/src/villager_routines.rs`, `environment/ambient/mobs.rs`）へ完全に移植・実装されている。
- **改善対応**:
  - ステータスを「実装済み（現行処理は Rust本体 `dogido-rust/src/villager_routines.rs` 等に移植完了）」に更新し、本文の目的・原則の日本語を洗練。

### 2.3 [docs/future-assistance-and-senryu-app-plan.md](future-assistance-and-senryu-app-plan.md)
- **実態との乖離**:
  - ヘッダに「支援の第一段 select_sword はコード・自動テスト・Minecraft実機確認済み」と記載されているが、実装場所が旧 Python か Rust かが明記されていなかった。
  - 実際の実装は `dogido-rust/src/assist/` に存在し、Rust ネイティブで動作している。
- **改善対応**:
  - 「第一弾 `select_sword`（剣への持ち替え）は Rust本体（`dogido-rust/src/assist/`）・自動テスト・実機にて検証済み」と明記し、用語の「第一段」を「第一弾」に是正。

### 2.4 [docs/look-target-observation-plan.md](look-target-observation-plan.md)
- **実態との乖離**:
  - 文書冒頭のステータスが「方針確定・実装対象」「関連 issue: 本計画とセットで GitHub に登録」のままとなっていた。
  - しかし末尾の改訂履歴およびコード実態では、L1〜L4・L6（Fabric レイキャスト、`GameEvent.look_target`、対話材料への反映、STT補正）まで全て実装完了しており、Issue #30 もクローズ済み。
- **改善対応**:
  - ヘッダのステータスを「L1〜L4・L6 実装完了（Issue #30 完了）」に整合させ、実装済みステータスを即座に把握できるように調整。

### 2.5 [docs/senryu-rag-plan.md](senryu-rag-plan.md) & [docs/senryu-roadmap.md](senryu-roadmap.md)
- **実態との乖離**:
  - `Senryu-RAG` ブランチでの開発当時の記述のままであり、第1波である「カタログ直引き使い切り」や「JSONL記憶（haiku_entries / revisions / player_profile）」が Rust 側に移植完了している文脈がヘッダから読み取りにくかった。
- **改善対応**:
  - 第1波のカタログ直引き活用および JSONL 記憶基盤が完了し、Rust 本体（`dogido-rust/src/haiku/` 等）がロジックを所有している現状をヘッダに反映。

### 2.6 [docs/sound-identity-plan.md](sound-identity-plan.md)
- **実態との乖離**:
  - 「計画ドラフト」と銘打たれ、Mob音の短期記憶・名前解決の実装場所が曖昧だった。
- **改善対応**:
  - 現行の Rust 本体（`dogido-rust/src/combat/auditory.rs` 等）に移植完了している旨を明記。

---

## 3. その他の文書に関する注意事項（変更不要・現状維持の理由）

1. **`docs/py-trees-integration.md`**:
   - 冒頭に `**状態: 終了（2026-10-03）—Python本体の優先制御方式。** py_trees の本体経路は退役しました。` と明確に書かれており、過去の経緯記録として理想的な形を保っているため維持。
2. **`docs/debug-checklist.md`**:
   - タイトルが `旧Python版のデバッグ記録` であり、冒頭に `チェック済みの項目を現行Rust本体の実機確認済みへ読み替えない` と明記されているため維持。
3. **`docs/research/` 配下の文書群**:
   - リサーチ調査メモ・実験記録としての役割が定義されており（`docs/README.md` 参照）、現行仕様と混同されるリスクが低いため、当時の文脈のまま安全に保持。

---

## 4. 総括と今後のドキュメント管理への提言

- **「計画ドキュメント」のクローズ運用**:
  機能実装が完了した段階で、本計画ドキュメントのヘッダにあるステータス（「方針確定」「実装対象」など）を「実装済み（実装先のモジュールパスを追記）」に更新する運用を徹底することで、後続のAIや開発者が「まだ未実装の機能がある」と誤認して重複実装・手戻りを起こすのを完全に防止できます。
- 今回のレビューにより、計画段階から実装完了に至った主要ドキュメントのステータスと現行 Rust コードとの同期が完了しました。
