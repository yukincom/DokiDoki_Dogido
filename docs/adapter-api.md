# Adapter ↔ dogido-server API 仕様

この文書は、Minecraft client adapter から `dogido-server` へイベントを送るための受信 API 仕様です。

イベント payload の中身は [イベントスキーマ](event-schema.md) を参照します。

## 1. 目的

- adapter と server の責務境界を固定する
- 受信 API の最小構成を決める
- ローカル単機運用と将来の拡張の両方に耐える

## 2. 前提

- 初期実装は単一プレイヤー、単一 adapter 前提
- 通信先は原則 `127.0.0.1` のみ
- 観測は adapter から server への送信が基本。限定支援commandだけ既存応答で逆向きに返す
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

adapter → server の主経路:

- `GET /healthz`
- `POST /api/v1/adapter-sessions`
- `POST /api/v1/game-events`
- `POST /api/v1/game-events/batch`
- `POST /api/v1/adapter-sessions/{session_id}/heartbeat`
- `DELETE /api/v1/adapter-sessions/{session_id}`

プレイヤー発話のサイドチャネル（adapter 以外）:

- `POST /api/v1/player-input` … マイク入力・開発時のテキスト注入。詳細は [§21](#21-post-apiv1player-input)

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
    "ambient_sounds",
    "inventory",
    "hotbar_slots",
    "danger_darkness",
    "combat_state",
    "death_events",
    "hostile_outcomes",
    "hostile_defeated_events",
    "creeper_fuse_state",
    "creeper_detonation_events"
  ],
  "execution_capabilities": [
    "client.hotbar.select.v1"
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
- `capabilities` は観測できる情報、`execution_capabilities` は実行できる操作。混ぜない
- sessionなしの暗黙sessionは実行capabilityを持たず、世界操作を返さない

## 8. `POST /api/v1/game-events`

単一イベント受信の主 endpoint。

### headers

- `Content-Type: application/json`
- `X-Dogido-Session-Id: <session_id>` 推奨
- `Idempotency-Key: <opaque-string>` 任意

### request body

[イベントスキーマ](event-schema.md) に準拠する JSON。adapterに未ackの実行結果がある場合は、トップレベルの `command_results` へ添付する。

```json
{
  "command_results": [
    {
      "command_id": "cmd_01JY2ABCXYZ",
      "command_type": "select_hotbar",
      "status": "succeeded",
      "executed_at": "2026-08-15T12:00:01.510+09:00",
      "selected_slot": 2,
      "selected_item_id": "minecraft:stone_sword",
      "detail_code": "selected"
    }
  ]
}
```

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
  "commands": [
    {
      "command_id": "cmd_01JY2ABCXYZ",
      "type": "select_hotbar",
      "slot": 2,
      "expected_item_id": "minecraft:stone_sword",
      "issued_at": "2026-08-15T12:00:00.200+09:00",
      "expires_at": "2026-08-15T12:00:02.200+09:00"
    }
  ],
  "acknowledged_command_ids": [],
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
  "commands": [],
  "acknowledged_command_ids": ["cmd_01JY2ABCXYZ"],
  "server_time": "2026-05-24T15:10:01.221+09:00"
}
```

### response `409`

`X-Dogido-Session-Id` がサーバー再起動などで失効している場合、イベントを暗黙セッションとして処理せず `detail.code=unknown_session_id` を返す。現行Fabric adapterはこれを受けると同じプレイヤー・capabilitiesでセッションを再登録する。Minecraftの再起動は不要。

```json
{
  "detail": {
    "code": "unknown_session_id",
    "session_id": "ses_01JY2ABCXYZ"
  }
}
```

### `select_hotbar` のルール

- commandは閉じた型だけ。任意のMinecraftコマンド文字列は受け付けない
- serverは結果を受け取るか期限が切れるまで、後続のgame-event応答にもpending commandを再提示する
- adapterはcommand IDを重複実行せず、Minecraftメインスレッドで期限・slot 0〜8・player/world・`expected_item_id`を再検証する
- 実行結果はserverがackするまで後続イベントへ再添付する
- `succeeded` は実際の選択slotとitem IDがcommandに一致したときだけ。古いsnapshotなら `rejected / expected_item_mismatch`

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
  "commands": [],
  "acknowledged_command_ids": [],
  "server_time": "2026-05-24T15:10:01.400+09:00"
}
```

### ルール

- `events` は最大 `25`
- 同一 batch 内では `sequence` 昇順を推奨
- 緊急イベントは batch より単送信を優先する
- batch内で発行・再提示されたcommandとackは、トップレベルへcommand ID単位で集約する
- 明示したsession IDが失効している場合は単送信と同じ `409 unknown_session_id` を返す

## 10. `POST /api/v1/adapter-sessions/{session_id}/heartbeat`

adapter は生きているがイベントが発生していない場合の keepalive。

現行Fabric adapterの支援逆チャネルはgame-event応答を使う。heartbeatはcommand配信経路ではない。

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
7. `GET /api/v1/voice-input/context`

## 21. `POST /api/v1/player-input`

adapter 経路ではない。`dogido_server.voice_input`（マイク）や開発時のテキスト注入用。

- 直近のアクティブセッションへ入力を載せる。先頭の `pending_player_text` 1件に加え、直接入力との衝突時は最大8件の内部待ち列を使う（合計9件）
- `source: "voice"` でも短さだけでは棄却しない（`石炭だ`、`雨だ`、`はい`などの短い自然な発話を通す）
- `source: "voice"` では、環境音から生じやすい `Thank` 系の定型誤認識を受け付けない
- `source: "voice"` では、現在のworkshop句・原文材料・時間帯などから作った候補内に限り、音の近いかな断片を会話理解用に補正する。原文は明示操作判定用に保持する
- **次の game-event** の `meta.user_text` としてチャットと同じ経路に合流する
- **セッションが無いと受け付けない**（`accepted: false`, `reason: no_active_session`）
- 先頭が明示知識質問、または開いているworkshopの入力なら、後続入力を先着順に保全する。それ以外の未処理の一般入力は、従来どおり最新入力で置き換える
- 同一本文は `voice` / `text` の経路が異なっても一発話として重複させず、先に受けた `source` を保つ
- 保全対象の待ち列が上限のときは上書きせず拒否する（`accepted: false`, `reason: queue_full`）
- 製品 README のプレイヤー向け手順には載せない（開発・デバッグ用）

相乗り・再キューの挙動は [対話設計](dialogue-design.md) を参照。

### request

```json
{
  "text": "おはようさん",
  "source": "voice"
}
```

`source` は `voice | text`。省略時は `text` で、開発用curlや手入力を音近傍補正しない。`dogido_server.voice_input` は `voice` を送る。

### response 例

```json
{
  "accepted": true,
  "session_id": "…"
}
```

```json
{
  "accepted": false,
  "reason": "no_active_session"
}
```

拒否理由は `empty_text` / `noise_text` / `no_active_session` /
`queue_full` のいずれか。`noise_text` は `source: "voice"` のみで返る。

### 開発時の例

```bash
# アダプタ接続中（セッションあり）のサーバーへ
curl -X POST http://127.0.0.1:5055/api/v1/player-input \
  -H 'Content-Type: application/json' \
  -d '{"text": "おはようさん"}'
```

auth が有効なときは adapter 系と同様に `Authorization: Bearer <token>` を付ける。

## 22. `GET /api/v1/voice-input/context`

別プロセスの `dogido_server.voice_input` が、書き起こし直前に Whisper の文脈を選ぶための内部API。直近のアクティブセッションだけを見て、`prompt_mode` を返す。

```json
{
  "prompt_mode": "haiku_workshop",
  "session_id": "…"
}
```

- workshop が open なら `haiku_workshop`、それ以外とセッションなしは `normal`
- 句本文や材料は返さない
- 取得に失敗した音声入力プロセスは `normal` を使い、書き起こしを止めない
- auth が有効なときは `Authorization: Bearer <token>` が必要

## 23. `POST /api/v1/voice-input/diagnostics`

別プロセスの `dogido_server.voice_input` が、STTの処理段階、認識結果、棄却理由、配送結果をサーバーの診断履歴へ通知する内部API。音声波形は送らない。

```json
{
  "schema_version": 1,
  "event": "stt_rejected",
  "level": "warning",
  "recognized_text": "…",
  "reason": "known_noise_text",
  "detail": "…",
  "prompt_mode": "normal",
  "duration_ms": 740
}
```

- `event` は `capture` / `context` / `stt_result` / `stt_rejected` / `stt_error` / `wake_word_rejected` / `delivery`
- `recognized_text` はWhisperが返した原文。認識できなかった場合は省略する
- `reason` は機械判定に使う短い理由、`detail` は人が調べるための補足
- 診断履歴はプロセス内の上限付き履歴で、会話記憶や評価ログへ読み戻さない
- auth が有効なときは `Authorization: Bearer <token>` が必要

## 24. ゲーム外の発言履歴・診断ログ

Minecraft画面とは別に、ブラウザで `GET /dogido` を開くと、ドギドの発言本文、知識回答の参考資料、診断ログを読める。発言・資料は個別に、発言全文と診断ログはまとめてコピーできる。

画面は `GET /api/v1/display/snapshot` を同一オリジンで定期取得する。任意の `session_id` クエリを付けると、そのセッションだけに絞る。

```json
{
  "schema_version": 1,
  "revision": 2,
  "utterances": [
    {
      "utterance_id": "utt_...",
      "session_id": "ses_...",
      "category": "knowledge",
      "text": "資料を基に整理すると、枕詞は……。",
      "created_at": "ISO-8601",
      "reference_ids": ["ref_..."],
      "output_mode": "audio_and_text"
    }
  ],
  "references": [
    {
      "reference_id": "ref_...",
      "title_ja": "資料名",
      "citation_label_ja": "文部科学省",
      "locator": "該当箇所",
      "url": "https://..."
    }
  ],
  "retention": {
    "storage": "process_memory",
    "max_utterances": 200,
    "cleared_on_restart": true
  },
  "diagnostic_schema_version": 1,
  "diagnostic_revision": 3,
  "diagnostics": [
    {
      "entry_id": "log_3",
      "created_at": "ISO-8601",
      "level": "WARNING",
      "logger": "dogido.voice_input",
      "source": "voice_input",
      "event": "stt_rejected",
      "message": "voice_input event=stt_rejected reason=empty_transcript"
    }
  ],
  "diagnostic_retention": {
    "storage": "process_memory",
    "max_entries": 1000,
    "cleared_on_restart": true
  }
}
```

- 発言として確定した本文の表示であり、音声再生完了の証明ではない。
- 音声が途中で中断されても、本文は画面に残る。
- 発言と参考資料には、プレイヤー入力、Minecraftの観測値、内部プロンプトを含めない。
- 診断ログには音声認識結果と棄却理由を含めるが、音声波形、認証情報、内部プロンプトは含めない。
- 成功した高頻度APIのアクセスログは省略し、同じAPIの400以上の応答と、それ以外の運用ログは残す。
- 会話記憶とは別のプロセス内履歴で、サーバー再起動時に消去する。
- auth が有効なときはsnapshot APIに `Authorization: Bearer <token>` が必要。画面の入力欄はトークンをブラウザへ永続保存しない。
