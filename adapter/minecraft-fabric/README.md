# Dogido Fabric Client

`dogido-server` に `status_snapshot` 系イベントを送る最小 Fabric client mod です。

現時点の対象は、このMacに入っている `Minecraft Java 1.21.11` と `fabric-loader-0.18.4-1.21.11` です。

## できること

- プレイヤー本人の `position / yaw / pitch / health / hunger / held_item / inventory`
- `local_light / sky_visible / biome / time_phase / danger_darkness_score`
- 周辺 hostile の簡易スキャン
- `status_snapshot` の定期送信
- 近距離 hostile 検知時の `threat_approaching` 送信
- 遮蔽された近距離 hostile を `hostile_audio_detected` として送信
- プレイヤー死亡時の `player_died` 送信
- 戦闘収束時の `combat_ended` 送信

## まだやっていないこと

- Minecraft の実サウンド packet 由来の `auditory_threats`
- 高精度の line-of-sight 判定
- エンダーマンやウィッチの個別ロジック
- ベッド/資源候補のワールドスキャン
- `passive_mobs` と `nearby_resources` の本格収集

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
- この mod は `POST /api/v1/adapter-sessions` と `POST /api/v1/game-events` を使う
- JSON の形は親プロジェクトの `docs/event-schema.md` に寄せている
- いまの `hostile_audio_detected` は sound packet ではなく、遮蔽 hostile を使った初期ヒューリスティック
