# 3D身体とRust本線の連携境界

状態: **2026-10-12、身体Mod 0.4.0 / Body API v3と、自他位置を既存adapter・Rust通常会話へ渡すP1を本線mainへ導入済み。Minecraft接続と本体起動を確認。実機で身体・playerの位置更新と会話材料への配送を確認。前後2地点の返答は不合格で、10月13日にレビュー済みの読み方と呼び名を追加したが、保存座標の比較では前後の応答は改善せず。反映後の実機3地点でも同じ「左側」の返答が続き、位置理解は不合格。**

2026-10-13更新: [姿勢拡張・owner視認の第一段階](../../docs/2026-10-13-body-vision-phase1.md)を実装。以下の現行契約はBody API v4／身体0.5.0／HTTP観測v2。旧版の実機確認と、新版の自動検査・未配置を区別する。

身体の正本は`animation/cube_dogido/cube-dogido-lab`。通常の起動場所は書類側の`DokiDoki-Dogido`のmainで、普段のサーバーappを使う。

- [本線導入・検証記録と次の実機操作](../../docs/2026-10-12-body-p1-install.md)
- [身体統合計画・P1の受け入れ条件](../../docs/2026-10-11-body-integration-plan.md)
- [身体の操作と生命ルール](../../animation/cube_dogido/cube-dogido-lab/README.md)
- [Java API v4の型・世代・結果](../../animation/cube_dogido/cube-dogido-lab/docs/body-api.md)
- [2026-10-06時点の接続計画（履歴）](../../animation/cube_dogido/cube-dogido-lab/docs/mainline-integration.md)

## 担当範囲と現在の接続

| コンポーネント | 所有する処理と接続状態 |
|---|---|
| 身体Mod | 毎tickの指定移動、HP、休息・食事・損耗・復活、owner登録、Java APIとFabric C2S/S2C。同一server tickの身体・owner姿勢とowner視認を測る |
| 既存client adapterの`body/` | 任意BodyBridgeの検出・版照合、姿勢・視認snapshotの読取り、既存HTTPイベントへの配送。身体の操作・結果ackの往復は未接続 |
| Rustの`body/`と`dialogue/body_runtime.rs` | owner UUID・次元・世代・時刻・鮮度の検証、現在／前回の姿勢・視認保持、通常会話への読取り専用コピー。モデルによる身体行動の選択は未実装 |
| Rust本線の既存処理 | 会話・戦闘・川柳・保存、発話の優先・配送・実再生／文字表示ACK後の履歴確定 |

身体ModにはHTTP session、LLM、TTS、会話履歴を作らない。身体の位置材料は既存通常会話の一回生成へ加える。10月13日から、身体観測がある会話に限ってレビュー済みの短い読み方と既存の呼び名を添える。別のモデル呼出し・身体専用HTTPサーバーは追加していない。

## P1の観測経路

```text
身体Modのserver測定（身体・ownerの同時刻の姿勢とowner視認）
  → Fabric S2C → BodyBridge v4の不変snapshot
  → adapterのbody/ → 既存HTTPイベントのbody_observation
  → Rustのbody/で検証・現在／前回を保持
  → world_context.observations.body_observation → 通常会話
```

- entrypointは`dogido:body_bridge_v4`。Mod不在・版不一致・身体未登録・未ロード・異次元・古いsnapshot等は、理由付きの未取得として扱う。playerの位置を身体へ代入しない。
- session登録の実player UUIDと身体のowner UUIDを照合する。名前しかない従来接続は会話を継続し、身体観測だけ利用不可とする。
- HTTP session、adapterのworldEpoch、身体Contextのowner・body ID・epoch・dimensionは別の識別子。復活・次元変更・再接続で過去の比較対象を流用しない。
- `server_tick`・`snapshot_revision`・受信経過・HTTP配送の時刻を検証する。重複・逆順・部分イベントで観測の鮮度を延長しない。
- 自分＝ドギドの位置・目・yaw/view_yaw/view_pitchと、所有者の位置・yaw/pitchは`pose_basis=server_pose`。`current/previous.vision`は別の根拠`body_eye_raycast_fov`で、身体の目から距離・FOV・遮蔽を測った結果。座標取得だけで可視とは扱わない。壁内視点はスペクテイター相当とし、抜けた先の別の壁は遮蔽する。
- 距離・前後左右の正解は検査用コードで計算する。モデルには現在／前回の座標と向きを渡し、正解ラベルは先に与えない。

現在／前回は隣り合うHTTP観測（既定約1秒）で、長時間前との比較ではない。位置変化だけで自動発話を開始する仕組みはP1に含めない。

## 行動を選ぶ側と身体のルール

移動・休息・食事等をモデルに選ばせる接続は後続。身体単体には`MOVE_TO / REST / WAIT / EAT / CALL`とDIRECT制御を実装済みだが、Rustからの身体操作配送・結果ack・モデル選択はまだ接続していない。

- コードは物理的な移動、所有者と世代の検証、実物の食料消費、HPと時間経過を担当する。DIRECTでは低HP・食料・敵から自動で次の行動を選ばない。
- 持ち主がサバイバル／アドベンチャーの場合だけ通常ダメージを受ける。ブロック通過可能で落下・溺れは無効。長い壁内滞在は損耗し、明示RESTと静止で回復する。
- 食事は現物一個を消費する。HP0で倒れ、待機後に持ち主の復活時点の現在座標で回復する。owner不在・位置未ロードなら待ち、身体未ロード時に別個体を作らない。
- 旧追従・探索・退避優先・火炎回避は比較用コマンド。DIRECTの外部制御の既定にはしない。
- 操作sequence・ID・観測tick・期限を照合し、同じ再送は同じpayloadで行う。古いモデル結果に最新Contextを付け直さない。
- `QUEUED`・`ACCEPTED`と実行成功を分ける。REST成功は休息開始で、全回復ではない。lease失効では移動と休息を停止し、物理的な損耗・復活は続く。

通常会話の一回生成、戦闘優先、既存の限定支援、保存判断の担当は維持する。HTTP応答の`state=null`から身体行動を推測せず、将来の身体commandは専用の型・capability・consumer・結果ackを定義する。

## 観測を混ぜない

- 身体の位置・HP・DOWNED・復活は、既存`player`・`visual_threats`・`player_died`を上書きしない。身体付近の敵をそのままプレイヤーの危険へ変換しない。
- 身体単体の食料観測とowner視認は実装済み。`sensors=OWNER_VISION_ONLY`で、一般モブ視認・嗅覚・構造物走査は未実装。敵や匂いがないという結果ではない。HTTPは姿勢とowner視認を接続する。
- 従来の周辺会話材料はplayer由来と明記する。owner視認以外の視覚・聴覚・嗅覚はまだ身体へ移行していない。
- 現行の嗅覚入力は`smell_observation`。身体の生座標・対象IDを嗅覚入力へ混ぜず、廃止済み`zombie_scent_clues`を再導入しない。
- 発話の優先・自発反応・音声取消・履歴確定は本線が管理する。匂い単独で戦闘を開始しない。

雷雨・花・就寝の専用行動、ログアウト時の見せ方・保管・通知距離・身体履歴の永続化は後続項目。

## 検証・引き渡し

本線導入時の自動検査はRust 677件、adapter 139件、身体39件が成功し、Clippyの指摘は0。Rust releaseと両Modを生成し、配置後に普段のappの起動、MinecraftでのMod読込みとHTTP session成立を確認した。

10月12日の実機で身体・playerの位置更新と通常会話への配送を確認したが、前後2地点の回答はともに「右」で不合格。10月13日の説明追加後はRust全678件が成功したものの、保存座標による独立した比較で前後の回答は改善せず。反映後の実機3地点でも同じ「左」の応答が続いた。実モデルの位置理解の改善と再試験、位置配送の異常系、ゲーム内描画・食事・被弾・保存再開・復活・次元転送、専用サーバーでの動作は実機確認待ち。既存の警告発言が届いたことを、身体の位置理解の合格として扱わない。

身体ソース・テスト・Gradle wrapper・元モデルは本線でGit追跡済み。成果物・キャッシュ・復旧アーカイブは対象外。旧Mod・配置前ファイル・検査ログ・ハッシュは`.dogido_tools/checkpoints/2026-10-12-body-p1-install/`へ保存した。配置と復旧の手順は[導入記録](../../docs/2026-10-12-body-p1-install.md)を参照。
