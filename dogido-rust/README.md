# ドギド Rust本体

HTTP受付、ゲーム判断、通常会話、川柳・共同編集、記憶、知識検索、音声入力の制御と配送をRustが所有します。旧Python本体・比較oracleの運用は終了しました。Pythonは設定、UniDic token、Whisper設定、AEC、端末AI、Chrome/MCPの接続補助に残ります。

保存した句だけを相談する場合は [川柳のテキスト相談室](workshop-text.md)、Minecraftと音声を合わせた確認は [実機チェック](manual-dialogue-check.md) を使います。

## 準備

Rustの版は `rust-toolchain.toml`、依存は `Cargo.lock` で固定しています。Python補助はリポジトリのルートで `pip install -e .`、必要に応じて `.[tts-reading]` 等の追加依存を導入します。共有LLMとVOICEVOXは設定した既存のものを使用します。

以下はすべてリポジトリのルートから実行します。

```sh
./dogido-rust/cargo.sh build --release --locked
```

`cargo.sh` は通常のRust環境に加え、`DOGIDO_RUST_TOOLCHAIN_DIR` で指定した `cargo/`・`rustup/` を使えます。家庭用配布は準備済みのRust本体を同梱し、利用先でのRustビルドを前提にしません。

## 設定と既定値

戦闘・環境の99項目とHTTPの4項目の既定値は、共有する `dogido_server/runtime_defaults.json` 一つにあります。Rustは `src/runtime_settings.rs`、Python起動補助は `dogido_server/runtime_settings.py` から読みます。`config.py` は型・入力制約と `.env` の上書きを担当し、同じ数値を再定義しません。Python単独導入と家庭用配布にも同じJSONを含めます。

`max_batch_size`、`max_body_kb`、`heartbeat_interval_ms`、`accepted_schema_version` はRustのHTTP設定へ渡ります。待受は `127.0.0.1` / `::1` / `localhost` に限定します。旧Python本体用の廃止設定が `.env` / `.env.shared` / 環境変数に残る場合は、設定名だけを起動時に知らせます。既存ファイルや設定値は自動変更しません。

`voice_vad_cli` は現役です。`voice_settings.resolve_vad_paths()` が明示したVAD実行ファイルを解決し、`launch_dialogue.py --voice` の `vad.cli` を通じてRustの音声入力へ渡します。通常の本体起動だけでは、この音声入力経路の確認を代用できません。

ソースのある作業フォルダでは、起動時と `--check` にCargoが記録したrelease依存ファイルの更新時刻を確認します。実行ファイルより新しいソース・埋込資料や、ビルド後に削除した依存があれば再ビルドを案内して停止します。releaseに含まれないテスト専用ファイルは対象外です。これは更新時刻による確認で、内容ハッシュやGit版の証明ではありません。ソースを持たない家庭用セットは作成元で同じ確認を済ませてから同梱します。

## 起動と停止

### 標準の起動（5055）

リポジトリのルートで、準備したPython補助環境を有効にして実行します。`.env` の一般設定を使い、ポートの既定は5055、記憶の保存先は設定した `memory_dir` です。

```sh
source .venv/bin/activate
python dogido-rust/scripts/launch_dialogue.py --settings-dir . --check
python dogido-rust/scripts/launch_dialogue.py --settings-dir .
```

起動後は [会話画面](http://127.0.0.1:5055/rust-chat) を開けます。Fabricの `server_base_url` も標準は `http://127.0.0.1:5055` です。ポートを設定で変えた場合は双方を揃えます。

マイクはWhisper・VAD・AECの準備後、同じリポジトリのルートを開いた別のターミナルで起動します。

```sh
source .venv/bin/activate
python dogido-rust/scripts/launch_dialogue.py --settings-dir . --voice --check
python dogido-rust/scripts/launch_dialogue.py --settings-dir . --voice
```

`--check` は設定・本体ファイル・補助を確認するだけで、サーバー・モデル生成・録音・Chromeを開始しません。終了は各ターミナルで `Ctrl+C`。今回起動した録音・補助・再生プロセスを回収し、共有LLMとVOICEVOX本体はそのままにします。

既存の `dogido-llm` 補助環境を使う場合は、`./scripts/start_dogido.command server` と別ターミナルの `./scripts/start_dogido.command voice` も同じRust起動へ接続します。`--profile shared` は従来どおり `.env.shared` を重ねます。

### 起動ファイルを使う

`dogido-rust/start_dialogue.command` / `start_voice.command` も同じ設定のポート（既定5055）と記憶保存先を使います。移行時の5056固定は廃止しました。

```sh
./dogido-rust/start_dialogue.command --check
./dogido-rust/start_dialogue.command
# マイクは別ターミナルで起動
./dogido-rust/start_voice.command --check
./dogido-rust/start_voice.command
```

会話画面・Fabric接続先は上の標準起動と共通です。`start_voice.command` は従来のAEC・無音800ms指定を維持します。Python補助環境は `DOGIDO_PYTHON`、設定を読むルートは `DOGIDO_RUST_SETTINGS_DIR` で指定できます。省略時はGit共通ディレクトリ側の設定ルートと、その `dogido-llm/bin/python` を使用します。Git情報がない配布フォルダでは、そのフォルダを設定ルートにします。Python環境の自動導入はしません。

記憶保存先は `DOGIDO_MEMORY_DIR` で指定します。移行時の保存先を引き継ぐ場合は、設定フォルダの `.env` に次を指定してください。本体とテキスト相談室が同じ既存記録を読み、起動時にファイルを移動・統合することはありません。相対パスは設定フォルダ基準です。

```dotenv
DOGIDO_MEMORY_DIR=.dogido_memory/rust-migration
```

未指定の既定は `.dogido_memory` です。接続先を変えた場合はMinecraftの `server_base_url` も合わせ、Minecraftを再起動して読み直します。

## 変更箇所と確認

| 処理 | 現行実装 |
|---|---|
| セッション・配線・取消 | `src/dialogue/` |
| 戦闘・環境 | `src/combat/` · `src/environment/` |
| 通常会話・観測照合 | `src/planner/` · `src/chat_*` · `src/conversation_observation.rs` |
| 川柳・共同編集・記憶 | `src/haiku/` · `src/workshop_*` · `src/haiku_memory.rs` |
| 知識・限定国語 | `src/knowledge/` · `src/language/` |
| 音声入力・配送 | `src/voice/` · `src/dialogue/audio.rs` |

`source_cards.json`、`reference/`、カタログは現役の資料です。旧Python本体とともに削除しません。Python SDK補助へ会話判断や保存権限を戻さず、各SDKとの入出力に閉じます。

### 残すPython補助と資料

パスはリポジトリのルートからの相対表記です。

| パス | 残す役割 |
|---|---|
| `dogido_server/__init__.py` · `dogido_server/config.py` | パッケージ入口と既存設定の解決 |
| `dogido_server/platform_ai.py` · `dogido_server/combat_input_contract.py` | 戦闘中断中の限定分類に必要な端末AI接続と出力契約 |
| `dogido_server/voice_settings.py` · `dogido_server/voice_capture.py` · `dogido_server/echo_input.py` | 音声設定・録音機器・AECの接続 |
| `dogido_server/language_dialogue/{__init__,main_web,chrome_web,google_overview,web_types}.py` | 専用Chrome・MCP SDKの接続と結果形式。会話・検索開始の判断はRust |
| `dogido-rust/scripts/haiku_tokens.py` · `dogido-rust/scripts/tts_tokens_worker.py` | Rustからの辞書token要求を受けるworker。相談・読みの判定・保存はRustが担当 |
| `dogido-rust/scripts/tts_shared_tokens.py` · `dogido-rust/scripts/tts_unidic_adapter.py` | UniDic token取得。`tts_shared_tokens._reader = Unidic()` をworker内で共有し、辞書は初回利用時に初期化 |
| `dogido_server/language_dialogue/source_cards.json` | Rustの国語検索が読む資料カード |
| `dogido_server/llm/companion_prompts.json` · `dogido_server/llm/reaction_situations.json` | 共有プロンプト・状況文の正本データ |
| `dogido-rust/src/server/dogido.html` | Rust本体に埋め込む表示画面 |
| `dogido_server/runtime_settings.py` · `runtime_defaults.json` | RustとPython起動設定で共有する既定値 |

旧 `dogido_server/tts_reading.py` は廃止しました。読みの判定・整形は `src/tts_reading.rs`、辞書tokenの取得だけが上記のPython補助です。`.[tts-reading]` はこの補助に必要な任意のUniDic依存であり、旧Python本体を起動するものではありません。

データ整備用のPython補助は `dev_tools/catalog_tools/` に置きます。`language_knowledge` を含む検索CLI/APIは資料整備専用で、本体の実行時検索はRustの `src/knowledge/` が担当します。`dogido-rust/scripts/` の起動・SDK接続・検証スクリプトも引き続き使用します。

### 検証

```sh
./dogido-rust/cargo.sh test --all-targets --locked
./dogido-rust/cargo.sh clippy --all-targets --locked -- -D warnings
./dogido-rust/cargo.sh build --release --locked
```

さらに変更した領域の `scripts/check_*.py` で模擬通信を確認します。保存済みの移植fixtureは回帰資産として使い、撤去したPython oracleを再導入して再生成しません。実モデル試験は生成内容と条件を別記し、実Minecraft・実マイク・スピーカー・Chromeの確認を自動テストの成功から推定しません。

実機で問題が出た機能の模擬確認は、次の既存スクリプトを入口にします。通常の開発用ビルドを用意し、リポジトリのルートから準備済みPython環境で実行します。これらは模擬モデル・音声を使う検証で、実機の合格とは分けます。

| 対象 | 模擬確認スクリプト（`dogido-rust/scripts/`） |
|---|---|
| 通常会話・音声配送 | `check_dialogue.py` |
| 発句・workshop・局所編集 | `check_haiku_runtime.py` · `check_workshop_runtime.py` · `check_workshop_edits.py` |
| 戦闘中断・復帰 | `check_workshop_combat.py` · `check_workshop_provisional.py` |
| 読み辞書・記憶 | `check_reading_runtime.py` · `check_memory_runtime.py` |
| 録音と音声認識の制御 | `check_voice_runtime.py` |

```sh
./dogido-rust/cargo.sh build --locked
python dogido-rust/scripts/check_workshop_edits.py
```

## 過去の移行記録

移植中のコマンド・比較件数・当時の未接続範囲は [移行時の実装・比較記録](docs/migration-comparison-history.md) に分離しました。現在の確認には上の検証手順と [実機チェック](manual-dialogue-check.md) を使います。機能ごとの変更経緯は [Rust移行記録](../docs/rust-migration-plan.md) を参照してください。
