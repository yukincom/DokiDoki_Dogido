# Body API v3

身体Mod **0.4.0 / protocol 3** の実装契約です。既存adapterは自他位置の読取りを接続済み。モデルによる移動・食事等の選択は未接続です。[現行の統合計画](../../../../docs/2026-10-11-body-integration-plan.md)と合わせて使います。

## v2からの変更

- entrypoint：`dogido:body_bridge_v3`
- payload：`dogido_experiment:body_request_v3`／`body_snapshot_v3`
- snapshotに`pose(eyePosition, ownerPosition)`を追加。`position`・`yaw`と同じserver tickで採る。座標はblock、yawは度。未取得はnull。
- `snapshotAgeMillis()`を追加。snapshot受信後の経過msを既存HTTP送信に引き継ぎ、同じ観測の添付で鮮度を更新しない。
- DOWNED・復活待機中も毎server tickに観測revisionを進める。
- この位置は`server_pose`で、視線遮蔽の検査を通した視認ではない。

## v2で追加済みの操作

- HP・壁内時間・復活・食料観測をsnapshotへ追加
- `MOVE_TO`・`REST`・`WAIT`・`EAT`を一回操作へ追加
- 外部からの状態指定は`Mode.DIRECT`、`retreat=false`に限定
- 初回bind／再読み込み／失効／復活後はDIRECTで待機
- v1の固定追従・探索・退避はローカル試験コマンドに限定

v1／v2のクラスやJARをv3として扱いません。entrypointとprotocolの両方を照合します。旧entrypoint／payloadの受信口はありません。

## 公開APIとスレッド

パッケージは`dogido.lab.api`。`BodyBridge`と`BodyProtocol`のrecord／enumが公開契約です。APIにはEntity／Worldが含まれません。

```java
public interface BodyBridge {
    int protocolVersion();
    boolean available();
    Optional<BodyProtocol.Snapshot> snapshot();
    long snapshotAgeMillis();
    BodyProtocol.Submission applyState(BodyProtocol.StateRequest request);
    BodyProtocol.Submission requestAction(BodyProtocol.ActionRequest request);
}
```

adapterは`cube-dogido-lab-0.4.0-body-api.jar`を**compileOnly**で参照します。APIクラスをadapterへ重複同梱しません。身体Mod不在でもadapterを読み込めるよう、任意連携クラスを分けます。

`available()`と送信はMinecraftのclient thread上で行います。`snapshot()`は不変データを読むだけで、HTTP workerから参照できます。受信後3秒を過ぎたsnapshotは空です。HTTP転送時はclient threadでsnapshotと経過msを続けて読み、3秒未満だけを現在値として送ります。受信登録と10client tickごとのポーリングは身体Modが所有します。

## 最小例

次のクラスをロードする前に、呼び出し側で`FabricLoader.getInstance().isModLoaded("dogido_experiment")`とv3 entrypointの存在を確認します。

```java
BodyBridge bridge = FabricLoader.getInstance()
    .getEntrypoints(BodyProtocol.ENTRYPOINT, BodyBridge.class)
    .stream().findFirst().orElse(null);
```

client thread上で、現在のsnapshotから一つの世代を保持します。

```java
var s = bridge == null ? null : bridge.snapshot().orElse(null);
if (bridge != null && bridge.protocolVersion() == BodyProtocol.VERSION
        && bridge.available() && s != null
        && s.presence() == BodyProtocol.Presence.PRESENT
        && s.vitals().life() == BodyProtocol.Life.ACTIVE) {
    bridge.applyState(new BodyProtocol.StateRequest(
        s.context(), revision++, s.snapshotRevision(), s.serverTick(), 100,
        true, BodyProtocol.Mode.DIRECT, false));
}
```

状態の`ACCEPTED`をsnapshotで照合した後、モデルが選んだ一手を送ります。以下は休息の例です。

```java
var request = new BodyProtocol.ActionRequest(
    s.context(), actionSequence++, UUID.randomUUID(),
    s.serverTick(), 60, BodyProtocol.Action.REST, null, null);
bridge.requestAction(request);
```

`MOVE_TO`は`destination`へ身体の足元のワールド座標を入れ、`foodId=null`。`EAT`は直近の`food.targets[].id`を`foodId`へ入れ、`destination=null`。`REST`・`WAIT`・`CALL`は両方nullです。行き先・食料を省略して別の行動へ補完する処理はありません。

`revision`と`actionSequence`は同じContext内で単調増加させます。更新されたsnapshotを基に送る際にも、古いモデル結果に新しいContextを付け直しません。

## 状態と一回操作

`StateRequest(context, revision, basedOnSnapshot, observedServerTick, leaseTicks, enabled, mode, retreat)`

- `enabled`は外部制御の有効期限付き許可です。`enabled=true`だけで探索・退避・休息を選びません。
- `mode=DIRECT`、`retreat=false`を使います。逃げる方向も`MOVE_TO`等で選びます。
- `enabled=false`、lease失効、世代変更で移動／休息指示を止めます。HP・環境接触・復活の物理ルールは継続します。
- ローカル試験コマンドはlease更新なしで試せます。次の外部状態が受理されると外部制御へ切り替わります。

`ActionRequest(context, sequence, actionId, observedServerTick, validForTicks, action, destination, foodId)`

| action | 意味 | 実行結果 |
|---|---|---|
| `MOVE_TO` | 身体から32ブロック以内の読み込み済み座標へ移動 | 到着で`SUCCEEDED/arrived`。途中の停止理由はsnapshotへ |
| `REST` | 現在地で休息する | 実際に静止して`SUCCEEDED/resting`。全回復を意味しない |
| `WAIT` | 現在地で待つ | 実際に静止して`SUCCEEDED/waiting`。休息回復なし |
| `EAT` | 指定された、現在届く食料一個を消費 | 消費後に`SUCCEEDED/food_consumed`。回復後HPはvitalsで照合 |
| `CALL` | 持ち主の正面へ呼び寄せる | 到着で`SUCCEEDED/arrived`。従来の呼び寄せプリミティブ |

DIRECTの移動は火・溶岩・敵の周辺・壁内を理由に別の行動へ変えません。生物との衝突・未ロード領域・ワールド境界はゲーム側で処理します。届かない食料へ自動移動せず、`food_out_of_reach`を返します。

`QUEUED`はclient通信への投入、`ACCEPTED`はserver受付です。`SUCCEEDED`は表に示した実行結果です。`INTERRUPTED`・`EXPIRED`・`REJECTED`と区別します。新しい一回操作は実行中の操作を中断して置き換えます。REST／WAITは完了後も、新しい行動・停止・失効まで指定した姿勢を続けます。

再送は**同じaction ID・sequence・全payload**を使います。直近32件の要求と結果を保持し、キャッシュ済みACCEPTEDも再実行しません。同じIDで内容を変えた場合は`action_id_conflict`。食べ物が消費済みでも、同じ要求の再送には保存した結果を返します。

## 世代・時刻

`Context(ownerId, bodyId, epoch, dimension)`のUUIDはMinecraftの値です。dimensionは身体の現在次元。`Snapshot.ownerDimension`は持ち主側で、身体が転送待ちなら違っていてよい値です。

- C2S送信者とownerを照合し、表示名で所有者を決めません。
- 身体の読み込み、ownerの切断／再接続・死亡／復活・次元変更、身体転送、身体の復活でepochを更新します。
- HP0では進行中操作を中断し、倒れている間の状態／行動を拒否します。復活後は新しいepochで待機します。
- 古いContext・state revision・action sequenceを拒否します。
- `observedServerTick`は基にしたsnapshotの値。未来値／100tickより古い状態は拒否します。
- `leaseTicks`は受理から1〜200tick。一回操作の開始受付期限`validForTicks`は基にしたtickから1〜100tickです。
- `basedOnSnapshot`は追跡用で、現在のsnapshot revisionとの完全一致を要求しません。
- ポーズ中はserver tickによる時間が止まります。clientの3秒失効は別の実時間です。

HTTP session、adapterのworldEpoch、身体のContextは別々に照合します。HTTP完了後にclientへ渡す段階でもsession／world世代を再確認します。

## snapshotの正本

`Snapshot`は次を返します。

| 領域 | 内容 |
|---|---|
| `version`・`presence` | protocol、`PRESENT/NOT_BOUND/UNLOADED/OWNER_UNAVAILABLE` |
| `context`・`ownerDimension` | 身体の世代と持ち主の次元 |
| `serverTick`・`snapshotRevision` | 観測時点と更新番号 |
| `position`・`yaw`・`activity`・`reason` | 身体の実位置・実動作。positionは足元 |
| `appliedStateRevision`・`stateResult`・`actionResult` | 適用した状態と、直近操作の実行結果 |
| `vitals` | `life(ACTIVE/DOWNED)`、HP／上限、damage有効、ownerGameMode、壁内tick、休息tick、復活残tick、復活回数、直近被弾 |
| `food` | 食料専用の`status`・観測tick・候補・`truncated` |
| `sensors` | 一般的なモブ視認・スメル・構造物センサーは`NOT_IMPLEMENTED` |

未登録／未ロード時のposition・vitalsはnullです。架空のゼロ座標・HP0へ置き換えません。`OWNER_UNAVAILABLE`と身体の`DOWNED`は別の状態です。

被弾の`lastDamage`は`sequence/serverTick/amount/cause`。amountは実際のHP減少で、攻撃者の位置を補作しません。sequenceと復活回数は個体へ保存します。再読み込み直後は過去の被弾詳細を現在観測として再生せず、lastDamageはnullです。

food候補は身体から8ブロック以内、読み込み済みで見通せる食料を近い順に最大16件です。候補IDは操作用で、会話へ直接投影するものではありません。未知／未ロード領域や壁の向こうの食料が存在しないという意味には使いません。倒れている／owner不在／身体未ロードでは`UNAVAILABLE`です。

食事直前に、観測の100tick以内・IDに対応する現物／種類・成熟状態・2ブロック以内・見通し・欠損HPを再検査します。植物消費は実ブロックを更新し、落下アイテムはstack一個を消費します。

## 接続できている範囲

実装済み：server身体制御、HP／時間ルール、食料観測と消費、任意Java entrypoint、C2S/S2Cの型と配線。

未接続：既存adapter→Rustへの専用型・実行capability・モデルの行動選択・結果の受領／利用。身体ModはHTTPサーバー、モデル、TTS、会話セッションを起動しません。

実機確認待ち：ゲーム内通信、描画・補間、食事／被弾、保存再開、復活・次元移動、専用サーバー。自動検査はこの実機確認を代替しません。
