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
- 実近接源・hotbar・現在バイオーム・温度・天候を競わせ、方向・距離・個数を含めない `smell_observation` 一件として送信
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

## 左下のドギドとワークショップの掛け軸

ドギドは左下・右向き。画面幅の9%、左余白2.5%、下余白4%で、ゆっくり上下する。
原画ファイルは変更せず、表示だけ左右反転する。ドギド一枚はサーバーなしでも表示する。
F1、メニュー、インベントリ、チャット入力中は両方隠れる。

サーバーでworkshopが開くと、右下の掛け軸へ三行を縦書き表示する。
約1秒で現れ、終了時は約1秒で消える。敵の接近・被弾などの危険時は即非表示。
修正案は「未採用案」と明記し、音声・チャットの修正が検証されたら表示にも反映する。
編集相談では左から上・中・下を出し、確認できた対象行を枠と橙色で示す。
選択バーは読み取り専用で、クリックして操作する画面ではない。修正・採否は従来の発話で行う。
書体はMinecraftの標準フォントを使う（ブラウザ試作の明朝体とは異なる）。

クライアント内のチャット欄で以下を入力して閉じると、即時に変更が見える。

- `/dogidohud size 72` — 小さくする（幅24〜256）
- `/dogidohud offset 12 36` — 左端・下端からの余白（GUIピクセル）
- `/dogidohud hide` / `/dogidohud show` — 表示切替
- `/dogidohud reset` — 採用した画面比率の配置へ戻す
- `/dogidohud motion off` / `on` — 上下動の切替
- `/dogidoscroll hide` / `show` — 掛け軸だけの表示切替
- `/dogidoscroll sound off` / `on` — 編集時の短い操作音の切替
- `/dogidoscroll motion off` / `on` — 掛け軸のフェードを即時切替にする

ドギドの設定は `config/dogido-character.properties` に保存する。旧試作設定には新しい画面比率の配置を適用し、`size`／`offset`指定時だけ固定GUIピクセルに切り替える。
掛け軸の表示・音・フェード切替は、このMinecraft起動中だけ有効。
`./gradlew test build` 後にjarを導入し、同じ版のサーバーを起動する。
**サーバー・Java自動テストとビルド済み。実Minecraft表示・音声編集・危険割り込みの一連の確認は未完了。**
通常時の配置案は [`tools/character-placement/index.html`](../../tools/character-placement/index.html)。

## 音まわり（現状）

- Minecraft の sound packet から `auditory_threats` / `ambient_sounds` を載せる
- 敵対中のモブ音は `auditory_threats`、友好モブ音とまだ敵対していない蜘蛛・エンダーマン等の音は `ambient_sounds` へ分ける
- `SoundManager` まで届いた焚き火などのブロック音・環境音・天候音も `ambient_sounds` へ載せる
- クライアント側の音観測 TTL は約 **15秒**（300 tick）。「…？ → 今の音なに？」の猶予用
- サーバの player_chat hearing バッファは別途約 **20秒**

## 匂い（遊びの限定観測）

- 伝播力に必要な範囲だけ調べる指定block、指定itemのhotbar 9slotと落下item、沼バイオーム、未視認・未聴取ゾンビ系だけを候補にする
- 伝播力、3次元距離、Minecraft温度、雨上がり補正をコードで比較し、同種は合算しない
- activeな雨・雪・雷と水中は `suppressed`。候補なしも `none` として明示する
- 火のついたcampfire／soul campfireの実調理slotにある肉・魚だけは寒さによる減衰を受けない
- 結果payloadは方向、正確な距離、個数、entity IDを含めず、匂いだけでadapterの戦闘追跡を開始しない
- 詳しい対象ID・計算・雨上がり・サーバー発話は親プロジェクトの [`docs/smell-policy.md`](../../docs/smell-policy.md)

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
- 掛け軸は `GET /api/v1/haiku-workshop/snapshot?session_id=...` を約400ms間隔で非同期取得する。取得失敗・不正応答・3秒以上の未受信では即非表示にする。別のMinecraft接続ではsessionを作り直し、旧ワールドの句を引き継がない。同じ接続のディメンション移動はsessionを維持する。
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
