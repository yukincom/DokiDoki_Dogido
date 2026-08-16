# Dogido Fabric Client

`dogido-server` に `status_snapshot` 系イベントを送る最小 Fabric client mod です。

現時点の対象は、このMacに入っている `Minecraft Java 1.21.11` と `fabric-loader-0.18.4-1.21.11` です。

## できること

- プレイヤー本人の `position / yaw / pitch / health / hunger / held_item / inventory`
- 乗車中だけ `player.vehicle`（乗り物ID・操縦者か・漕ぐ／走る／移動中）
- `local_light / sky_visible / biome / time_phase / danger_darkness_score`
- **視線先 `look_target`**（画面中央クロスヘアが刺さっているブロック/エンティティ）
- 周辺 hostile の簡易スキャン
- `status_snapshot` の定期送信
- 近距離 hostile 検知時の `threat_approaching` 送信
- 遮蔽された近距離 hostile を `hostile_audio_detected` として送信
- プレイヤー死亡時の `player_died` 送信
- 戦闘収束時の `combat_ended` 送信
- 直前のドギド応答を、`↑`＝良い例・`↓`＝要レビューとして私的候補箱へ記録

## 学習候補の評価キー

- `↑`：直前の応答を `👍 good_example` として候補化
- `↓`：直前の応答を `👎 needs_review` として候補化
- 同じ応答で反対側を押すと評価を上書きする。履歴は消さず、エクスポート時に最後の評価を正とする
- チャット、コマンド、看板、本、インベントリなど、**画面を開いている間は両キーとも無効**
- 評価できる応答は既定で直近3分以内。通常のプレイログを常時学習データとして保存する機能ではない
- キー割り当てはMinecraftの「設定 → キー設定 → ドギド：学習候補の評価」で変更できる

保存先はサーバー側の `.dogido_training/inbox/evaluation_flags.jsonl`。Git対象外で、人間レビュー前には学習へ投入しない。

## まだやっていないこと

- 高精度の line-of-sight 判定
- エンダーマンやウィッチの個別ロジック
- ベッド/資源候補のワールドスキャン
- `nearby_resources` の本格拡張（現状は原木・板・羊毛・石炭に加え、積雪実測用の雪3種だけ）
- エリトラ滑空など、乗り物ではないプレイヤー活動

## 音まわり（現状）

- Minecraft の sound packet から `auditory_threats` / `ambient_sounds` を載せる
- クライアント側の音観測 TTL は約 **15秒**（300 tick）。「…？ → 今の音なに？」の猶予用
- サーバの player_chat hearing バッファは別途約 **20秒**

## 設定ファイル

初回起動後に `config/dogido-fabric-client.properties` を作ります。

主な設定:

- `server_base_url=http://127.0.0.1:5055`
- `snapshot_interval_ticks=20`
- `threat_scan_interval_ticks=4`
- `audio_scan_interval_ticks=8`
- `combat_ended_quiet_ticks=100`
- `max_threat_distance=16.0`
- `audio_threat_distance=12.0`
- `panic_distance=7.0`
- `rear_warning_distance=8.0`

## 開発メモ

- `dogido-server` を先に起動する
- この mod は `POST /api/v1/adapter-sessions`、`POST /api/v1/game-events`、`POST /api/v1/training-feedback` を使う
- JSON の形は親プロジェクトの `docs/event-schema.md` に寄せている
- いまの `hostile_audio_detected` は sound packet ではなく、遮蔽 hostile を使った初期ヒューリスティック
