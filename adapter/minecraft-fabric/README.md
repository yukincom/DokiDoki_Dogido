# Dogido Fabric Client

`dogido-server` に `status_snapshot` 系イベントを送る最小 Fabric client mod です。

現時点の対象は、このMacに入っている `Minecraft Java 1.21.11` と `fabric-loader-0.18.4-1.21.11` です。

## できること

- プレイヤー本人の `position / yaw / pitch / health / hunger / held_item / inventory`
- `player.hotbar`（0〜8、選択中slot、item ID、耐久、main-hand攻撃属性、武器候補種別）
- 乗車中だけ `player.vehicle`（乗り物ID・操縦者か・漕ぐ／走る／移動中）
- `local_light / sky_visible / biome / time_phase / danger_darkness_score`
- **視線先 `look_target`**（画面中央クロスヘアが刺さっているブロック/エンティティ）
- 周辺 hostile の簡易スキャン
- `status_snapshot` の定期送信
- 近距離 hostile 検知時の `threat_approaching` 送信
- 遮蔽された近距離 hostile を `hostile_audio_detected` として送信
- 8ブロック以内にいる未視認・未聴取のゾンビ系だけを、発話用の方向・正確な距離・頭数を含めない `zombie_scent_clues` として送信（スケルトンは対象外）
- プレイヤー死亡時の `player_died` 送信
- 戦闘収束時の `combat_ended` 送信
- game-event応答の型付き `select_hotbar` command受信
- command ID・期限・slot・期待itemをMinecraftメインスレッドで再検証し、選択slotだけを変更
- 実行結果をserverがackするまで後続イベントへ再送
- 新しい音源を短期保持へ追加したとき、Minecraftの `latest.log` に `Dogido sound observed` を出す（hostile / ambientの切り分け用）

## まだやっていないこと

- 高精度の line-of-sight 判定
- エンダーマンやウィッチの個別ロジック
- ベッド/資源候補のワールドスキャン
- `nearby_resources` の本格拡張（現状は原木・板・羊毛・石炭に加え、積雪実測用の雪3種だけ）
- エリトラ滑空など、乗り物ではないプレイヤー活動

## 音まわり（現状）

- Minecraft の sound packet から `auditory_threats` / `ambient_sounds` を載せる
- 敵対中のモブ音は `auditory_threats`、友好モブ音とまだ敵対していない蜘蛛・エンダーマン等の音は `ambient_sounds` へ分ける
- `SoundManager` まで届いた焚き火などのブロック音・環境音・天候音も `ambient_sounds` へ載せる
- クライアント側の音観測 TTL は約 **15秒**（300 tick）。「…？ → 今の音なに？」の猶予用
- サーバの player_chat hearing バッファは別途約 **20秒**

## ゾンビの匂い（遊びの限定観測）

- 実際に索敵した `zombie / zombie_villager / husk / drowned` が8ブロック以内にいる場合だけ候補にする
- 対象へのline-of-sightがある、視認確定保持中、または同じentity IDの音を保持中なら送らない
- `skeleton / wither_skeleton / zombified_piglin` と他の敵は対象外
- payloadは種別・entity ID・粗い距離帯・固定basisだけ。方向、exact position、正確な距離は送らない
- 匂いだけではadapterの戦闘追跡を開始しない。serverが在圏中一度＋全体クールダウンで発話を決める

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
- 観測capabilityと `client.hotbar.select.v1` の実行capabilityは分けてsession登録する
- server再起動でsession IDが失効した場合は `409 unknown_session_id` を受けて自動再登録する。serverだけの再起動でMinecraftを再起動する必要はない
- 支援commandはheartbeatではなくgame-event応答で受ける。任意のMinecraftコマンド文字列は実行しない
- JSON の形は親プロジェクトの `docs/event-schema.md` に寄せている
- `hostile_audio_detected` は、視認されていない近距離の hostile 音観測が更新されたときに送る

## `select_sword` 実機確認

1. `dogido-server` を起動する
2. このディレクトリで `./gradlew test build`
3. `build/libs/dogido-fabric-client-0.1.0.jar` をMinecraftの `mods` へ入れ、同名の旧jarを外す
4. ワールドへ入り、hotbarに弱い剣と強い剣を置く
5. 「剣」または「剣に持ち替えて」と入力・発話し、弱い剣へ切り替わることを確認する
6. 剣を外し、トライデント→斧→弓→道具の順でfallbackすることを確認する
7. 依頼直後に対象slotのitemを動かし、別itemへ勝手に切り替わらないことを確認する
8. 「剣の話をしよう」「剣ある？」「剣に持ち替えないで」では切り替わらないことを確認する

選択だけはクライアント内で完結するため、シングルプレイのLAN公開は不要。
