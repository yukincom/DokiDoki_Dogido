# ドギド Rust版

ドギド本体を段階的に移植するための実装です。接続専用HTTPサーバー、RigのLLM接続試験、Python版との通信比較が動きます。

現在はセッション登録・再接続・終了と表示データの受付を確認する段階です。ゲームの判断・会話・音声は未移植です。進行は[移行計画](../docs/rust-migration-plan.md)を参照してください。

## ビルド

Rustは[rustup](https://rust-lang.org/tools/install/)で用意してください。`rust-toolchain.toml`でRust 1.98.1、`Cargo.lock`で依存を固定しています。Rigは`rig-core 0.42.0`です。

リポジトリのルートから実行します。

```sh
./dogido-rust/cargo.sh build --release --locked
```

`cargo.sh`は通常のRust環境のほか、`DOGIDO_RUST_TOOLCHAIN_DIR`で指定した場所の`cargo/`・`rustup/`を使えます。

## 接続専用サーバー

```sh
dogido-rust/target/release/dogido-rust serve
```

画面は`http://127.0.0.1:5056/dogido`です。終了はCtrl+C。既定は接続テスト用の5056ポートで、`--listen 127.0.0.1:0`なら空きポートを選び、起動時に表示します。この段階ではループバックアドレスにだけバインドできます。

`serve`はLLM・VOICEVOX・記憶storeを構築せず、外部サービスへアクセスしません。`.env`も読みません。Bearer認証が必要なら`DOGIDO_AUTH_TOKEN`を環境変数で指定します。トークンはログへ出しません。

| API | 現在の動作 |
|---|---|
| `/healthz` | サーバー稼働を返す。`dialogue_ready=false`、`llm_enabled=false` |
| `/api/v1/adapter-sessions` | 登録。heartbeat間隔5秒、batch上限25、schema `2026-05-24` |
| `/api/v1/adapter-sessions/{id}/heartbeat` | 接続更新とsequence記録。未知IDは404 |
| `/api/v1/adapter-sessions/{id}` | DELETEで終了。繰り返し終了も成功 |
| `/api/v1/display/snapshot` | 起動元、接続状態、登録／終了等の診断ログ。発言・資料はまだ空 |
| `/api/v1/haiku-workshop/snapshot` | 閉じたworkshopの既存形式。未知IDは404 |
| `/api/v1/game-events`・`/batch` | 古いsession IDは409。既知IDでも世界処理が未対応なので501 |
| `/api/v1/player-input` | 空入力・sessionなしは理由付きで拒否。それ以外も会話が未対応なので501 |
| 音声文脈・音声診断・記憶API | 未実装を501で返す |

501では`accepted=false`を返し、観測sequenceの消費・発話・操作・保存はしません。イベントの受信は接続表示の更新にだけ使います。`game-events`の全フィールド検証・重複判定・入力待ち列は、判断処理の移植時に追加します。

状態変更は一つの処理係へ直列に渡し、表示GETは公開済みsnapshotだけを直接読みます。HTTP側が待機を取り消しても、キューに入った登録／終了の順序と投影の公開は維持します。接続が途絶えた表示も定期更新します。

## LLM単独の接続確認

要求の形式だけを検査します。

```sh
dogido-rust/target/release/dogido-rust check dogido-rust/fixtures/connection.json
```

起動済みのMLXへ、一回だけ生成を依頼します。

```sh
dogido-rust/target/release/dogido-rust generate dogido-rust/fixtures/connection.json
```

既定の接続先は`http://127.0.0.1:8080/v1`、要求モデルはfixture内の`default_model`、タイムアウトは20秒です。接続先は`--base-url`、タイムアウトは`--timeout-ms`で指定できます。モデルの起動や切替は行いません。

このCLIは`.env`を読みません。認証が必要な接続先では`DOGIDO_LLM_API_KEY`を環境変数で設定します。キー未指定時は、RigのAPIに合わせて公開の仮値`dogido-local-no-key`をBearerヘッダーに送ります。

出力JSONには返答、`finish_reason`、入力／生成トークン数、要求／応答のモデル名、応答ID、API所要時間を残します。トークン数が返らなかった場合は`null`です。`elapsed_ms`は一回のAPI呼出と応答読取の時間で、音声の体感応答時間とは別です。

## 検証

```sh
./dogido-rust/cargo.sh test --locked
./dogido-rust/cargo.sh clippy --locked --all-targets -- -D warnings
```

既存Python環境で比較器を実行します。先に通常の開発用ビルドを作ってください。

```sh
./dogido-rust/cargo.sh build --locked
python dogido-rust/scripts/compare_provider.py
```

比較器は同じリポジトリのPythonプロンプト・providerと既存fixtureを使い、一時的なループバックHTTPサーバーへRust版を接続します。送信本文、返答、終了情報、失敗時の呼出回数を比較し、サーバーを終了して`reports/provider-comparison.json`へ結果を保存します。モデル呼出や音声再生はありません。

終了情報を返さない旧Python版では、その項目だけfixtureと照合します。Pythonとも比較できたケースは`python_metadata_comparisons`に記録します。

接続サーバーの実HTTP・再起動・終了確認は、releaseビルド後に実行します。

```sh
python dogido-rust/scripts/check_server.py
```

一時ポートでRustサーバーを起動し、Pythonの型と閉鎖HUDの投影を照合します。再起動後の404／409と再登録、Ctrl+C／SIGTERMでの終了を確認し、結果を`reports/server-check.json`へ保存します。試験プロセスは終了時に回収し、実モデル・TTSは呼びません。

## 接続実装の範囲

- RigのChat Completions経路を明示し、messages・温度・トークン上限・thinking指定を保持します。
- messagesは既存Pythonと同じrole／文字列形式に揃えます。通信の自動再試行とリダイレクトは無効です。
- 生成本文が不完全なJSONでも、`finish_reason=length`と本文・トークン数を上位へ返します。内容の検査・採否・再生成は、今後移植するドギドの処理が担当します。
- 対象は現在のMLXの通常のChat Completions応答です。Rigは`id`・`model`・messageの`role`等を要求するため、旧Pythonが許容する省略形すべての互換実装ではありません。
- 移植済みの型はLLM境界とsession／heartbeat／player-inputの外形です。Fabricイベント全体、設定全体、記憶の保存形式の互換性は、それぞれの移植時に検証します。
