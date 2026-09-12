# 共有 MLX プロファイル

別に起動した常駐 MLX サーバーの `http://127.0.0.1:8080/v1` を、雑談と川柳で
共有するための起動プロファイルです。各アプリは OpenAI 互換 Chat Completions endpoint へ
接続するだけです。
通常のプロセス内 MLX は従来どおり `standalone` です。

## 初回設定

```bash
cp .env.shared.example .env.shared
```

`.env.shared` は `.env` の後に読み込まれ、LLM の接続先だけを上書きします。
API キーは不要で、このファイルは Git 対象外です。共有プロファイルでは次を起動時に検査します。

- chat / haiku がともに `chat_completions`、`local`、`http://127.0.0.1:8080/v1`
- request の `model` が必ず `default_model`
- `DOGIDO_PLATFORM_AI_PROVIDER=chat`

検査に失敗した場合は起動を止めます。共有 endpoint の失敗時にプロセス内 MLX をロードする
fallback はなく、ドギド終了時にも 8080 を停止しません。8080 の起動・停止・モデル・並列度・
context・cache は共有 MLX サーバー側で管理します。`GET /v1/models` は現在ロード中のモデル確認には
使いません。

## 起動

```bash
# 共有 endpoint を使う
./scripts/start_dogido.command --profile shared server

# 従来のプロセス内 MLX を明示して使う
./scripts/start_dogido.command --profile standalone server

# 起動せず設定元を確認する
./scripts/start_dogido.command --profile standalone --dry-run
```

引数を省略した既存コマンドも `standalone` のままです。共有 endpoint 自体はこのスクリプトで
起動しません。既存の LLM timeout、生成上限、メッセージ、temperature、JSON 検証、
VOICEVOX / Whisper / Fabric の設定も変更しません。
