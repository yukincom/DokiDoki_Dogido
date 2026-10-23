# コードレビュー: Python → Rust 移行後の接続・死にコード・互換

**2026-10-23追記:** 本文のOS AI・端末AI接続と旧分類fallbackは当時の記録。現在は共通BERTで意図を分類し、戦闘からの再開・継続はRustが警戒解除を観測して決める。旧経路を復元せず、[現行仕様](2026-10-23-modernbert-23class-integration.md)を参照する。

> 当時のレビュー記録です。指摘を現在の未対応事項として扱わず、[AGENTS.md](../AGENTS.md) と現行コードを確認してください。

**日付**: 2026-10-03
**作成**: Claude Sonnet 5.5 (Antigravity)
**対象**: `DokiDoki-Dogido` 書類側の作業ツリー（HEAD `cfff196` ＋ 未コミット変更）
**方針**: 実装・修正はしていない。読む／走らせる／報告するだけ。

---

## 0. 結論

**接続は概ね健全。** 起動設定（Python）から Rust 本体、Fabric アダプタまでの受け渡しで、壊れている箇所は見つからなかった。
ただし次の4点は早めに片づけたい。

| # | 内容 | 重さ |
|---|---|---|
| H1 | Python本体の削除（約11万行）と Fabric の新規ファイルが**まだコミットされていない** | 高 |
| H2 | `.env` で設定できるのに**Rustへ届かず黙って無視される設定**が多数ある | 高 |
| M1 | `/dogido` 画面・API 文言が「戦闘・川柳は未接続／移行中」のまま（実際は接続済み） | 中 |
| M2 | `clippy -D warnings`（README の検証手順）が**現在は失敗**する | 中 |

死にコードは「Rust 側」にはほぼ無く、**Python 設定 (`dogido_server/config.py`) と周辺の説明文**に集中している。

### 実行した確認

| 確認 | 結果 |
|---|---|
| `cargo check --all-targets` | 警告ゼロ |
| `cargo test --lib` | 451件中448件成功。3件の失敗はサンドボックスが Python 補助の起動を拒否したため（`cannot start haiku helper / Operation not permitted`）。権限を外して3件だけ再実行し**全件成功** |
| `cargo test --release --test '*'`（統合テスト） | 全件成功 |
| Python `pytest`（`tests/` と `dogido-rust/scripts/test_*.py`） | 204成功・11スキップ |
| `launch_dialogue.py --settings-dir . --check` | 成功。Rust本体・資料・補助の import を確認 |
| 戦闘・環境設定キー（99個）の突合 | Python `Settings` に不足なし・既定値のずれなし |
| Fabric が送るイベント名 8種 | すべて Rust 側に文字列があり、`docs/event-schema.md` にも記載あり |
| `cargo clippy --all-targets -- -D warnings` | **失敗**（M2 参照） |

### 実行していない確認（結論に含めない）

- Fabric の Gradle ビルド・テスト
- `dogido-rust/scripts/check_*.py` の模擬通信スクリプト群
- `--voice` 経路の実行
- 実 Minecraft・実マイク・実 TTS
- `docs/` の Grok レビュー4本のうち、精読したのは `compat-leftover-checklist` の前半のみ

---

## 1. 重要度 高

### H1. 作業ツリーに 572 件の未コミット変更

`git status --short` の内訳は削除 296・変更 229・未追跡 47。
`git diff --stat` は **525 files changed, 6381 insertions, 109910 deletions**。

- HEAD のメッセージは「Rust移行の確定済み履歴を統合」だが、**Python本体の撤去（`tests/test_zombie_scent.py` など）は git に入っていない。**
- Fabric に未追跡の新規ファイルがある。
  - `MobEnvironmentObservation.java`
  - `PortalObservationTracker.java`
  - `SmellSpatialEstimate.java`
  - `ThinkingAnimation.java`
  - `thinking/` テクスチャ
  - 対応するテスト3本
  - 追跡中の Java（`DogidoClientAdapter.java` など）から参照されている可能性が高い。**未確認だが、クリーンな checkout ではビルドできなくなる恐れがある。**
- `dev_tools/catalog_tools/`、`dev_tools/prompt_lab/`、`LICENSE-CHARACTER.md`、`docs/family-single-player.md` も未追跡。

**提案**: 「Python本体の撤去」「Fabric の観測追加」「dev_tools 整理」「docs」を別コミットに分けて入れる。
`AGENTS.md` は書類側を正本としているので、ここが git に入っていないと正本の裏づけがなくなる。

### H2. 設定が黙って無視される（Python `Settings` ↔ Rust のずれ）

`launch_dialogue.py` が Rust に渡すのは、LLM/VOICEVOX/戦闘/警告/川柳/Web 設定だけ。
`config.py` には旧サーバー時代の設定が残っていて、`.env` に書いても**何も起きない**。

**Rust 側がハードコードしていて、Python の値が届かないもの**

| Python 設定 | Rust 側の実態 |
|---|---|
| `max_batch_size`（25） | `server/mod.rs` の `ServerConfig::default()` 固定 |
| `heartbeat_interval_ms`（5000） | 同上 |
| `accepted_schema_version`（`2026-05-24`） | 同上。Java（`DogidoBuildInfo.SCHEMA_VERSION`）と合わせて**3か所に同じ文字列** |
| `max_body_kb`（256） | `server/` に `DefaultBodyLimit` の指定なし。Rust は参照すらしない |
| `allow_non_local_bind` | Rust は常に `ensure!(listen.ip().is_loopback(), "connection-test server requires a loopback address")`。`true` にすると起動失敗する |
| `display_history_max_entries` / `diagnostic_history_max_entries` / `audio_max_pending_batches` | どこからも読まれない |

**どこからも参照されない設定（Python・Rust・scripts・tests・dev_tools を検索）**

- `service_name`、`service_version`
- `say_voice`、`voicevox_prewarm_enabled`、`voicevox_temp_dir`
- `voice_vad_cli`
- `multi_hostile_distance`、`combat_clear_distance`
- `sleep_prompt_cooldown_ms`、`sleeping_neighbor_comment_cooldown_ms`

> 注: `llm_chat_*` / `llm_haiku_*` の一部は検索では未参照に見えるが、`_llm_route_value()` が `getattr(self, f"llm_{route}_{name}")` で動的に読んでいるので**生きている**。削除対象に入れないこと。

**提案**
- 上記の死に設定を `config.py` から削除し、`.env.example` と README の該当記述も揃える。
- 残すなら「Rust では無効」と明記する。
- `allow_non_local_bind` だけは、起動時に「Rust は loopback 専用」と分かる文言で `launch_dialogue.py` 側から止めるのが親切。

---

## 2. 重要度 中

### M1. 自己紹介文言が移行途中のまま

実際は戦闘・川柳・共同編集まで Rust で接続済みなのに、表示や API が古い。

| 場所 | 古い内容 |
|---|---|
| `dogido-rust/src/server/mod.rs:233`（`/dogido` のバナー） | 「Rust版・平時の会話試験。**戦闘・川柳・世界操作は未接続**」 |
| `dogido-rust/src/server/runtime.rs:163` | 「…**句の共同編集は移行中**」 |
| `dogido-rust/src/server/runtime.rs:147` | `source_label_ja` が「Rust版・冒険会話試験」 |
| `dogido-rust/src/server/mod.rs:1`（doc コメント） | 「記憶store・世界操作は未接続」 |
| `dogido-rust/src/main.rs:343` | ログ「…の**移行モード**」 |
| `dogido-rust/src/main.rs:310` | エラー文が「connection-test server requires…」 |
| `/healthz`・runtime の `phase` | `dialogue_preview` / `connection_only` |

- `launch_dialogue.py` が見るのは `runtime == "rust"` と `dialogue_ready` だけなので、`phase` の値を変えても起動確認は壊れない。
- ただし `phase` を表示や外部ツールが読んでいないかは未確認。

**`/dogido` の HTML 文字列置換**

- `server/mod.rs:238` は、Python 時代の `dogido_server/static/dogido.html` を `include_str!` し、`.replace()` で6か所書き換えている。
- **今は6か所とも元HTMLに1回ずつ一致する（確認済み）。**
- ただし `.replace()` は一致しなくても何も言わない。HTML を直したときに画面だけ壊れて、ビルドもテストも通る危険がある。
- テスト（`server/tests.rs:592`）が確認しているのは1か所だけ。
- 「Python環境」という語もそのまま残っている。

**提案**: 文言を現状に合わせる。HTML は Rust 側の資産として `dogido-rust/src/server/` へ移し、置換をやめるのが理想。
すぐやらないなら、6か所すべての一致を確認するテストを足す。

### M2. `clippy -D warnings` が通らない

`dogido-rust/README.md` の検証手順にある次のコマンドが、現在（Rust 1.98.1・edition 2024）は失敗する。

```sh
./dogido-rust/cargo.sh clippy --all-targets --locked -- -D warnings
```

- lib: 6件
  - `combat/outcomes/names.rs:24`、`workshop_validation/mod.rs:343`、`dialogue/workshop_runtime.rs:314`、`reaction_leaf/prompts.rs:101`（以上 `collapsible_if`）
  - `villager_routines.rs:35`（`manual_range_contains`）
  - `mob_environment.rs:6`（`too_many_arguments`、9/7）
- test: 2件
  - `haiku/tests.rs:426`（`needless_range_loop`）
  - `chat_observation/tests.rs:100`（`needless_update`）

挙動のバグではなく書き方の指摘。`cargo check` は警告ゼロなので、直し方を決めれば小さい作業。

### M3. 戦闘・環境の既定値が三重管理

| 場所 | 役割 |
|---|---|
| `dogido_server/config.py` の `Settings` | 実際に Rust へ渡る値 |
| `dogido-rust/src/combat/defaults.json` ＋ `environment/{ambient,danger}_defaults.json` | キー集合と Rust 側の既定 |
| `combat/model.rs` の `Settings::default()` / `merged()` | 上記 JSON を読み、Python からの上書きを検査 |

- 今日時点では**キー不足なし・既定値の差ゼロ**（99キーを突合済み）。
- ただしランチャーは JSON の全キーを `getattr(settings, key)` で埋めるため、**JSON の既定だけを直しても実運用では効かない。** `cargo test` では効いて見えるので紛らわしい。
- `Settings` の doc コメントは「Python既定値から抽出した」とあり、Python が正本という前提になっている。
- 複数ファイルにまたがる重複キーが8個ある。
  - `conversation_ambient_mute_ms`、`ender_eye_comment_cooldown_ms`、`ender_eye_recent_ms`、`player_input_priority_cooldown_ms`、`special_biome_comment_cooldown_ms`、`structure_comment_cooldown_ms`、`weather_sound_recent_ms`（`combat` と `ambient`）
  - `darkness_llm_comment_cooldown_ms`（`ambient` と `danger`）
- 上書き順が逆。Rust は `defaults → danger → ambient`、ランチャーは `defaults → ambient → danger`。キーだけ使うので今は無害。

**提案**
- 「JSON とPython既定が一致する」ことを確認する pytest を1本足す。
- 正本をどちらにするか決める（個人的には JSON を正本にして `config.py` から削るのが筋）。
- 重複キーは1か所にまとめる。

### M4. `events/models.rs` の「生成済み」表記が嘘になっている

- 先頭に「Generated by scripts/generate_event_types.py; do not edit field declarations by hand.」とある。
- その生成器は**作業ツリーで削除済み**（`git ls-files -d` で確認）。元の Pydantic モデルも無い。
- `dogido-rust/README.md:524` にまだ `python dogido-rust/scripts/generate_event_types.py` が載っている（ただし「移行時の記録（終了）」節の中）。
- 実質は**手編集が正本**になっているので、ヘッダを現状に直す。誤って「再生成」すると全部消える、という誤解を防げる。

---

## 3. 重要度 低

### L1. 「Python と一致」テストと Python 癖の再現

- テスト名が `*_match_python` / `*_canonical_python` のものが約30件。fixture は約33MB。
- 方針（移植 fixture は回帰資産として保持）どおりなので問題はない。ただし将来、Rust 側の挙動を意図して変えるとテスト名が事実と食い違う。fixture を手で直すことになる。
- `compat.rs`（`is_python_whitespace` / `json_truthy` / `fold_kana`）に Python 由来の癖が集約されていて、約20モジュールが `as space` などで依存している。今は正しいが、「Python互換」ではなく「ドギドの仕様」として doc に書き直しておくと誤って直されにくい。
- 遅いテスト: `knowledge::provider::tests::full_facts_sources_and_order_match_canonical_python` は debug で60秒超。lib テスト全体は約120秒。

### L2. 空洞なテストと小さな残骸

- `dogido-rust/scripts/test_haiku_tokens.py:15` の `assert 'dogido_server.state_machine.machine' not in sys.modules` は、そのモジュールがもう無いので**常に真**。意味がない。
- `dogido_server/state_machine/` に `.DS_Store` だけが残る（空ディレクトリ）。
- `dogido_server/config.py:147` のコメントが「`dogido_server.voice_input` プロセス」と、存在しない構成を指している。
- `dogido_server.egg-info/SOURCES.txt` が旧構成（`service.py` など）のまま。`.gitignore` 済みで実害はない。`pip install -e .` で更新される。

### L3. Rust 単体テストが Python 環境に依存

- `workshop_editing::tests`（2件）と `dialogue::knowledge_queue::tests`（1件）が Python 補助を起動する。起動できない環境では失敗する。
- 今は個人環境なので問題ない。CI を作るときは、Python 補助を呼べる環境が要る。
- 出力に出る `simulated SDK close failure` のトレースバックは、意図した模擬エラーで異常ではない。

### L4. 起動ランチャーはバイナリの新しさを見ない

- `check_runtime_files()` は `dogido-rust/target/release/dogido-rust` が**存在して実行可能か**しか見ない。
- 今日時点ではバイナリ（23:21 ビルド）がソースより新しいことを確認済み。
- ただし古いバイナリで起動しても気づけない。バージョンや git rev の表示、またはソースとの mtime 比較を `--check` に足すと安全。

### L5. Rust 本体のデータが `dogido_server/`（Python パッケージ）にある

- `reaction_leaf/prompts.rs` が `dogido_server/llm/reaction_situations.json`、`server/mod.rs` が `dogido_server/static/dogido.html` を `include_str!` している。
- `knowledge/retrieval.rs` は helper のパスから3階層上を root とみなし、実行時に `dogido_server/language_dialogue/source_cards.json` を読む。
- 家庭用配布は `build_family_client.py` が `dogido_server/` と `data/` を丸ごとコピーして成立している。意図どおり動く。
- ただし「Python補助」の名前のディレクトリが Rust 本体の正本データ置き場になっている。将来 `data/` 等へ移すときに、`pyproject.toml` の `package-data`、`family_runtime.py`、`build_family_client.py` を一緒に直す必要がある。

---

## 4. 確認して問題なかったもの

- **ポート**: 標準は 5055（Rust の `--listen` 既定、Fabric 既定、README、`.env.example`、ランチャー）。`dogido-rust/*.command` だけが 5056 と従来の記憶保存先を明示する、という `AGENTS.md` の説明どおり。矛盾なし。
- **認証と待受**: `/api/` は Bearer 認証。`DOGIDO_AUTH_TOKEN` は環境変数で渡り、引数にもログにも出ない。待受は loopback に限定。
- **旧 Python の混入防止**: `scripts/family-client/family_runtime.py` の `LEGACY_RUNTIME`（`app.py`・`service.py`・`voice_input.py` が残っていたら検出）は、互換の残りというより有益な安全装置。
- **ランチャー → Rust の設定キー**: 戦闘・警告の全キーが `Settings` に存在し、未知キーは Rust が起動時に拒否する構造。
- **旧 Python モジュールへの import**: 残っている `dogido_server` の import 先（`config` / `platform_ai` / `combat_input_contract` / `voice_*` / `echo_input` / `language_dialogue/*`）はすべて実在し、`AGENTS.md`・README の「残す Python 補助」一覧と一致する。
- **`compare_*.py` の記述**: `dogido-rust/README.md` に多数あるが、「移行時の記録（終了）」節として明示されている。コマンドは実体が無く動かないが、方針どおりの履歴。

---

## 5. 別の指摘（`docs/` の Grok 4.5 レビュー）

`docs/2026-10-03-Grok4.5-*.md`（4本・未追跡）は、**旧 Python 本体が残っていた時点の構成**（`service.py` 約4500行、`mixins/*`、`:5055`=Python／`:5056`=Rust の並走）を前提にしている。現状はその実体がもう無い。

- 精読したのは `compat-leftover-checklist` の前半のみ。
  - §1.1（反応 leaf の Python plain-speech 互換）
  - §3.x（Python ↔ Rust 二重実装）
  - §4.x（mixin ダイエット）
  - 上記は現状では**対象が存在しない**（旧構成の指摘）。
- §1.2（`player_chat_policy` の旧表記）は、他の docs には残っている。たとえば `docs/player-chat-casual-plan.md`、`docs/server-reorg-and-workshop-order.md` など（`git grep` で多数ヒット）。ただし多くは終了・履歴扱いの文書。
- このまま置くと、エージェントが「まだ Python 側に直すものがある」と誤解する。
  - 冒頭に「旧構成時点の記録。現状は2026-10-03 の Rust 本体」と一文添える。
  - または削除する。

---

## 6. 推奨する順番

1. **H1**: 未コミット変更を意味のまとまりでコミット（特に Fabric の未追跡 Java）。
2. **H2**: 死んだ Python 設定の削除と、無視される設定の明記。
3. **M1**: `/dogido` の古い文言の更新と、HTML 置換の一致テスト。
4. **M3**: 既定値の一致テスト1本。
5. **M2 / M4**: clippy の整理と、`events/models.rs` ヘッダ訂正。
6. **L 系**: 空洞テスト、空ディレクトリ、Grok docs の注記。
