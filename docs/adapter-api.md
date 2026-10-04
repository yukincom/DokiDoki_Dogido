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
    "smell_observation",
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

- `status_snapshot`
  - 暗所スコア・周辺資源・時間帯は同梱フィールドで継続更新する

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

Rustの `voice-input`（マイク）と会話画面・開発時のテキスト入力が使うAPI。以下は現行本体 `serve-dialogue` の契約です。入力を受けた時点で現在観測へ照合し、返答生成や分類を非同期jobへ渡します。次のgame-eventへの相乗りは行いません。

### request

```json
{
  "session_id": "session_…",
  "text": "おはようさん",
  "source": "voice"
}
```

| フィールド | 契約 |
|---|---|
| `session_id` | 任意。省略または `null` の場合は接続中sessionがちょうど一つのときだけ自動選択する。ゼロ件・複数件なら `select_one_session` |
| `text` | 必須の文字列。空白だけは `empty_text`、空白を含むUnicode文字数が1000を超えれば `text_too_long` |
| `source` | `voice` または `text`。省略時は `text`。手入力を音近傍補正しないため、マイクからの配送だけ `voice` を指定する |

指定した `session_id` が存在しなければ `unknown_session_id`。通常のゲーム接続では直近観測の受信から10秒以内かつ `observed_at` が現在時刻の前後10秒以内であることを検査し、古い観測のまま通常会話を開始しません。

`source: "voice"` でも短さだけでは棄却しません。workshopの現在句・保存済み材料を候補とした音近傍補正は会話理解用に限り、原文は明示操作の検証用に保持します。`Thank` 系などの定型誤認識はRust音声クライアントが配送前に除外します。このHTTP APIは `noise_text` を返しません。

### 受付・重複・保留

- `accepted: true` は受付または既存入力への照合が成立したことを表す。生成・採用・保存・実再生の完了ではない。返された `turn_id` と会話表示の `playback_status` で後続結果を確認する。
- 通常会話は受付時に `generating` の行を作り、jobを開始する。新しい別入力は先行する通常会話の生成・再生を取り消すため、全入力を先着順に蓄える共通FIFOではない。
- 受付検査を通り、処理中または保留中の同一本文に一致した場合は、`voice` / `text` をまたいでも `deduplicated: true` で既存入力を返す。これは処理中・保留中の重複防止であり、完了後の同文を永久に拒むものではない。
- Dialogue全体の未完了jobが16件以上なら `input_queue_full`。これは未完了の処理数の上限であり、sessionごとの入力16件を保存する待ち列ではない。
- 危険中・警告中などに届く入力は知識質問かを別jobで分類できる。受付時は `queued: true`・`reason: "knowledge_input_routing"`、表示は `routing`。明示知識質問と確定すると `waiting_for_safety` となり、安全な観測と先行処理の終了後に同じ `turn_id` で先着順に再開する。
- この知識保留枠はsessionごとに最大9件（再開のため取り出した一件を含む）。満杯なら `knowledge_queue_full` とし、古い質問を落とさない。一般のjob上限16件とは別に検査する。
- 戦闘の限定質問、剣支援、句の戦闘中断中入力は各専用経路が採否を決める。通常会話を始められない場合は `fresh_safe_snapshot_required`。安全な観測があり緊急環境音声だけを待つ場合は一件保留でき、`reason: "after_environment_warning"` を返す。
- 叫声だけの `voice` 入力は `reason: "situation_vocalization"` として受け付け、生成中の会話等を中断し状況記録へ渡す。この経路はjob上限と観測鮮度の検査を迂回するが、通常会話・句の操作には流さない。

経路によって `turn_id` が未発行または `null` の受付もあります。`queued`・`deduplicated`・`reason` は該当時だけ返り、すべての成功応答に同じ項目が揃うとは限りません。

### response 例（HTTP 200）

通常会話の受付:

```json
{
  "accepted": true,
  "session_id": "session_…",
  "turn_id": "turn_…"
}
```

危険中の知識質問の振り分け受付:

```json
{
  "accepted": true,
  "queued": true,
  "session_id": "session_…",
  "turn_id": "knowledge_…",
  "reason": "knowledge_input_routing"
}
```

受付拒否:

```json
{
  "accepted": false,
  "reason": "select_one_session"
}
```

| 主な `accepted: false` の理由 | 意味 |
|---|---|
| `empty_text` / `text_too_long` | 本文が空白だけ、または1000文字超 |
| `select_one_session` | session未指定で、一件に決められない |
| `unknown_session_id` | 指定sessionが存在しない |
| `input_queue_full` | 未完了jobが16件以上 |
| `knowledge_queue_full` | 対象sessionの知識保留枠が満杯 |
| `fresh_safe_snapshot_required` | 観測が古い、または現在の危険・警告等によりその入力を開始できない |
| `server_stopping` | 本体が停止処理中 |
| `chat_context_unavailable` | 現在観測から会話用snapshotを構築できない |

保留入力の再開・宛先確認では、入力の世代が変わった `superseded_input` / `superseded_address_input`、保留元が表示台帳から失効した `held_turn_expired` もあり得ます。これらを成功や自動再送として扱いません。

型不正・必須項目欠落・不正な `source` 等はHTTP 422の `detail.code: "invalid_request"`、要求サイズ超過は413、認証失敗は401です。HTTP受付workerの停止時は503の `detail: "server_stopping"` になることがあります。呼出側はHTTP状態と本文の `accepted` の両方を確認します。

### 開発時の例

```bash
# 標準設定のRust本体へ。Fabricが接続し、新しい観測を送っている状態で使用
curl -X POST http://127.0.0.1:5055/api/v1/player-input \
  -H 'Content-Type: application/json' \
  -d '{"text": "おはようさん", "source": "text"}'
```

複数sessionがある場合は本文に `session_id` を指定します。authが有効なときは `Authorization: Bearer <token>` を付けます。接続先ポートは実際の起動設定に合わせてください。

現在の受付の実装は `dogido-rust/src/server/contracts.rs`・`server/runtime.rs`、採否とjob開始は `dialogue/mod.rs`、知識保留は `dialogue/knowledge_queue.rs` です。接続診断だけの `serve` は会話を接続しないため、この本体契約の確認には使いません。

## 22. `GET /api/v1/voice-input/context`

別プロセスのRust `voice-input` が、書き起こし直前に Whisper の文脈を選ぶための内部API。単一のアクティブセッションと観測の鮮度を確認して、`prompt_mode` を返す。

```json
{
  "prompt_mode": "haiku_workshop",
  "session_id": "…"
}
```

- 単一セッション・新鮮な観測・workshop open・戦闘pause外で、そのworkshopが入力受付可能なときだけ `haiku_workshop`。それ以外、セッションなし、複数セッションでは `normal`
- 句本文や材料は返さない
- 取得に失敗した音声入力プロセスは `normal` を使い、書き起こしを止めない
- auth が有効なときは `Authorization: Bearer <token>` が必要

## 23. `POST /api/v1/voice-input/diagnostics`

別プロセスのRust `voice-input` が、STTの処理段階、認識結果、棄却理由、配送結果をサーバーの診断履歴へ通知する内部API。音声波形は送らない。

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

## 25. ゲーム内の川柳掛け軸

`GET /api/v1/haiku-workshop/snapshot?session_id=ses_...` は、指定sessionのworkshopを読み取り専用で返す。
既存Bearer認証を使用し、未知／削除済みsessionは404、成功時は`Cache-Control: no-store`。
通常の発言履歴とは別で、LLM・世界操作・保存・採用・pause判定をGETから実行しない。

```json
{
  "schema_version": 1,
  "session_id": "ses_...",
  "revision": 3,
  "observed_sequence": 42,
  "workshop_id": "workshop_...",
  "state": "open",
  "canonical_lines": ["くわをもち", "はたけのまえで", "ひとやすみ"],
  "pending_lines": [],
  "editing": true,
  "selected_line": 1,
  "provisional_resume": false,
  "character_state": "normal"
}
```

- `state`: `closed / open / danger`。閉鎖時の句配列は空。`workshop_id`はpin単位で安定し、戦闘中断・再開で変わらない。
- `character_state`: `normal / thinking`（schema v1の追加フィールド。旧serverで欠ける場合はnormal）。新規川柳の取り合わせ／本句と、workshopの修正案を実際に生成している区間だけthinking。workshop open、preface待ち、音声待ち・読み上げ、雑談生成だけではthinkingにしない。生成の入口とfinallyでservice workerから即投影し、返り値を発話queueへ渡す前にnormalへ戻す。例外・生成取消もfinallyで解除。GETは生成worker待ちにならない。
- `canonical_lines`は採用済みの表示三行。CASが現在句に合う未採用案だけを`pending_lines`に別記し、描画時は「未採用案」と明記する。不整合案をGETから破棄しない。
- `editing / selected_line`は表示専用。選択行は0始まりの0/1/2またはnull。確定した編集対象か、編集相談の検証済み行参照を映す。単なる意味質問の行マークだけでは編集中にしない。操作ボタンや保存権限ではない。
- service専用workerの操作完了時に投影を作り、短いlockで置換する。GETは独立cacheだけを読み、LLM処理待ちのキューへ入れない。編集対象確定時にも投影し、生成待ちの間に対象を出せる。投影エラーは本体処理へ伝播させず、cacheを無効化する。
- Fabricは同時一件・約400ms間隔で取得し、session・schema・revision・行数／外形を検証する。描画はゲームスレッド。欠損／失敗／3秒以上の未受信は即非表示。新しいMinecraft接続ではsessionを再登録し、古い応答を捨てる。
- キャラクター本体は欠損／失敗／3秒以上の未受信でも隠さず、通常顔＋瞬きへ戻る。ローカル危険・死亡・ワールド変更でも考え顔を解除し、古いrevision／危険観測より前のsnapshotで復帰しない。音声とは別の約400msポーリング表示なので、画面切替と実音声開始のフレーム単位の同期は保証しない。
- ローカルの敵／被弾／死亡観測でも即遮蔽し、`observed_sequence`が新しい危険観測まで追いつく前の応答では復帰させない。`provisional_resume`は既存の明示・低脅威再開がコードで成立したときだけtrue。再接近・被弾・敵変更はその許可を再度遮蔽する。匂い単独では遮蔽しない。
- 句の出現／通常終了は各1秒、危険は即時。編集中の対象切替はフェードせず、一度だけ短い操作音を出す。初回接続・同じsnapshotの再受信では音を出さない。TTSの実再生完了との厳密な同期ではない。

## 26. あんちょこと読み訂正

Rust本体の `/catalog` は、カタログの説明・読みを表示し、フォームから読みを訂正する画面。
通常の会話・音声・旧 `読み:` 入力からは登録しない。現在句や未採用案を編集せず、モデル生成・音声再生も開始しない。
以下のAPIは既存Bearer認証を使用し、ゲームsessionの指定は不要。

| API | 内容 |
|---|---|
| `GET /api/v1/catalog` | `enabled`（記憶保存の有効・無効）、`entries`（カタログ一覧）、`corrections`（現在有効な訂正）を返す |
| `PUT /api/v1/catalog/readings` | 明示した読みを検証・保存し、`saved: true` と保存した `correction` を返す |
| `DELETE /api/v1/catalog/readings` | 表記と現在の登録IDを照合して取消を追記し、`removed: true` を返す |

PUTの新規登録例:

```json
{
  "surface": "草地",
  "reading": "くさち",
  "entry_id": "biome:meadow",
  "wrong_reading": "そうち",
  "expected_id": null
}
```

- `surface`: 前後空白・制御文字のない1〜80文字。`reading`: 1〜80文字のひらがな・長音符。
- `entry_id`: 任意。指定した場合はカタログIDと表記の一致を検査する。
- `wrong_reading`: 任意。指定する場合は正しい読みとは異なる1〜80文字のひらがな・長音符。
- `expected_id`: 新規登録ではnull。既存訂正の編集では、GETで読んだ現在の訂正IDを渡す。
- DELETEは `surface` と `expected_id` を渡す。別の更新が先行したときは409 `reading_changed` とし、上書きしない。

表記・読み・カタログ照合の不正は422、記憶保存が無効なら409 `memory_disabled`、停止中は503 `server_stopping`、保存処理の失敗は500 `reading_storage_failed`。成功応答だけを保存成功として扱う。

保存先は設定した記憶ルートの `long_term/catalog_corrections.jsonl`。元のカタログを変更せず、登録・取消を追記し、次の処理から有効な訂正を読み込む。実機での確認は [あんちょこの手順](../dogido-rust/manual-dialogue-check.md#あんちょこでの読み訂正) を参照。
