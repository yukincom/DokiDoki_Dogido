# Adapter -> dogido-server 受信 API 仕様

この文書は、Minecraft client adapter から `dogido-server` へイベントを送るための受信 API 仕様です。

イベント payload の中身は [イベントスキーマ](event-schema.md) を参照します。

## 1. 目的

- adapter と server の責務境界を固定する
- 受信 API の最小構成を決める
- ローカル単機運用と将来の拡張の両方に耐える

## 2. 前提

- 初期実装は単一プレイヤー、単一 adapter 前提
- 通信先は原則 `127.0.0.1` のみ
- イベントは adapter から server への一方向送信が基本
- 音声出力は `dogido-server` が直接処理し、adapter へ返さない

## 3. 基本方針

- 最初は HTTP API を正とする
- 低遅延が必要なら後で WebSocket を追加する
- 1 イベント 1 リクエストを基本とする
- 高頻度更新が必要なら batch endpoint を使う

## 4. バインドとセキュリティ

### デフォルト

- bind address: `127.0.0.1`
- CORS: disabled
- TLS: なし
- auth: なし

### 非ローカル運用時

- 明示設定があるときだけ `0.0.0.0` bind を許可
- その場合は `Authorization: Bearer <token>` を必須にする
- 外部公開は推奨しない

### リクエスト制限

- body 上限: `256 KB`
- adapter 1 セッションあたりの受信レート目安: `20 req/s` 以内

## 5. API 一覧

- `GET /healthz`
- `POST /api/v1/adapter-sessions`
- `POST /api/v1/game-events`
- `POST /api/v1/game-events/batch`
- `POST /api/v1/adapter-sessions/{session_id}/heartbeat`
- `DELETE /api/v1/adapter-sessions/{session_id}`

## 6. `GET /healthz`

起動確認用。

### response `200`

```json
{
  "ok": true,
  "service": "dogido-server",
  "version": "0.1.0"
}
```

## 7. `POST /api/v1/adapter-sessions`

adapter の起動時に session を作る。

### request

```json
{
  "adapter_name": "dogido-fabric-client",
  "adapter_version": "0.1.0",
  "game": "minecraft-java",
  "schema_version": "2026-05-24",
  "player_name": "main_player",
  "profile_name": "default",
  "capabilities": [
    "visual_threats",
    "auditory_threats",
    "inventory",
    "danger_darkness"
  ]
}
```

### response `201`

```json
{
  "session_id": "ses_01JY2ABCXYZ",
  "accepted_schema_version": "2026-05-24",
  "server_time": "2026-05-24T15:10:00.000+09:00",
  "event_endpoint": "/api/v1/game-events",
  "batch_endpoint": "/api/v1/game-events/batch",
  "heartbeat_interval_ms": 5000,
  "max_batch_size": 25
}
```

### ルール

- `session_id` は server 発行
- 以後のイベント送信では `X-Dogido-Session-Id` ヘッダで送る
- session を作らずにイベント送信してもよいが、初期実装でも session ありを推奨する

## 8. `POST /api/v1/game-events`

単一イベント受信の主 endpoint。

### headers

- `Content-Type: application/json`
- `X-Dogido-Session-Id: <session_id>` 推奨
- `Idempotency-Key: <opaque-string>` 任意

### request body

[イベントスキーマ](event-schema.md) に準拠する JSON。

### response `202`

```json
{
  "accepted": true,
  "event_id": "evt_01JY2ABCXYZ",
  "session_id": "ses_01JY2ABCXYZ",
  "sequence": 1842,
  "deduplicated": false,
  "state": {
    "mode": "panic",
    "combat_active": true
  },
  "outputs": {
    "panic_cue_enqueued": true,
    "callout_enqueued": true,
    "speech_enqueued": false
  },
  "server_time": "2026-05-24T15:10:01.221+09:00"
}
```

### response `200`

重複イベントを受理したが再処理しなかった場合。

```json
{
  "accepted": true,
  "event_id": "evt_01JY2ABCXYZ",
  "session_id": "ses_01JY2ABCXYZ",
  "sequence": 1842,
  "deduplicated": true,
  "server_time": "2026-05-24T15:10:01.221+09:00"
}
```

## 9. `POST /api/v1/game-events/batch`

複数イベントをまとめて送る endpoint。

### request

```json
{
  "events": [
    {
      "schema_version": "2026-05-24",
      "game": "minecraft-java",
      "adapter": "dogido-fabric-client",
      "observed_at": "2026-05-24T15:10:01.000+09:00",
      "sequence": 1843,
      "event": {
        "name": "status_snapshot",
        "source_kind": "system",
        "priority_hint": "background",
        "certainty": "high"
      },
      "player": {},
      "world": {}
    }
  ]
}
```

### response `202`

```json
{
  "accepted": true,
  "received": 1,
  "processed": 1,
  "deduplicated": 0,
  "server_time": "2026-05-24T15:10:01.400+09:00"
}
```

### ルール

- `events` は最大 `25`
- 同一 batch 内では `sequence` 昇順を推奨
- 緊急イベントは batch より単送信を優先する

## 10. `POST /api/v1/adapter-sessions/{session_id}/heartbeat`

adapter は生きているがイベントが発生していない場合の keepalive。

### request

```json
{
  "last_sequence": 1843,
  "sent_at": "2026-05-24T15:10:05.000+09:00"
}
```

### response `200`

```json
{
  "ok": true,
  "session_id": "ses_01JY2ABCXYZ",
  "server_time": "2026-05-24T15:10:05.010+09:00"
}
```

## 11. `DELETE /api/v1/adapter-sessions/{session_id}`

adapter 終了時に session を閉じる。

### response `200`

```json
{
  "ok": true,
  "session_id": "ses_01JY2ABCXYZ"
}
```

## 12. バリデーションルール

### 共通

- JSON parse 可能であること
- `schema_version` が対応範囲内であること
- `game` は現時点では `minecraft-java`
- `observed_at` は ISO 8601
- `event.name` は許可済み enum に含まれること

### `sequence`

- 非負整数
- session 単位で単調増加を推奨
- 同じ `sequence` は重複として扱ってよい

### `observed_at`

- server 時刻との差が極端に大きい場合は warning 扱い
- 未来時刻すぎる場合は reject してよい

## 13. 順序と重複

### 重複判定

以下のいずれかで dedupe してよい。

- `session_id + sequence`
- `Idempotency-Key`

### out-of-order

- 少し古いイベントは受理してもよい
- ただし現在 state を巻き戻さない
- `observed_at` が古すぎる場合は `accepted=true, deduplicated=true` として捨ててもよい

## 14. 再送ポリシー

adapter 側は以下を実装する。

- timeout: `500 ms` 〜 `1000 ms`
- `5xx` または network error のときだけ再送
- `4xx` は基本再送しない
- 再送は指数バックオフ
- 緊急イベントは最大 `2` 〜 `3` 回まで

## 15. ステータスコード

- `200 OK`
  - heartbeat 正常
  - delete 正常
  - 重複イベント受理
- `201 Created`
  - session 作成成功
- `202 Accepted`
  - イベント受理
- `400 Bad Request`
  - 不正 JSON
- `401 Unauthorized`
  - token 不正
- `404 Not Found`
  - 不明 session
- `409 Conflict`
  - session 状態不整合
- `413 Payload Too Large`
  - body 超過
- `422 Unprocessable Entity`
  - schema は JSON だが内容不正
- `429 Too Many Requests`
  - 受信過多
- `503 Service Unavailable`
  - server 過負荷または停止中

## 16. エラー応答

```json
{
  "accepted": false,
  "error": {
    "code": "invalid_schema",
    "message": "event.name is required",
    "details": {
      "field": "event.name"
    }
  },
  "server_time": "2026-05-24T15:10:01.221+09:00"
}
```

## 17. 推奨送信戦略

### 高優先度

即時単送信。

- `threat_approaching`
- `player_died`
- `hostile_audio_detected`

### 中優先度

単送信または短時間 debounce。

- （レガシー）`danger_darkness_changed` / `resource_option_found` / `time_phase_changed`
  - 現行 adapter はこれらを主経路にせず、`status_snapshot` 同梱フィールドで代替する

### 低優先度

batch 可。

- `status_snapshot`（暗所スコア・inventory の本流もここ）
- `ambient_mob_detected`

## 18. `status_snapshot`

定期的な状態同期用イベント。

### 用途

- セッション生存中の平常状態更新
- inventory や**暗所判定の本流入力**（`danger_darkness_score` 等）
- UI やデバッグ用途

### 暗所について

初期は `danger_darkness_changed` 専用イベント案もあったが、挙動が粗く、server 側で多段リアクション（`dark_push` / shelter 等）に寄せた。  
暗所は snapshot の連続スコアを正とする。詳細は [現行仕様 §6](current-spec.md)。

### 推奨頻度

- `500 ms` 〜 `1000 ms`

### event 例

```json
{
  "name": "status_snapshot",
  "source_kind": "system",
  "priority_hint": "background",
  "certainty": "high"
}
```

## 19. 受信 API と内部処理の境界

受信 API は以下までを責務とする。

- 認証
- session 解決
- schema validation
- dedupe
- queue への投入
- 軽量な応答生成

以下は内部処理の責務。

- state machine 更新
- 発話優先度判定
- LLM 呼び出し
- TTS / cue 再生（現行は PC 音声）
- （将来）M5Stack Push への再生命令

## 20. 実装優先度

1. `GET /healthz`
2. `POST /api/v1/game-events`
3. `POST /api/v1/adapter-sessions`
4. `DELETE /api/v1/adapter-sessions/{session_id}`
5. `POST /api/v1/game-events/batch`
6. `POST /api/v1/adapter-sessions/{session_id}/heartbeat`
