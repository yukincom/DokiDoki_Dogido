# ドギド Rust版

ドギド本体を段階的に移植するための実装です。現在は、Rigから既存のMLXへ一回生成を依頼するCLIと、Python版との通信比較が動きます。

Minecraft接続・会話の状態管理・音声再生は、これから移植します。進行は[移行計画](../docs/rust-migration-plan.md)を参照してください。

## ビルド

Rustは[rustup](https://rust-lang.org/tools/install/)で用意してください。`rust-toolchain.toml`でRust 1.98.1、`Cargo.lock`で依存を固定しています。Rigは`rig-core 0.42.0`です。

リポジトリのルートから実行します。

```sh
./dogido-rust/cargo.sh build --release --locked
```

`cargo.sh`は通常のRust環境のほか、`DOGIDO_RUST_TOOLCHAIN_DIR`で指定した場所の`cargo/`・`rustup/`を使えます。

## 接続確認

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

## 接続実装の範囲

- RigのChat Completions経路を明示し、messages・温度・トークン上限・thinking指定を保持します。
- messagesは既存Pythonと同じrole／文字列形式に揃えます。通信の自動再試行とリダイレクトは無効です。
- 生成本文が不完全なJSONでも、`finish_reason=length`と本文・トークン数を上位へ返します。内容の検査・採否・再生成は、今後移植するドギドの処理が担当します。
- 対象は現在のMLXの通常のChat Completions応答です。Rigは`id`・`model`・messageの`role`等を要求するため、旧Pythonが許容する省略形すべての互換実装ではありません。
- 現在はLLM境界だけの型です。Fabricイベント、設定全体、記憶の保存形式の互換性は、それぞれの移植時に検証します。
