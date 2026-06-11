# Minecraft実況AI "ドキドキドギド"

Minecraft のゲーム状況を読み取り、怖がりな AI キャラクター「ドギド」が実況・警告・雑談を行うための設計メモです。

##　プロジェクトコンセプト
**AIと発見しよう！マイクラ世界の新しい見方！**

ちょっと怖がりなAI相棒「ドギド」は、
Minecraftの冒険中にふいに川柳を詠みます。

でも、ドギドの句はちょっぴり下手くそ。
プレイヤーは「どこが変？」「どう直す？」とツッコミながら、
言葉を観察し、表現を工夫していきます。

自分で詠んだ句も保存できます。
洞窟、朝日、クリーパー、ウーパールーパー。
Minecraftの世界が、そのまま表現の材料になります。

## ドキュメント構成

- [プロジェクト概要](docs/project-overview.md)
- [現行仕様](docs/current-spec.md)
- [イベントスキーマ](docs/event-schema.md)
- [受信 API 仕様](docs/adapter-api.md)
- [サンプルイベントログ収集ケース](docs/sample-event-log-cases.md)
- [デバッグチェックリスト](docs/debug-checklist.md)
- [モンスター定義スキーマ](docs/monster-schema.md)
- [`py_trees` 統合メモ](docs/py-trees-integration.md)
- [実行時依存ライブラリ](docs/runtime-dependencies.md)
- [状態機械](docs/state-machine.md)
- [連携構成](docs/integration-architecture.md)
- [挙動仕様](docs/behavior-spec.md)
- [川柳アーキテクチャ](docs/haiku-architecture.md)
- [技術課題](docs/technical-risks.md)

## 読む順番

1. [プロジェクト概要](docs/project-overview.md)
2. [現行仕様](docs/current-spec.md)
3. [イベントスキーマ](docs/event-schema.md)
4. [受信 API 仕様](docs/adapter-api.md)
5. [サンプルイベントログ収集ケース](docs/sample-event-log-cases.md)
6. [モンスター定義スキーマ](docs/monster-schema.md)
7. [`py_trees` 統合メモ](docs/py-trees-integration.md)
8. [実行時依存ライブラリ](docs/runtime-dependencies.md)
9. [状態機械](docs/state-machine.md)
10. [連携構成](docs/integration-architecture.md)
11. [挙動仕様](docs/behavior-spec.md)
12. [川柳アーキテクチャ](docs/haiku-architecture.md)
13. [記憶アーキテクチャ](docs/memory-architecture.md)
14. [技術課題](docs/technical-risks.md)

## 現時点の方針

- Minecraft 側はイベント取得に専念する
- AI のキャラクター性は LLM に任せきらず、状態管理をコード側で持つ
- 音声基盤より先に、イベント入力と優先制御の仕様を固める
- 川柳の「保存はコードで」「学習・整理・再利用はヘルメスエージェント」
- エージェントは「川柳の先生」ではなく「編集者・司書」

## 実装状況

- `dogido_server/`
  - `FastAPI` の受信 API
  - `event schema` の `Pydantic` モデル
  - `normal / alert / panic / suppressed_panic / aftermath` の状態機械
  - `py_trees` ベースの action policy
  - `aftermath / ambient / death` の LLM leaf
  - `VOICEVOX / say / afplay` の PC 音声バックエンド
  - `fixtures/` を流す replay CLI
- `tests/`
  - state machine と API の最小テスト
- `adapter/minecraft-fabric/`
  - Minecraft Java 1.21.11 / Fabric 用の最小 client adapter
  - `status_snapshot / threat_approaching / hostile_audio_detected / player_died / combat_ended` を `dogido-server` に送る

## 起動メモ

依存を入れる:

```bash
pip install -e .
```

設定ファイルを作る:

```bash
cp .env.example .env
```

サーバー起動:

```bash
python -m dogido_server
```

fixture replay:

```bash
python -m dogido_server.replay fixtures --no-audio
```

初回スモークテスト:

```bash
python -m dogido_server.smoke_test --mode all
```

実際に PC で鳴らす:

```bash
python -m dogido_server.smoke_test --mode all --audio
```

## LLM Routes

低レイテンシの戦況報告は state machine とキャッシュ音声で処理し、LLM は使いません。

- 雑談・助言
  - `chat` route
  - ローカル MLX でもクラウド API でもよい
- 川柳
  - `haiku` route
  - まず軽い route で矛盾候補を抽出し、最後の句だけ `haiku` route のモデルで生成する

`mlx_lm.server` のようなローカル互換サーバーでも、OpenAI / OpenRouter / Claude / Grok / Gemini の API でも、route ごとに切り替えられます。

```bash
mlx_lm.server \
  --model mlx-community/Qwen3.6-35B-A3B-4bit-DWQ \
  --host 127.0.0.1 \
  --port 8080
```

`.env` 側は例えばこうです。

```env
DOGIDO_LLM_BACKEND=chat_completions
DOGIDO_LLM_PROVIDER=local
DOGIDO_LLM_BASE_URL=http://127.0.0.1:8080/v1
DOGIDO_LLM_MODEL=mlx-community/Qwen3.6-35B-A3B-4bit-DWQ
```

OpenAI を使うなら、`base_url` は省略できます。

```env
DOGIDO_LLM_BACKEND=chat_completions
DOGIDO_LLM_PROVIDER=openai
DOGIDO_LLM_MODEL=gpt-4.1-mini
DOGIDO_LLM_API_KEY=...
```

OpenRouter を使うなら、`HTTP-Referer` と `X-Title` も設定できます。

```env
DOGIDO_LLM_BACKEND=chat_completions
DOGIDO_LLM_PROVIDER=openrouter
DOGIDO_LLM_MODEL=openai/gpt-4.1-mini
DOGIDO_LLM_API_KEY=...
DOGIDO_LLM_HTTP_REFERER=https://example.com
DOGIDO_LLM_APPLICATION_NAME=Dogido
```

雑談と川柳を分けるなら、route override を使います。

```env
DOGIDO_LLM_BACKEND=mlx
DOGIDO_LLM_PROVIDER=local
DOGIDO_MLX_MODEL_ID=mlx-community/Qwen3.6-35B-A3B-4bit-DWQ

DOGIDO_LLM_CHAT_PROVIDER=local
DOGIDO_LLM_CHAT_BASE_URL=http://127.0.0.1:8080/v1
DOGIDO_LLM_CHAT_MODEL=mlx-community/Qwen3.6-35B-A3B-4bit-DWQ

DOGIDO_LLM_HAIKU_PROVIDER=openai
DOGIDO_LLM_HAIKU_MODEL=gpt-4.1
DOGIDO_LLM_HAIKU_API_KEY=...
```

`backend` を route ごとに明示しなくても、`provider=claude|grok|gemini|openai|openrouter` のときは API family を自動解決します。

## Minecraft Adapter

Fabric adapter の詳細は [adapter/minecraft-fabric/README.md](adapter/minecraft-fabric/README.md) を参照。

ビルド:

```bash
cd /Users/yukin_co/Documents/DokiDoki-Dogido/adapter/minecraft-fabric
./gradlew build
```

出力 jar:

```text
/Users/yukin_co/Documents/DokiDoki-Dogido/adapter/minecraft-fabric/build/libs/dogido-fabric-client-0.1.0.jar
```
