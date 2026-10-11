# 3D身体とRust本線の連携境界

状態: **2026-10-06、身体Mod 0.3.0 / Body API v2は単体実装済み。本線Rust・既存adapterへの接続、実モデルとゲーム内の確認は未実施。** 本線Rust化の検証完了を表す文書ではありません。

身体の正本はリポジトリ内の`animation/cube_dogido/cube-dogido-lab`です。

- [身体の操作と生命ルール](../../animation/cube_dogido/cube-dogido-lab/README.md)
- [Java API v2の型・世代・結果・最小例](../../animation/cube_dogido/cube-dogido-lab/docs/body-api.md)
- [本線の接続先対応表・実装順・検証手順](../../animation/cube_dogido/cube-dogido-lab/docs/mainline-integration.md)

## 担当範囲

| コンポーネント | 所有する処理 |
|---|---|
| 身体Mod | 毎tickの指定移動、HP、休息回復、食料観測と消費、損耗・倒れる・復活、owner登録、Java APIとFabric C2S/S2C |
| 既存client adapter | 身体Modとの任意接続、既存HTTP sessionでの受け渡し、client threadへの配送、接続世代と未ack結果の管理（身体部分は未接続） |
| Rust本線の身体用処理 | モデルへ身体観測を渡して次の一手を選ばせる処理、要求の検証、結果の利用（未実装） |
| Rust本線の既存処理 | 会話・戦闘・川柳・保存、観測の会話投影、発話の優先・配送・実再生後の履歴確定 |

身体担当は独立Modを編集し、adapterとRustへの接続は本線側で行います。身体ModにはHTTP session、LLM、TTS、会話履歴を作りません。

## 行動を選ぶ側と身体のルール

身体実験は、モデルが移動・休息・食事等の行動をなるべく選ぶ方針です。0.3.0の外部操作は`DIRECT`に限定し、低HPだから休む・食料を見たから食べる・危険だから別方向へ逃げる処理を自動で入れません。

- モデル側へ渡す操作は`MOVE_TO / REST / WAIT / EAT / CALL`。操作結果を次の観測とともに返します。
- コードは物理的な移動、所有者と世代の検証、実物の食料消費、HPと時間経過を担当します。
- 持ち主がサバイバル／アドベンチャーの場合だけ通常ダメージを受けます。ブロック通過可能で、落下・溺れは無効です。
- 長い壁内滞在は損耗し、明示RESTと静止で回復します。食事は現物一個を消費して回復します。
- HP0で倒れ、待機後に持ち主の**復活時点の現在座標**で回復します。owner不在・位置未ロードなら理由を返して待ち、身体未ロード時に別個体を作りません。
- 旧0.2の追従・探索・退避優先・火炎回避はローカル比較用コマンドに残しています。DIRECTの外部制御の既定として使いません。

これは身体実験の行動選択の分担です。本線の通常会話read planner、戦闘優先、明示依頼による`select_sword`、保存判断は現行の分担を維持します。

## 通信・所有者・世代

```text
Rust本線の身体用処理（今後接続）
  ↕ 既存HTTP session
既存client adapter（今後接続）
  ↕ BodyBridge v2（不変データ）
身体Modのclient窓口
  ↕ Fabric C2S / S2C
身体Modのserver制御 → Entity
```

- ゲーム世界とEntityへ触るのは論理サーバースレッドです。HTTP workerは不変snapshotを読み、操作はclient実行キューからC2Sへ渡します。
- entrypointは`dogido:body_bridge_v2`。versionも照合します。身体Mod不在・版不一致でも既存adapterと2D表示が起動する任意連携にします。
- ownerはMinecraft UUIDで、既存の未所有個体を明示的に結び付けます。複数候補から近い個体を自動選択せず、C2Sの実送信者とownerを照合します。
- HTTP session、adapterのworldEpoch、身体Contextを区別します。古いモデル結果を新しいContextへ付け替えず、観測からやり直します。
- 状態revision・一回操作sequence・ID・観測tick・期限を照合します。同じ操作の再送は同じpayloadで行い、二重実行を防ぎます。
- `QUEUED`・`ACCEPTED`と実行成功を分けます。RESTの成功は休息開始であり、全回復ではありません。
- 制御停止・lease失効では指定移動と休息を停止します。表示・個体削除とは別で、物理的な損耗と復活の規則は続きます。
- 身体の位置が取得できなければ欠損と理由を返し、架空のゼロ座標やHP0で補いません。

専用サーバーを含むゲーム内通信は実機確認待ちです。Java APIと通信のコンパイル成功だけで接続済みとは扱いません。

## 本線の現在実装との接続差分

現在のRustのgame-event応答は`state=null / outputs=null`です。旧資料にある`state.mode`から退避を導く案は使えません。現在のtyped command/resultは`SelectHotbar`用なので、身体用の型・capability・consumer・結果ackを別途追加します。

HTTPの`body_observation`等の項目名は**未確定**です。未知extraを受け取れたことは型検証や身体連携の実装を意味しません。接続時に本線の`docs/event-schema.md`・`docs/adapter-api.md`、Rustとadapterの型、両側のfixtureを揃えます。

詳細なファイル対応表と確認項目は[接続手順](../../animation/cube_dogido/cube-dogido-lab/docs/mainline-integration.md)を正とします。本線の現行構成と起動は[本線README](../README.md)、実機確認は[manual-dialogue-check.md](../manual-dialogue-check.md)を参照します。

## 身体観測と発話

- 身体の位置・HP・DOWNED・復活は、既存`player`、`visual_threats`、`player_died`を上書きしません。身体付近の敵をそのままプレイヤーへの危険へ変換しません。
- 身体の食料観測は実装済みです。一般モブ視認・嗅覚・構造物センサーは`NOT_IMPLEMENTED`であり、空の観測結果とは違います。
- 現行の嗅覚入力は`smell_observation`です。廃止済み`zombie_scent_clues`は再導入しません。生座標・対象IDは身体操作や診断に限定し、嗅覚の会話入力へ混入させません。
- 観測主体・dimension・観測tick・根拠と、会話へ投影する情報を区別します。身体からのセンサー距離をプレイヤーの視認・スメル距離へ流用しません。
- 発話の優先・自発反応の間隔・対象照合・音声取消・実再生後の履歴確定は本線が管理します。匂い単独で戦闘を開始しません。

雷雨・花・就寝の専用行動と、一般身体センサーは後続項目です。ログアウト時の見せ方・保管・通知距離・履歴の永続化範囲も未確定です。

## 検証・引き渡し

身体0.3.0は39件の自動テストとビルドが成功しています。Minecraftでの被弾・食事・保存再開・復活・次元転送・通信、モデルの行動選択は未確認です。本線のRust試験の成否は本線側の検証記録を参照し、身体の単体結果と混ぜません。

身体ディレクトリは現在Git追跡対象外なので、本線のcommitだけではソース一式を共有できません。接続時は身体ソース・API JAR・Mod JAR・本書と接続手順を同時に引き渡します。
