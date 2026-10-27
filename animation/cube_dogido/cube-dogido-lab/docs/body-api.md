# Body API v12

## v12の移動と注視の並行

BERTへ変更済み。旧判定へ戻さない。身体Mod **0.7.14 / protocol 12** とadapterを組で更新します。entrypointは`dogido:body_bridge_v12`、payloadは`body_request_v12`／`body_snapshot_v12`。旧版の受信口とfallbackはありません。HTTP観測v3・自律制御v2は維持します。

### 注視

HTTP `body.request.v1`の`look_direction / look_target / look_player / look_relative_*`を既存`LOOK_AT`へ接続します。相対8方向は受付時の**身体自身のyaw**を基準とする水平8block先、同じ方向は身体の眼からplayerの現在の視線に沿う8block先、対象は現在crosshairの実ヒット点（命中なしは本人の視線方向）、player注視は本人の現在の眼です。移動先には使いません。

- `ActionRequest.destination`は注視点。64block以内の有限点、所有者・context・sequence・期限・重複は既存action受付で検査します。
- `MOVE_TO`と`LOOK_AT`は同じsequenceを使い、進行と結果は分けます。snapshotの`actionResult`は移動、追加した`lookResults`は既存の直近32件台帳にある注視結果と、台帳登録前に拒否された直近一件です。snapshotの上限は16KiB、要求は従来の8KiBです。CALL中もLOOKを受理します。再送では元の点・観測tick・action IDを保持します。
- 注視は移動制御無効・退避／危険・呼び寄せ・進行中action・追従・その他の活動状態を理由に拒否しません。移動計画・速度を変えずにyaw/pitchを適用します。後から受理した明示CALLは古い注視保持を解除し、CALL後の新しいLOOKは移動中・到着時とも優先します。
- 向きを適用したtickで`SUCCEEDED/facing_target`を一度返します。位置差・角度差・停止や姿勢安定を成功条件にしません。成功後100tickは注視点を保持し、その間も移動・撮影・LLM返答を並行します。ダウン時は未完了の移動と注視をともに終端化します。
- `PoseObservation.viewPitch`は身体の実pitchです。明示撮影は次の描画frameを使い、保存後の姿勢を理由に画像を破棄しません。[設計方針](../../../../docs/2026-10-26-look-capture-and-darkness.md)を参照してください。

### 明示呼び寄せ

`BodyBridge.requestRecall(RecallRequest)`は、owner UUID・接続ID・命令ID・現在dimension・最新snapshotのserver tick・20tick以内の期限を受けます。実送信者をserverで確認して、既存`BodyManager.recall`へ一度だけ渡します。通常`ActionRequest`のcontext検証は変更しません。

- 所有する既存個体を優先し、未ロードなら失敗。未所有個体の取得・新規生成・安全な配置は既存の明示CALL規則を使います。
- 同じ命令IDは直近32件の結果を再利用し、再度の生成やCALLを起こしません。接続の実playerが変わると別の台帳です。
- `RecallReceipt`は元のowner・接続・命令ID・dimensionと、実身体から得たcontext・action結果を持ちます。snapshotの`recall_result`で配送し、`recallReceipt(actionId)`から取得します。
- `QUEUED / ACCEPTED`は受付。`SUCCEEDED/arrived`だけを到着としてHTTPへ返します。同じ個体のCALL転送で変わるepochは実身体から追跡します。
- 移動中は実際の進行方向へ向き、到着時に後からのLOOKがなければプレイヤーへ回頭します。成功通知は到着時に一度だけ返し、短距離でも回頭は自然に続けます。回頭中の`call_arrival_look`では通常の自律選択と配送待ちを止め、補間終了後に通常注視へ戻ります。[方針と検証](../../../../docs/2026-10-27-heat-warning-and-look-priority.md)。

### HTTPの寿命と入力世代

`body_request`は命令ID、最大5秒の送信期限、owner、dimension、入力世代、look時はcontextを必須にします。adapterはsession・world epoch・入力世代・実時計を送信直前にも照合し、切断・世界変更・新しい入力世代の古いcallbackを配送しません。取消や期限切れでadapterの元命令の送信枠を解放します。実行結果は命令IDとsequence（look）または接続ID（recall）を照合し、35秒以内に終端結果がなければ期限切れです。すでに身体へ受理された行動は身体側の有界制御と既存の優先規則に従います。

2026-10-23に身体Modの166テストとadapterの286テスト・ビルドを確認し、普段のMinecraftの`mods`へ配置しました。ゲーム内動作は未確認です。[検証・配備記録](../../../../docs/2026-10-23-modernbert-23class-integration.md)を参照してください。

## 0.7.10の帰還後の顔確認

実帰還後は、最新の作業状態を確認し、顔を見るかそばに留まるかを一度だけ判断します。帰還直後の待機中でも、実到着からの顔への一手を受け付けます。作業・危険・優先行動の検査は維持し、顔や見物への到着で繰り返しません。Body API v9とHTTP自律制御v2の外形は変更していません。

## 0.7.9の安心度と接近

- entrypointは`dogido:body_bridge_v9`、payloadは`body_request_v9`／`body_snapshot_v9`。旧v8の受信口は残さず、身体・adapter・Rustを組で更新する。
- `ControlObservation`へ`Reassurance(value,ownerDistance,ownerVisible,faceVisible,ownerWorking)`を追加。値は0〜100、距離はblock。身体server tickの実測で更新し、snapshot読取・命令受付・到着結果では加算しない。
- `ActionRequest`へ`MovePurpose`を追加。`MOVE_TO`の`INSPECT`は外部の検証済みdestination、`OWNER_NEAR`／`OWNER_FACE`はdestination=nullとし、身体が現在ownerから安全な位置を選ぶ。その他のactionではpurposeを指定しない。
- owner接近はownerの移動に追随し、カメラ回転だけでは周回しない。作業正面・敵・衝突・期限・CALL・緊急追従を毎tick再確認する。`OWNER_FACE`は作業中に受け付けない。発話中の注視を優先する。
- 到着時だけ`SUCCEEDED/arrived`。その後100tickは`owner_near_dwell`／`owner_face_dwell`。HTTPは既存命令のpurposeとtarget=nullへ対応する自律制御v2を使う。
- 通常の接近は統合された環境判断が選ぶ。コードの距離追従は`min(warpDistance,max(followDistance*1.5,followDistance+4))`を超えた場合（既定12block）、高さ復帰・危険回避・ワープは維持する。
- [現行仕様と検証状態](../../../../docs/2026-10-20-reassurance.md)。以下の版別説明は変更履歴で、現在値と重なる箇所は本節を優先する。

## 0.7.8の発話注視

`BodyBridge.observeSpeechGaze(SpeechGazeObservation)`で同じcontext・sequence・観測server tickと注視点を受けます。座標はblock、target=nullは終了。観測tickから20tick、範囲64block以内の有限座標だけを受理します。同tickの終了は新sequenceなら受け、古い再配送で復活させません。context変更で破棄し、保存しません。

既存会話snapshotの`body_gaze`をadapterが読み、現在の実視認・発音点だけへ照合します。向きはowner視認と同じ`BodyVisionGeometry.facing`から求め、yawを最大6度/tick、pitchを4度/tickで合わせます。音声中は待機見回しより優先し、移動・衝突・危険回避を変更しません。CALLはowner注視を優先します。[仕様と確認状態](../../../../docs/2026-10-18-body-speech-gaze.md)。

## 0.7.7の注意範囲と観察時間

- FOLLOWの近傍注意は、身体の眼とyawを基準に前8・後ろ1・左右2・上下3block。上下は世界の鉛直を使い、壁などの遮蔽は既存rayで除外します。owner視認・敵・聴覚・衝突回避は従来の範囲を保ちます。
- 追従中、初回、追従完了後3秒、ownerの5block/秒を超える移動の後3秒は各方向1block。`holding / follow_settling`中は新規観察を開始しません。
- 観察MOVE_TOの配送受付から対象一件へ集中し、実到着から100tick（通常20TPSで5秒）の`inspection_dwell`終了で再探索します。adapterはcontext・action ID・sequenceを照合し、失敗・危険・追従・世代変更・期限切れで解除します。
- Rustの選択開始は最短3秒間隔。`follow_lookaround`中も選択・受付を許可し、現在視認と安全性の再照合後、既存inspect受付で見回しを止めます。
- Body API v7と既存通信形式は継続し、モデル・プロンプトは変更しません。[今回の仕様と検証](../../../../docs/2026-10-17-body-attention.md)。

## 0.7.6から継続する縦追従

- `followDistance`の初期値は8block。距離超過に加え、身体位置がownerの`feet.y + bodyHeight`から上下4blockを超えても追従します。開始後は水平2block以内の安全位置まで寄ります。
- 従来の横／後ろが全て塞がれた場合、ワールド軸の通路・狭い側方・上下を順に検査します。竪穴では身体がいる側の上下候補を先に調べ、ownerを横切る目標を避けます。CALLと通常ワープも同じ補助候補を使います。
- CALL／FOLLOWの到着時には、目標だけでなく現在位置の空間・危険も照合します。
- 公開API v7は継続します。対応adapterは候補・実行・停止位置を現在の`followDistance`へ揃え、Rustは範囲内の候補だけで3秒以上の間隔を空けて選択します。
- 仕様と導入状況は[縦追従・寄り道の記録](../../../../docs/2026-10-17-body-vertical-follow.md)を参照してください。

観察の停止先は、共有する高さ計算で追従基準高から上下3.2block以内へ収める。到着許容0.4と到着後の浮遊全幅0.36を足しても上下4を超えず、見物途中で距離追従へ切り替わることを防ぐ。身体側も受付時に再照合する。BodyProtocolにはこの計算用の定数・メソッドだけを追加し、record・enum・通信形式は変更しない。

## 0.7.5の呼び寄せ完了と浮遊

- CALLは安全な現在目標の0.2block以内、かつownerとの水平距離が`callDistance`以内に入ったtickで`SUCCEEDED/arrived`を一度返し、CALLを解除します。次tickから通常modeの待機・見回し・観察・追従へ戻り、到着後のownerの回転や小移動へ貼り付きません。
- 移動中・待機中とも振幅0.18block、周期120tickの上下波を実移動へ重ねます。通常20TPSで約6秒。退避・埋まりからの復帰・安全目標なしでは浮遊を休止し、衝突・危険・未ロード範囲・作業正面を避ける既存検査を通る追加分だけを使います。
- 位相補正した目標と、そこへの基本移動の次の位置も空間・危険を検査し、不適なら補正前の目標への移動に戻します。
- 浮遊が止められたtickは波の位相も止め、高度の累積を防ぎます。慣性には基本移動だけを渡し、波を二重に増幅しません。
- 詳細と検証境界は[呼び寄せ完了・浮遊の仕様](../../../../docs/2026-10-17-body-call-release-floating.md)を参照してください。

## 0.7.4の距離追従

- protocol 7は継続し、adapterの更新は不要です。
- `followDistance`は追従を始める距離です。開始後は現在ownerの水平2block以内・目線付近の安全位置まで追従します。
- 選んだ横／後ろの位置をownerの移動に合わせて更新します。非作業時はカメラ回転だけで周回せず、採掘中は現在の正面を避けて候補と経路を更新します。
- ワープ後も接近を再開します。危険回避・候補なしでは到着扱いにせず、未到着の追従中は観察MOVE_TOを受け付けません。
- 詳細と検証境界は[通常追従の仕様](../../../../docs/2026-10-17-body-follow-beside.md)を参照してください。

## v6からの変更

- entrypointは`dogido:body_bridge_v8`、payloadは`body_request_v8`／`body_snapshot_v8`。身体Modとadapterを組で更新します。
- `observeOwnerWork(OwnerWorkObservation(context,observedServerTick,working))`を追加。既存の`player.block_breaking_active`を共有し、採掘中はCALLの正面を避けます。未取得・期限切れも配置だけは横／後ろへ寄せます。
- adapterは5tickごとに現在値を送り、同じ身体contextと観測tickで20tick以内だけ有効。falseも送り、同tick／古い／未来／別世代／working欠損の観測を受け付けません。行動やmodeを変更する要求ではありません。
- 登録時と古い保存からの読込み時はFOLLOW。ローカルの明示modeは保存します。外部lease失効は自動でFOLLOWへ戻さず、従来どおり停止します。
- CALLは現在の持ち主へ水平2block以内。通常は正面、採掘中は横／後ろ。接近中は現在位置・向き・作業を毎tick再確認し、実到着で成功して通常動作へ戻ります。危険回避中は到着扱いにしません。
- `/dogidobody call`は既存所有個体を優先し、未登録なら近くの未所有一体を登録、いなければ安全位置へ生成します。所有台帳あり未ロード・複数未所有・DOWNEDを新規個体で置き換えません。詳しくは[呼び寄せ仕様と実機確認範囲](../../../../docs/2026-10-16-body-recall.md)。

## v5からv6への変更（履歴）

- entrypointは`dogido:body_bridge_v6`、payloadは`body_request_v6`／`body_snapshot_v6`。旧版とは接続しません。
- snapshotへ`control(enabled,mode,followDistance,warpDistance)`を追加し、現在の実制御状態を公開します。
- 外部`applyState`は引き続きDIRECTだけです。ローカル操作で有効にしたFOLLOWでは、受理条件を満たした`MOVE_TO`を観察移動として受け付けます。
- FOLLOWのMOVE_TOは、持ち主と目的地が追従範囲内であること、身体から32ブロック以内・ロード済み・安全な位置であること、優先動作や実行中操作がないことを再確認します。
- 実到着後は`holding / inspection_dwell`、見回している間は`holding / follow_lookaround`です。0.7.7では前者を5秒とし、新しい観察MOVE_TOの拒否は到着後の間と追従直後に限ります。見回しは実yawを更新するため、視認判定もその向きを使います。到着結果は移動完了時に一度だけ返し、待機終了時に成功通知を重ねません。
- 観察移動は最大600tick。到着0.4ブロック以内で`SUCCEEDED/arrived`となり、危険回避・距離追従・呼び寄せ・モード変更・owner消失が優先されると中断します。同じIDの再送で実行期限を延ばしません。
- HTTPの姿勢・感覚はv3を維持し、制御状態・候補・操作結果には別の`body_autonomy` v1を使います。[現行HTTP契約](../../../../docs/adapter-api.md#body-autonomy-v2)。

## v4からv5への変更（履歴）

- entrypointは`dogido:body_bridge_v5`、payloadは`body_request_v5`／`body_snapshot_v5`。旧版を補完しない。
- `hearing(status,serverTick,sounds,truncated)`を追加。serverの実発音時に身体位置への到達を検査し、最大8件・300tickの有界記録として保持する。
- 各音は`sequence/serverTick/position/soundId/category/volume/pitch/sourceEntityId`を持つ。`pitch`がclientで決まる爆発音はnull。sourceEntityIdも実取得できる場合だけ保持する。再取得で発音tickと通番は更新しない。
- snapshotのJSONは8192文字・UTF-8 bytes以内。超過時は感覚候補を明示的に切り詰める。
- `sensors=BODY_VISION_AND_HEARING`。APIの公開`perception` packageへ視野・見通しを共通化し、adapterの身体観測にも同じ規則を使う。食料の見通し条件は維持する。
- HTTPは`body_observation` v3。一般モブ・環境・匂い・client実音の採取はadapterが身体姿勢を基準に行う。[観測移行仕様](../../../../docs/2026-10-13-body-observation-origin.md)。

## v3からv4への変更（履歴）

- entrypoint：`dogido:body_bridge_v4`、payload：`dogido_experiment:body_request_v4`／`body_snapshot_v4`
- `pose`に`viewYaw/viewPitch/ownerYaw/ownerPitch`を追加。`Snapshot.yaw`は描画上の身体向き。現モデルの視線は身体の顔向きでpitch=0。
- `vision(serverTick,rangeBlocks,horizontalFovDegrees,verticalFovDegrees,playerVisibility,reason)`を追加。同一tickの眼から距離16・水平120度・垂直90度・ownerの3点への見通しを判定する。
- `VISIBLE/in_view`、`NOT_VISIBLE/out_of_range|outside_fov|occluded`、`UNAVAILABLE/area_unloaded|body_downed|owner_unavailable|invalid_pose`。視覚だけ未取得でも正常な姿勢を保持する。
- `sensors=OWNER_VISION_ONLY`。一般モブ・嗅覚・構造物の走査は未実装。眼が壁内ならスペクテイター相当の壁内視点を使い、移動・ダメージ・食料の見通し条件には適用しない。
- HTTPは`body_observation` v2、姿勢の根拠は`pose_basis=server_pose`、視覚の根拠は`vision.basis=body_eye_raycast_fov`。幾何と材質の詳細は[視認仕様](../../../../docs/2026-10-13-body-vision-phase1.md)。

## v2からv3への変更（履歴）

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
- v2当時は初回bind／再読み込み／失効／復活後にDIRECT待機。v7では登録・旧保存読込み・復活の既定をFOLLOWへ変更（外部lease失効は停止を維持）
- v1の固定追従・探索・退避はローカル試験コマンドに限定

v1〜v10のクラスやJARをv11として扱いません。entrypointとprotocolの両方を照合します。旧entrypoint／payloadの受信口はありません。

## 公開APIとスレッド

パッケージは`dogido.lab.api`。`BodyBridge`と`BodyProtocol`のrecord／enumが公開契約です。recordの通信契約にはEntity／Worldが含まれません。共有の視認utilityはMinecraftのworldを入力に受けます。

```java
public interface BodyBridge {
    int protocolVersion();
    boolean available();
    Optional<BodyProtocol.Snapshot> snapshot();
    long snapshotAgeMillis();
    BodyProtocol.Submission applyState(BodyProtocol.StateRequest request);
    BodyProtocol.Submission requestAction(BodyProtocol.ActionRequest request);
    BodyProtocol.Submission requestRecall(BodyProtocol.RecallRequest request);
    Optional<BodyProtocol.RecallReceipt> recallReceipt(UUID actionId);
    BodyProtocol.Submission observeOwnerWork(BodyProtocol.OwnerWorkObservation observation);
}
```

adapterはprotocol 11の`cube-dogido-lab-*-body-api.jar`を**compileOnly**で参照します。0.7.9で安心度とowner接近目的を加えたAPIへ更新しました。現在の追従範囲で観察先を選ぶ修正を含むadapterを組み合わせます。APIクラスをadapterへ重複同梱しません。身体Mod不在でもadapterを読み込めるよう、任意連携クラスを分けます。

`available()`と送信はMinecraftのclient thread上で行います。`snapshot()`は不変データを読むだけで、HTTP workerから参照できます。受信後3秒を過ぎたsnapshotは空です。HTTP転送時はclient threadでsnapshotと経過msを続けて読み、3秒未満だけを現在値として送ります。受信登録と10client tickごとのポーリングは身体Modが所有します。

## 最小例

次のクラスをロードする前に、呼び出し側で`FabricLoader.getInstance().isModLoaded("dogido_experiment")`とv11 entrypointの存在を確認します。

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

`MOVE_TO/INSPECT`は`destination`へ身体の足元のワールド座標を入れ、`foodId=null`。`MOVE_TO/OWNER_NEAR`・`OWNER_FACE`は両方nullとし、現在ownerへの位置を身体側で選ぶ。`EAT`は直近の`food.targets[].id`を`foodId`へ入れ、`destination=null`。`REST`・`WAIT`・`CALL`は両方nullで、`movePurpose=INSPECT`（コンストラクタの既定値）です。行き先・食料を省略して別の行動へ補完する処理はありません。

`revision`と`actionSequence`は同じContext内で単調増加させます。更新されたsnapshotを基に送る際にも、古いモデル結果に新しいContextを付け直しません。

## 状態と一回操作

`StateRequest(context, revision, basedOnSnapshot, observedServerTick, leaseTicks, enabled, mode, retreat)`

- `enabled`は外部制御の有効期限付き許可です。`enabled=true`だけで探索・退避・休息を選びません。
- `mode=DIRECT`、`retreat=false`を使います。逃げる方向も`MOVE_TO`等で選びます。
- `enabled=false`、lease失効、世代変更で移動／休息指示を止めます。HP・環境接触・復活の物理ルールは継続します。
- ローカル試験コマンドはlease更新なしで試せます。次の外部状態が受理されると外部制御へ切り替わります。
- 0.6.1ではDIRECTの待機・休息中にブロックへ埋まると、同一次元のownerがいる場合に半径3block以内の空間へ通常移動で復帰します。未指示の再ロード直後も対象ですが、外部制御を有効にはしません。明示無効化・lease失効・MOVE_TO・CALL・DOWNED中は対象外です。snapshotのactivityはHOLDING/RESTINGを維持し、復帰移動中はreason=`idle_block_recovery`になります。

`ActionRequest(context, sequence, actionId, observedServerTick, validForTicks, action, destination, foodId, movePurpose)`

| action | 意味 | 実行結果 |
|---|---|---|
| `LOOK_AT` | 64block以内の点へその場で向く | 実回転後に`SUCCEEDED/facing_target`。最大100tick保持 |
| `MOVE_TO` | 身体から32ブロック以内の読み込み済み座標へ移動 | 到着で`SUCCEEDED/arrived`。途中の停止理由はsnapshotへ |
| `REST` | 現在地で休息する | 実際に静止して`SUCCEEDED/resting`。全回復を意味しない |
| `WAIT` | 現在地で待つ | 実際に静止して`SUCCEEDED/waiting`。休息回復なし |
| `EAT` | 指定された、現在届く食料一個を消費 | 消費後に`SUCCEEDED/food_consumed`。回復後HPはvitalsで照合 |
| `CALL` | 現在の持ち主の2block以内へ呼び寄せる。通常は正面、採掘中は横／後ろ | 実到着で一度だけ`SUCCEEDED/arrived`。直ちに解除し通常modeへ戻る |

DIRECTの移動は火・溶岩・敵の周辺・壁内を理由に別の行動へ変えません。生物との衝突・未ロード領域・ワールド境界はゲーム側で処理します。届かない食料へ自動移動せず、`food_out_of_reach`を返します。

`QUEUED`はclient通信への投入、`ACCEPTED`はserver受付です。`SUCCEEDED`は表に示した実行結果です。`INTERRUPTED`・`EXPIRED`・`REJECTED`と区別します。DIRECTでは新しい一回操作は実行中の操作を中断して置き換えます。FOLLOWの観察移動は実行中操作がある場合に拒否します。REST／WAITは完了後も、新しい行動・停止・失効まで指定した姿勢を続けます。

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
| `pose` | 同時刻の眼・owner位置・身体の視線・owner yaw/pitch |
| `vision` | owner視認の三値・理由・範囲・FOV。採取tickはsnapshotのtickと一致 |
| `hearing` | server実音の発音tick・通番・位置・種類・到達済記録・切り詰め |
| `control` | 現在のenabled・mode・追従距離・ワープ距離・実測安心度とowner視認。外部状態変更の許可を追加するものではない |
| `sensors` | `BODY_VISION_AND_HEARING`。周辺感覚のHTTP採取はadapterが担当 |

未登録／未ロード時のposition・vitalsはnullです。架空のゼロ座標・HP0へ置き換えません。`OWNER_UNAVAILABLE`と身体の`DOWNED`は別の状態です。

被弾の`lastDamage`は`sequence/serverTick/amount/cause`。amountは実際のHP減少で、攻撃者の位置を補作しません。sequenceと復活回数は個体へ保存します。再読み込み直後は過去の被弾詳細を現在観測として再生せず、lastDamageはnullです。

food候補は身体から8ブロック以内、読み込み済みで見通せる食料を近い順に最大16件です。候補IDは操作用で、会話へ直接投影するものではありません。未知／未ロード領域や壁の向こうの食料が存在しないという意味には使いません。倒れている／owner不在／身体未ロードでは`UNAVAILABLE`です。

食事直前に、観測の100tick以内・IDに対応する現物／種類・成熟状態・2ブロック以内・見通し・欠損HPを再検査します。植物消費は実ブロックを更新し、落下アイテムはstack一個を消費します。

## 接続できている範囲

実装済み：server身体制御、HP／時間ルール、食料観測と消費、任意Java entrypoint、C2S/S2C、自他姿勢・owner視認・身体の周辺感覚の既存adapter／Rust会話・診断への配送。

接続済み：FOLLOW待機中の発話・身体行動の一回選択、`body.inspect_target.v2`の実行capability、既存ゲームイベント経由の一件の接近指示、到着／中断結果とACK。選択はRust側の会話モデル、再照合と着地点はadapter、実移動と安全制御は身体Modが担当します。食事・休息のモデル選択と、敵発見を契機に持ち主へ高速帰還する経路は未接続です。身体ModはHTTPサーバー、モデル、TTS、会話セッションを起動しません。

実機確認待ち：ゲーム内通信、描画・補間、食事／被弾、保存再開、復活・次元移動、専用サーバー。自動検査はこの実機確認を代替しません。
