# Dogido Fabric Client

`dogido-server` に `status_snapshot` 系イベントを送る最小 Fabric client mod です。

向きの保持・静止画像の更新・本人を見る選択・雨の観測・縦書き「ー」の変更は[2026-10-25の記録](../../docs/2026-10-25-body-orientation-scene.md)を参照。
現時点の対象は、このMacに入っている `Minecraft Java 1.21.11` と `fabric-loader-0.18.4-1.21.11` です。

## できること

- 既存の会話snapshotから実再生中の対象を受け、現在観測の座標へ身体を向ける（[発話と視線](../../docs/2026-10-18-body-speech-gaze.md)）。
- 任意の身体Mod／Body API v12から、自他姿勢・owner視認と身体基準の周辺感覚を`body_observation` v3で送信。取得不能時は理由を送り、playerの感覚で補完しない（[観測移行仕様](../../docs/2026-10-13-body-observation-origin.md)）。BERTの明示呼び寄せ・注視は`body.request.v1`で受け、実完了だけを結果へ戻す。相対8方向は身体のyaw、同じ方向はplayerの視線、対象注視は現在crosshairの命中点を使い、空や遠景など命中先がなければplayerの視線方向を使う。向きと移動は並行し、明示撮影は停止・位置差・角度差を条件にしない。FOLLOWの制御状態・候補は`body_autonomy` v2、観察移動・その場の本人注視は`body.inspect_target.v3`を使う（[移動接続](../../docs/2026-10-16-body-motion-start.md)・[安心度と発話・行動の統合](../../docs/2026-10-20-reassurance.md)）。
- 現在の `player.block_breaking_active` を通常観測と身体の立ち位置判断で共有する（[呼び寄せ仕様](../../docs/2026-10-16-body-recall.md)）。
- プレイヤー本人の `position / yaw / pitch / health / hunger / held_item / inventory`
- `player.hotbar`（0〜8、選択中slot、item ID、耐久、main-hand攻撃属性、武器候補種別）
- 乗車中だけ `player.vehicle`（乗り物ID・操縦者か・漕ぐ／走る／移動中）
- `local_light / sky_visible / biome / time_phase / danger_darkness_score`
- **視線先 `look_target`**（画面中央クロスヘアが刺さっているブロック/エンティティ）
- 周辺 hostile の簡易スキャン
- `status_snapshot` の定期送信
- 近距離 hostile 検知時の `threat_approaching` 送信
- 遮蔽された近距離 hostile を `hostile_audio_detected` として送信
- 実近接源・hotbar・現在バイオーム・温度・天候から各匂いの種類と強度を解決し、`smell_observation.components` として送信
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

## 身体視点の撮影診断

`/dogido-view prepare`で10分の待機を開始し、受信側の確認後に`/dogido-view capture`で一枚撮ります。身体視点・同frameのプレイヤー枠・192pxへの縮小を試す独立診断です。[準備と画像確認](../../dev_tools/body_view_probe/README.md)。通常会話は`client.body_view.capture.v1`と同じ撮影処理を使い、有効設定時に自動で192px画像を添えます。手動prepareやPythonの監視は不要です。[会話画像の仕様](../../docs/2026-10-21-chat-image.md)。

## チャットでドギドと話す

- `/dogido <文章>` — ドギドだけへ送る。通常チャットと他のコマンドは転送しない。
- `/dogido voice off` — 文字のみ（音声入力・読み上げを停止）。
- `/dogido voice on` — 文字＋音声。音声を使うときは音声入力プロセスも起動する。
- `/dogido` — 現在の状態と使い方。

返答は `[ドギド]`、音声入力の認識結果は `[聞き取り]` としてチャットへ表示する。認識ミスは `/dogido` から訂正を伝えられる。音声の選択はサーバー受理後に既存の `config/dogido-fabric-client.properties` へ保存する。入力は1000文字までを検査し、無言で切り詰めない（Minecraft標準入力欄自体の上限は別）。通信失敗時に入力を自動再送しない。

**2026-10-09時点の記録:** テキスト会話の関連自動テストは確認済み。当時のRust統合テストには期待値不一致6件が残った。release本体・導入先Mod更新と起動前確認済み。2026-10-11にユーザーが音声ON/OFF・文字入力を実機確認済み。[当時のテスト結果と確認範囲](../../docs/2026-10-09-minecraft-text-dialogue.md)。

2026-10-23のBERT 23分類接続ではadapterの286テスト・ビルドとMod配置を確認した。対応Rustは873 passed、0 failed。ゲーム内動作は未確認。[現在の検証・配備記録](../../docs/2026-10-23-modernbert-23class-integration.md)。

## 左下のドギドとワークショップの掛け軸

ドギドは左下・右向き。画面幅の9%、左余白2.5%、下余白4%で、ゆっくり上下する。
作者制作の太線原画 `Dogido_nomal.png` / `Dogido_nomal_close.png` を無加工で使用し、通常顔4.5秒・閉じ目140ミリ秒で瞬きする。コードやCSSでの左右反転・左右対称化は行わない。
ドギド一枚はサーバーなしでも表示する。
川柳の取り合わせ・本句・修正案の生成中だけ作者の `thinking/` の原画10枚を重ねた考え顔に切り替え、生成終了（失敗を含む）で読み上げ用の通常顔へ戻る。workshopが開いているだけでは考え顔にしない。表示状態は既存の掛け軸snapshotから約400ms周期で取得。切断・古い情報・危険・ワールド変更時は通常顔へ戻す。
考え顔は通常と同じ幅9%・左下基準で1210×1400の縦横比を維持。考え顔の上下浮遊は7.6秒周期（通常顔は3.8秒）。ボディ5枚を960msずつ1→2→3→4→5→4→3→2の順で往復、尻尾2枚を位相1050ms付き3300ms、黒目2枚を右5400ms→左4200ms→右7200ms→左4800msで独立切替。`thinking_face.png`の眉・白目・頬・口・手は固定。原画の反転・変形・描き直しはせず、旧変形メッシュは使用しない。`motion off`では体1・尾1・右目線の静止画にする。考え顔の閉眼原画はないため、通常顔の閉眼画像を流用しない。
F1、メニュー、インベントリ、チャット入力中は両方隠れる。

サーバーでworkshopが開くと、右下の掛け軸へ三行を縦書き表示する。
約1秒で現れ、終了時は約1秒で消える。敵の接近・被弾などの危険時は即非表示。
修正案は「未採用案」と明記し、音声・チャットの修正が検証されたら表示にも反映する。
編集相談では本文の縦書きと揃えて左から下・中・上を出し、確認できた対象行を枠と橙色で示す。上の句は右、中の句は中央、下の句は左に対応する（2026-10-21修正）。
選択バーは読み取り専用で、クリックして操作する画面ではない。修正・採否は従来の発話で行う。
書体はMinecraftの標準フォントを使う（ブラウザ試作の明朝体とは異なる）。

クライアント内のチャット欄で以下を入力して閉じると、即時に変更が見える。

- `/dogidohud size 72` — 小さくする（幅24〜256）
- `/dogidohud offset 12 36` — 左端・下端からの余白（GUIピクセル）
- `/dogidohud hide` / `/dogidohud show` — 表示切替
- `/dogidohud reset` — 採用した画面比率の配置へ戻す
- `/dogidohud motion off` / `on` — 上下動と瞬きの切替（offでは通常顔）
- `/dogidoscroll hide` / `show` — 掛け軸だけの表示切替
- `/dogidoscroll sound off` / `on` — 編集時の短い操作音の切替
- `/dogidoscroll motion off` / `on` — 掛け軸のフェードを即時切替にする

ドギドの設定は `config/dogido-character.properties` に保存する。旧試作設定には新しい画面比率の配置を適用し、`size`／`offset`指定時だけ固定GUIピクセルに切り替える。
掛け軸の表示・音・フェード切替は、このMinecraft起動中だけ有効。
`./gradlew test build` 後にjarを導入し、同じ版のサーバーを起動する。
**サーバー・Java自動テストとビルド済み。実Minecraft表示・音声編集・危険割り込みの一連の確認は未完了。**
通常時の配置案は [`dev_tools/character-placement/index.html`](../../dev_tools/character-placement/index.html)。

## 音まわり（現状）

身体の聴覚は発音時に身体へ届いたserver実音と位置付きclient実音を使う。同じ音の二重配送とsnapshot再送で元の年齢を更新せず、未取得・未ロード・予算超過を区別する。音の記録がないことを無音の証拠にしない。詳細と実機確認状況は[観測基準の移行記録](../../docs/2026-10-13-body-observation-origin.md)を参照。

- Minecraft の sound packet から `auditory_threats` / `ambient_sounds` を載せる
- 敵対中のモブ音は `auditory_threats`、友好モブ音とまだ敵対していない蜘蛛・エンダーマン等の音は `ambient_sounds` へ分ける
- `SoundManager` まで届いた焚き火などのブロック音・環境音・天候音も `ambient_sounds` へ載せる
- クライアント側の音観測 TTL は約 **15秒**（300 tick）。「…？ → 今の音なに？」の猶予用
- サーバの player_chat hearing バッファは別途約 **20秒**

## 匂い（遊びの限定観測）

- 伝播力に必要な範囲だけ調べる指定block、指定itemのhotbar 9slotと落下item、沼バイオーム、ゾンビ系を候補にする。ゾンビ系は敵対状態・視認・聴取と独立して収集する
- 伝播力、3次元距離、Minecraft温度をコードで比較し、同種は合算しない。マスキング後に残る各種類と強度を送る
- activeな雨・雪・雷と水中は `suppressed`。候補なしも `none` として明示する
- 火のついたcampfire／soul campfireの実調理slotにある肉・魚だけは寒さによる減衰を受けない
- 方位は計算・送信しない。位置・距離・個数・entity IDも送らず、匂いだけでadapterの戦闘追跡を開始しない
- 雨上がりは通常の成分と分けた一度の気づき候補として送る。180秒は候補期限で、通常の匂いを消さない
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

## 実装の入口

`src/main/java/dogido/fabric/` は責務ごとに分けている。

| 配置 | 担当 |
|---|---|
| `DogidoClientAdapter` | 初期化、tick順、送信タイミング、接続・world変更、各担当への配線 |
| `observation/` | 共用観測型、tick内のworld/blocks取得、イベントごとの観測取得 |
| `observation/{smell,player,world,sound,mobs}/` | 各観測の収集・規則・履歴とreset |
| `protocol/` | JSON組み立て、HTTP/session/sequence、command契約、表示payload解析 |
| `commands/` | 許可済みhotbar操作の検証・実行 |
| `display/` | 両HUD、表示状態、配置・アニメーション、ドギド専用チャット |
| `mixin/` | 音packet・爆発packet・実再生音の既存入口 |

状態は各観測担当が持ち、rootが従来の順番で呼ぶ。分割の検証範囲と実機未確認項目は[2026-10-09の実施記録](../../docs/2026-10-09-fabric-adapter-split-results.md)を参照。

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
5. `/dogido 剣` または `/dogido 剣に持ち替えて` と入力するか音声で依頼し、弱い剣へ切り替わることを確認する
6. 剣を外し、トライデント→斧→弓→道具の順でfallbackすることを確認する
7. 依頼直後に対象slotのitemを動かし、別itemへ勝手に切り替わらないことを確認する
8. 「剣の話をしよう」「剣ある？」「剣に持ち替えないで」では切り替わらないことを確認する

選択だけはクライアント内で完結するため、シングルプレイのLAN公開は不要。

## 乗れる動物の呼び戻し

シングルプレイの統合サーバーで、同じ次元にロード済みの馬系・ラクダ系・ブタ・ストライダー・ハッピーガスト・オウムガイ系・ラマ系（亜種込み14種）を扱う。名前はMinecraftの名札から読み、登録操作は不要。Ownerがある種類は本人だけ、Ownerがない許可済み種類は名札＋鞍／ハーネスを条件にする。

既存の `owned_horses` と `recall_horse` を使い、契約は `integrated_server.horse.recall.v2`／snapshot v2。32頭超過は一覧全体を利用不可にし、チャンクを強制ロードしない。乗員・拘束・牽引・期限・接続世代は実行直前にも再検査する。

周囲2〜5ブロックの着地点で、陸上は乾いた平らな床、ハッピーガストは身体全体の空間、オウムガイ2種は横3×3の水域を必須にする。中心白・外周黄の粒子と種類別の鳴き声を到着時に出す。移動結果と演出完了は別に記録する。

実装は `horse_recall/` にまとめる。通常馬版は2026-10-12にユーザーが実機で成功を報告。14種拡張後の自動検証・実モデル検証とMOD配備は完了し、同日にユーザーから拡張後の実機テスト完了の報告あり。[対象条件と検証・配備記録](../../docs/2026-10-11-horse-recall.md)を参照。

発句時の自動撮影は `client.screenshot.body_view.v1`。材料を固定して新しい句の生成を始めた時点で、共通の身体カメラへ一枚の撮影を依頼する。枠なし原寸PNGを標準 `screenshots` に `dogido_` 付き日時名で保存し、同じ生成の句が保存された後にだけ本棚へ紐付ける。会話画像の24時間削除の対象にはしない。詳細は [本棚の自動撮影仕様](../../docs/2026-10-13-bookshelf-integration.md)。
