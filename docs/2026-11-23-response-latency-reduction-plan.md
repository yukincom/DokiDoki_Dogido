# 応答レイテンシ短縮の実装計画（2026-11-23）

**状態: 計画（未実装）。** 2026-11-23 の実機計測（通常電源モード復帰後）に基づき、発話終了からドギドの音声再生開始までの待ち時間を **合計 2〜3.5 秒短縮** するための分割実装計画。
各パートは独立して実装・自動テスト・ローカルコミットできる単位に分けている。

---

## 0. 実測ベースライン（2026-11-23 10:24〜10:31）

通常電源モード時の通常会話／視線後返答（`task 638` 等）の実測内訳：

| 区間 | 実測値 | ボトルネックの正体 |
|---|---:|---|
| 話し終わり → 録音確定 (`speech_segment`) | `780 ms` | `silence_ms=800` の無音確定待ち |
| `speech_segment` → `stt_started` | `552〜576 ms` | Silero VAD (`whisper-vad-speech-segments`) の毎発話コールド起動 |
| Whisper STT (`dogido-whisper-worker`) | `293〜490 ms` | 常駐済み（高速） |
| 視線・注目完了 → Rust受信 (`assist_result` / 画像) | `+700〜950 ms` | Fabric の 20tick（1秒）スナップショット周期待ち |
| LLM 入力読み込み (`6,404` トークン) | `4,882 ms` | ① 雑談時の `catalog_knowledge` 全件通過＋`world_context` の `null`/内部メタ<br>② KVキャッシュ追い出し（`f_keep=0.159`）＋呼び名不一致＋画像処理 |
| LLM 生成 (`31` トークン) | `269 ms` | MTPドラフト付き 111 tok/s（高速）。ただし `follows_look` は非ストリーム |
| TTS 合成（UniDic IPC + VOICEVOX） | `443〜1,354 ms` | 文単位で直列実行 |

---

## 作業共通ルール（実装担当向け）

- **1 パート = 1 コミット**。前パートのテストが全通過してから次へ進む。
- **モデル向けプロンプト文面は変えない**（必要な場合は事前に「元 → 新」を本人確認）。本計画は主に「入力データの軽量化・常駐化・通信タイミング・キャッシュ整合・ストリーミング有効化」で短縮する。
- **既存の未関連変更を巻き込まない**（`git add -A` 禁止、変更ファイルのみ個別 stage）。
- **観測台帳（`Session` が保持する正本）を破壊しない**。モデル入力向けの軽量化は「モデルへ渡す直前のコピー」に限定する。
- 完了報告では **実装済み・自動確認済み・実機確認済み** を分けて書く。

---

## Part 1: Silero VAD の `dogido-whisper-worker` 常駐化（効果: 毎発話 -500〜550 ms）

### 旧 → 新
- **旧**: [stt.rs](../../dogido-rust/src/voice/stt.rs) の `vad()` が毎発話ごとに外部CLI `whisper-vad-speech-segments` を新規起動し、`ggml-silero-v6.2.0.bin` をディスクから読み込み直している（実測 552〜576ms）。
- **新**: [native/whisper_worker/main.cpp](../../native/whisper_worker/main.cpp) の常駐ワーカーに任意の `--vad-model` を追加して起動時に1回だけロードし、同じ常駐プロセス上で VAD 判定（例: `op: "vad"` または `transcribe` 前の内蔵チェック）を実行する。CLIフォールバック（`voice_vad_cli`）は互換のため残しつつ、常駐ワーカーが VAD 対応なら外部プロセス起動を省く。

### 対象ファイル
- `native/whisper_worker/main.cpp`
- `dogido-rust/src/voice/whisper.rs`
- `dogido-rust/src/voice/stt.rs`
- `dogido-rust/scripts/build_whisper_worker.py`（必要なら再ビルド確認）
- テスト：`dogido-rust/tests/python/test_whisper_worker_native.py`、`dogido-rust/src/voice/` 内ユニットテスト

### 守るべき不変条件
- VAD 未設定時は従来通りそのまま Whisper へ通す。
- VAD 失敗・タイムアウト時は fail-open（音声を誤破棄しない）を維持する。
- 検出区間 0 件のときだけ `vad_rejected`（`no_speech_segment`）を報告してスキップする。
- `voice_vad_cli` 設定キー自体は撤去しない（既存設定互換）。

### 検証
- `cargo test --manifest-path dogido-rust/Cargo.toml --lib -- voice`
- `python3 dogido-rust/scripts/build_whisper_worker.py` でワーカー再ビルド確認
- 推奨コミット例：`Silero VADを常駐whisperワーカーへ統合し毎発話の起動遅延を削減する`

---

## Part 2: `chat_prompt` の雑談時カタログ絞り込みと `world_context` モデル入力軽量化（効果: 毎ターン -1.5〜2.5 秒）

### 旧 → 新
- **旧**:
  1. [chat_prompt.rs](../../dogido-rust/src/chat_prompt.rs) で `topic_names.is_empty()`（特定のモブ名を含まない通常雑談）のとき、`catalog_knowledge.entries` の全エントリと `villager_routines` がそのままモデルへ渡される。
  2. `world_context` に `null` 値フィールド（`"hunger": null` 等）、内部用の `basis` 文字列、重複する `observed_at` タイムスタンプが多数含まれ、さらに `spaced_json`（空白入り）でシリアライズされている。
- **新**:
  1. `topic_names.is_empty()` の通常雑談では、`catalog_knowledge` は **直近変化（`changes`）や指差し（`look_target`）に関わる対象だけ** に絞る（無関係な周囲全モブの辞書解説を毎回載せない）。`villager_routines` も村人の話題や直近変化がない雑談では省略する。
  2. モデルへ渡す直前のコピーに対し、[dialogue_capacity.rs](../../dogido-rust/src/conversation_observation/dialogue_capacity.rs)（または専用投影関数）で `null` フィールドや不要な内部メタ（`basis` の一部・重複 `observed_at` 等）を安全に削ぎ落とす。

### 対象ファイル
- `dogido-rust/src/chat_prompt.rs`
- `dogido-rust/src/conversation_observation/dialogue_capacity.rs`
- 必要に応じ `dogido-rust/src/reaction_leaf/prompts.rs`（環境反応側でも同じ `world_context` 軽量化を共有する場合）
- 影響テスト：`chat_prompt::tests`、`chat_materials::tests`、`conversation_observation::*`、`mob_environment::tests`

### 守るべき不変条件
- **正本台帳は変更しない**（モデル入力用コピーだけ軽量化する）。
- 「いま見えとるもん」「さっきまで見えとったもん」（`sight.rs`）、位置関係・視認事実（`body_observation`）、匂い（`smell`）、天気・バイオーム、指差し対象（`look_target`）の意味を落とさない。
- `asks_mob_information == true` または会話にモブ名が出たターンでは、該当モブの `catalog_knowledge` を従来通り渡す。
- テストの期待値を変えるときは「削った冗長フィールドだけ」に限定し、機能検証を弱めない。

### 検証
- `cargo test --manifest-path dogido-rust/Cargo.toml --lib -- chat_prompt chat_materials conversation_observation mob_environment reaction_leaf`
- 推奨コミット例：`通常雑談の辞書投影とモデル向けworld_contextを軽量化し入力トークンを削減する`

---

## Part 3: KV キャッシュ共有化（呼び名統一）と `llama-server` 起動プロファイル調整（効果: -0.5〜1.5 秒）

### 旧 → 新
- **旧**:
  1. [reaction_leaf/prompts.rs](../../dogido-rust/src/reaction_leaf/prompts.rs) が `body_player` に固定 `"プレイヤー"` を渡す一方、[chat_prompt.rs](../../dogido-rust/src/chat_prompt.rs) は `player_name`（例: `Meal_chan（プレイヤー）`）を渡しており、`system` 前半の `body_observation` で毎回 KV キャッシュが割れている。
  2. `llama-server` 起動設定（`~/mlx-server/gguf/profiles.json`、管理対象外の外側設定）が `-c 65536`・KV未量子化・既定キャッシュ上限のため、1エントリ約 870〜1,074 MiB で毎ターン追い出しが起きている。
- **新**:
  1. `reaction_leaf/prompts.rs` でも `d["player_name"]` があれば同じ呼び名（`chat_prompt.rs` と同じルール）を `dialogue_with_body` へ渡し、通常会話と環境反応で `base` → `grounding` → `body_observation` の先頭トークン列を完全一致させる。
  2. [shared-llm-profile.md](shared-llm-profile.md) に推奨パラメータ（`-c 16384`〜`32768`、`-ctk q8_0 -ctv q8_0`、`--cache-ram 8192`）と確認指標（`timings.cache_n` / `f_keep`）を明記し、実機反映はユーザー環境の設定ファイルで行う。

### 対象ファイル
- `dogido-rust/src/reaction_leaf/prompts.rs`（および `environment/reaction.rs` 側で `body_player` を呼ぶ経路があれば統一）
- `dogido-rust/src/reaction_leaf/fixtures.json`（必要な場合）
- `docs/shared-llm-profile.md`（本計画フェーズで更新済み）

### 守るべき不変条件
- `player_name` が空・未設定・`"プレイヤー"` のときは従来通り `"プレイヤー"` になる。
- `body_observation` が存在しないときは従来通り説明ブロックを付けない。

### 検証
- `cargo test --manifest-path dogido-rust/Cargo.toml --lib -- reaction_leaf companion_prompt environment::reaction`
- 推奨コミット例：`環境反応と通常会話でbody_observationの呼び名を統一しKVキャッシュ再利用を改善する`

---

## Part 4: Fabric 完了報告の即時送信と `follows_look` の文ストリーム有効化（効果: 視覚・注目系で -0.7〜1.0 秒）

### 旧 → 新
- **旧**:
  1. [DogidoClientAdapter.java](../../adapter/minecraft-fabric/src/main/java/dogido/fabric/DogidoClientAdapter.java) の `shouldSendSnapshot()` が `snapshotIntervalTicks`（既定 20tick = 1秒）を待つため、`body_request` 完了や明示撮影完了の送信が平均 500〜700ms 遅れる。
  2. [turn.rs](../../dogido-rust/src/dialogue/turn.rs) で `(!input.follows_look() || input.player_approach.is_some())` により、`follows_look` 経由の返答で `speech_stream` が無効（非ストリーミング）になっている。
- **新**:
  1. Fabric 側で「未送信の `body_request` 結果または明示撮影結果（`body_view`）がある」ときだけ、`snapshotIntervalTicks` を待たず即時（最小間隔ガード付きで）送信する。
  2. Rust 側の `turn.rs` で `follows_look` 経由の通常返答でも文ストリーム（`speech_stream`）を有効化し、1文目確定と同時に VOICEVOX 合成を開始する。

### 対象ファイル
- `adapter/minecraft-fabric/src/main/java/dogido/fabric/DogidoClientAdapter.java`
- `dogido-rust/src/dialogue/turn.rs`
- 関連テスト：Fabric `DogidoClientAdapter` 系テスト、Rust `dialogue::visual_runtime::tests` / `dialogue::speech_stream_runtime::tests`

### 守るべき不変条件
- 通常時の定期スナップショット間隔（20tick）は増やさない（結果がある瞬間だけ前倒し）。
- 取消（新入力・緊急警告・切断）や `text_displayed` / `playback_status` の分離仕様を壊さない。
- `workshop` モードや `workshop_fallback` の挙動を変えない。

### 検証
- `./adapter/minecraft-fabric/gradlew -p adapter/minecraft-fabric test`
- `cargo test --manifest-path dogido-rust/Cargo.toml --lib -- dialogue::visual_runtime dialogue::speech_stream_runtime`
- 推奨コミット例：`body_request完了と撮影結果の即時送信およびfollows_lookの文ストリームを有効化する`

---

## Part 5: 音声無音待ち・UniDicバイパス・裏ワーカーエラーの切り分け（効果: -0.3〜0.6 秒＋CPU安定化）

### 旧 → 新
- **旧**:
  1. 音声入力の `silence_ms` が 800ms（実測 `required_silence_ms=780`）。
  2. [tts_runtime.rs](../../dogido-rust/src/dialogue/tts_runtime.rs) が漢字を含まないひらがな・カタカナのみの文でも毎回 `tts_unidic_adapter.py` へ IPC している。
  3. [bookshelf/mod.rs](../../dogido-rust/src/bookshelf/mod.rs) の `spawn_research_worker` が 30 秒ごとに `research_archive_pending: Operation not supported (os error 45)` で失敗し続けている。
- **新**:
  1. 漢字・英字など UniDic 解析が必要な文字を含まない文は、明示訂正辞書だけ適用して UniDic IPC をバイパスする。
  2. `research_archive_pending` の `os error 45`（外付けSSD等での非対応操作）の原因を特定し、無駄な 30 秒リトライ・CPU負荷を抑える。
  3. `silence_ms` は実機での話し方（間）を見ながら 550〜650ms 範囲で調整可能にする（設定値のみで変更可能）。

### 対象ファイル
- `dogido-rust/src/dialogue/tts_runtime.rs`
- `dogido-rust/src/bookshelf/mod.rs`（および `research_archive` 周辺）
- テスト：`dialogue::tts_runtime::tests`、`bookshelf::tests`

### 検証
- `cargo test --manifest-path dogido-rust/Cargo.toml --lib -- tts_runtime bookshelf`
- 推奨コミット例：`かな専用文のUniDicバイパスと研究アーカイブ周期エラーの抑制を行う`

---

## 実機での効果確認方法（全パート完了後）

各パート適用後に release ビルド（`dogido-rust` / `dogido-whisper-worker` / Fabric JAR）を行い、以下を `server.log` と `llama-server.log` で照合する：
1. `speech_segment` → `stt_started` の差が `550ms` → `<50ms` になったか（Part 1）
2. 通常会話の `prompt eval` トークン数が `5,300〜6,400` → `2,000〜3,500` 程度へ減ったか（Part 2）
3. `llama-server.log` の `f_keep` 上昇と `removing oldest entry` の減少（Part 3）
4. `body_request` の `executed_at` → `assist_result` 受信差が `700ms` → `<150ms` になり、`chat_leaf` が `rust_stream` になったか（Part 4）
