# 共有LLMの運用プロファイル（GGUF・キャッシュ）

2026-11-02確認。通常会話、画像付き会話、環境反応、川柳は、常駐する llama.cpp の `llama-server` へ Chat Completions で接続します。接続先は `http://127.0.0.1:8080/v1`、要求のモデル名は `default_model` です。実モデルは Gemma 4 26B-A4B のGGUF量子化版（QAT UD-Q4_K_XL）です。

| 用途 | 現行のファイル |
|---|---|
| 生成モデル | `gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf` |
| 画像入力用プロジェクター | `mmproj-F16.gguf` |
| MTPのドラフトモデル | `mtp-gemma-4-26B-A4B-it.gguf` |

**モデルファイルのキャッシュ**は `~/.cache/gguf/models/gemma4-26b-qat-ud-q4-k-xl/` です。上の3ファイルをここからロードします。管理アプリの名称は `MLX 8080.app` で、GGUFの起動設定は `~/mlx-server/gguf/profiles.json` にあります。過去のMLX版で測った値は当時の比較記録です。

**プロンプトキャッシュ**は `llama-server` 側で有効です。以前の要求と一致する先頭トークンのKVキャッシュを再利用できます。固定の指示を先に、変わる観測・対話履歴・今回の入力を後に組み立てます。速度を比べるときは入力全体の長さと再利用分を分け、取得できる場合は `timings.cache_n`（再利用トークン数）と `timings.prompt_ms`（今回の入力処理時間）を記録します。実際の会話履歴・画像は各要求に合わせて用意します。

## ドギドの設定プロファイル

`standalone` と `shared` はどちらもRust本体を起動します。`standalone` は `.env`、`shared` は `.env` の後で `.env.shared` を読みます。

## 初回設定

```bash
cp .env.shared.example .env.shared
```

`.env.shared` は `.env` の後に読み込まれ、LLM の接続先だけを上書きします。
API キーは不要で、このファイルは Git 対象外です。共有プロファイルでは次を起動時に検査します。

- chat / haiku がともに `chat_completions`、`http://127.0.0.1:8080/v1`
- request の `model` が必ず `default_model`

検査に失敗した場合はドギドの起動を止めます。モデルの起動・停止・並列度・コンテキスト長とキャッシュは共有サーバーが管理します。稼働構成を再確認するときは、`GET /v1/models` の形式（`gguf`）と提供元（`llamacpp`）、`llama-server` の起動引数またはロードログにあるモデルファイル名を照合します。`default_model` はAPI上の別名です。

OS AIの接続とprovider設定は撤去済みです。意図分類は共通BERT、戦闘中断後の再開・継続はRustの警戒解除判定を使います。[現行仕様](2026-10-23-modernbert-23class-integration.md)。

## 起動

```bash
# 共有 endpoint を使う
./scripts/start_dogido.command --profile shared server

# .env の接続設定でRust本体を起動する
./scripts/start_dogido.command --profile standalone server

# 起動せず設定元を確認する
./scripts/start_dogido.command --profile standalone --dry-run
```

引数を省略した既存コマンドも `.env` を使う `standalone` のままです。Rust本体は設定されたローカルAPIへ接続します。共有 endpoint 自体はこのスクリプトで
起動しません。既存の LLM timeout、生成上限、メッセージ、temperature、JSON 検証、
VOICEVOX / Whisper / Fabric の設定も変更しません。
