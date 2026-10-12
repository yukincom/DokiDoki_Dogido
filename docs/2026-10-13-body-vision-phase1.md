# 身体の姿勢・プレイヤー視認を既存会話へ通す計画

状態: **2026-10-13、第一段階を実装し、自動検査済み。本線mainへ反映。実機には未反映。**

## 目的と範囲

ドギド自身の位置・顔の向き・目を基準に、所有者であるプレイヤーを視認できるか測り、既存の通常会話と診断へ届ける。位置のサーバー実測と、視野・見通しを検査した視認を別の根拠として表す。

第一段階は、姿勢情報の拡張、所有者一人の視認、既存配送・会話・診断への接続まで。周辺モブ・ブロックの一覧、見失った対象の長期記憶、嗅覚・聴覚の移行、モデルの移動選択、独立した頭の回転は第二段階以降。

### 既存実装と参考元

| 既存の担当 | 再利用するもの |
|---|---|
| 身体 `BodyPoseObservation` | 同じserver tickの身体の眼・owner位置。向きを追加する |
| 身体 `BodyFood` | 眼から対象へのraycast。計算を共通化し、食料の到達・消費条件は従来の担当に残す |
| 身体 `BodySpace` | ワールド境界と読み込み済みchunkの確認。観測のためにchunkを生成しない |
| 身体 `BodyBridge` / `BodyClientBridge` | 不変snapshot、既存Fabric通信、10client tickごとの取得、受信経過時間 |
| adapter `body/` / `EventObservationCapture` | 版・owner・次元・鮮度確認、既存イベントへの添付 |
| Rust `body/` / `dialogue/body_runtime` | 世代と順序、現在／前回、会話の不変コピーと発話診断 |
| Rustのカタログ | 第二段階の種類IDから日本語名・一般知識への変換 |
| 身体 `MOVE_TO` | 後続の移動出力比較でも最終的な絶対座標の実行口にする |

参考はmc_aiplayerの固定commit `a029fa6a3760fd0f83834c104051b041d986da60`。
[PerceptionCollector](https://github.com/zoyluoblue/mc_aiplayer/blob/a029fa6a3760fd0f83834c104051b041d986da60/src/main/java/io/github/zoyluo/aibot/perception/PerceptionCollector.java)の採取と、[ObservableWorldQuery](https://github.com/zoyluoblue/mc_aiplayer/blob/a029fa6a3760fd0f83834c104051b041d986da60/src/main/java/io/github/zoyluo/aibot/mode/ObservableWorldQuery.java)の可視判定を分ける設計を参考にする。外部Bot・LLM基盤は導入しない。参照先の特権による遮蔽迂回・自動採掘・資源優先を取り込まない。参照先には視野角の制限がないため、下記のFOVはドギド側で定義する追加仕様。

adapterの既存`ObservationVisibility`はplayer/client専用で、3点への見通しと隣接窓補正を行い、FOVを検査しない。身体側は既存`BodyFood`のserver実測を共通化する。現在のplayerの種類保持表や短期敵保持を、身体の現在視認として流用しない。`look_target`はプレイヤーの指差しとして保持する。

## 第一段階の契約

### 姿勢

- 座標はblock、yaw/pitchは度。yaw=0は南、+90は西。yawはHTTP境界で`[-180,180)`へ正規化する。pitchは`[-90,90]`で正が下。
- `self_pose.position / eye_position / yaw`は既存の身体基準。`yaw`は描画上の身体の向き。
- `self_pose.view_yaw / view_pitch`を追加する。現在の箱型モデルは独立した頭・上下の顔回転を持たないため、視線は身体の実際の顔の向き、pitch=0。存在しない頭の向きを生成しない。
- `player_pose.position / yaw / pitch`はownerの足元と向き。プレイヤーが振り向いてもドギドの視線は変わらない。
- これらと視認結果は同じserver thread、同じsnapshotのtickで採る。

### 所有者の視認

- 初期の固定規則: 距離16block、水平FOV120度、垂直FOV90度。観測にもこの範囲を添える。将来調整するときは規則・試験・文書を同時更新する。
- 対象の高さ25%・55%・85%の3点を検査する。各点について眼からの距離、視線基準の水平・垂直角、同じ線分の遮蔽を判定する。一つの点が全条件を満たせば可視。
- 距離内の点がなければ`not_visible/out_of_range`、距離とFOVを満たす点がなければ`not_visible/outside_fov`、対象点がすべて遮られていれば`not_visible/occluded`。
- 対象への線分に未ロード領域があり、ほかに可視と確認できた点がなければ`unavailable/area_unloaded`。身体DOWNED・owner不在等も可視／不可視へ推測しない。
- 視覚はMinecraftの形状raycastを使う。第一段階はCOLLIDER、液体は遮蔽しない。Minecraftのガラス・色付きガラス・ガラス板・遮光ガラスを視線の通る材質として扱い、通常の壁・葉・部分ブロックは形状に従う。画素・暗さ・透明テクスチャの穴を解析する視覚ではない。
- すり抜け中は、ユーザー指定のスペクテイター相当の壁内視点を使う。眼から線分上で連続する固体区間を透過し、空間へ抜けた先の別の壁は遮蔽する。厚い壁と部分ブロックの形状も考慮する。距離・FOVは身体の実際の眼を基準に保つ。これは描画ピクセルの再現ではなく、身体の視認規則。
- 身体の壁すり抜け、サバイバル／アドベンチャー時の既存の壁内損耗、クリエイティブ／スペクテイター時のダメージ無効を維持する。視覚のCOLLIDERを移動制限へ流用しない。
- 食料は既存のOUTLINE / FluidHandling.NONEと対象ブロックへの命中許可を保つ。視覚用のガラス透過を食料取得条件へ混ぜない。
- 第一段階はowner一人、最大3本の視線だけ。一般の周辺走査や毎tickの全範囲探索を追加しない。

### 形式と配送

Java Body APIをv4、身体Modを0.5.0、HTTP内の`body_observation`をv2へ更新する。entrypointとFabric payloadもv4に揃え、APIクラスをadapterへ重複同梱しない。

HTTP観測の外形は`version, status, age_ms, pose_basis, sample`。`pose_basis`は`server_pose`で、姿勢だけの根拠。`sample`は既存の`context / server_tick / snapshot_revision / self_pose / player_pose`に`vision`を加える。

`vision`は以下を必須にする。

| フィールド | 意味 |
|---|---|
| `basis` | `body_eye_raycast_fov`。身体の眼、距離、視野、遮蔽を使った判定 |
| `range_blocks` | 眼から対象点までの上限 |
| `horizontal_fov_degrees` / `vertical_fov_degrees` | 水平・垂直の全視野角 |
| `player_visibility` | `visible / not_visible / unavailable` |
| `reason` | 下記の閉じた理由 |

理由は`visible: in_view`、`not_visible: out_of_range / outside_fov / occluded`、`unavailable: area_unloaded / body_downed / owner_unavailable / invalid_pose`。不正な組合せ・値を境界で拒否する。視覚が未取得でも正常な姿勢は渡せる。観測全体の未取得は既存の`status=unavailable,reason`だけを送り、座標を0で埋めない。

Body APIのvisionには採取tickを持たせ、adapterでsnapshotのtickと一致することを確かめる。HTTPのvisionは同じ`sample.server_tick`に結び付く。以前の視界に新しい姿勢の時刻を付けない。

配送は既存の `BodyBridge → adapter body/ → game-events body_observation → Rust body/ → world_context.observations.body_observation`。新しいHTTP endpoint、イベント種別、別モデル呼出し、別の永続化は作らない。HTTP session、adapter worldEpoch、身体Contextはそれぞれ照合する。

### 会話と診断

- 通常会話の`current / previous`へ拡張姿勢とvisionを含める。既存の座標規約・鮮度と、周辺情報の`observer=player`を維持する。
- 現在のsession診断と、生成へ使った`body_observation_input`に同じ観測を残す。生成後の現在値で上書きしない。
- 位置理解の試験には、引き続き生座標とyawを渡す。コードで計算した前後左右の正解を入力へ混ぜない。視認の成否と、LLMによる位置理解は別に採点する。
- 身体観測のプロンプトは、可視性のフィールドと位置の根拠の違いを説明する最小変更を2026-10-13にユーザー承認済み。先頭の位置・前後左右・previousの説明を保ち、末尾に本書の「承認済み文面」を加える。

## 第二段階以降

1. ownerで観測基準と配送を確認後、限定した周辺モブ・ブロックへ拡張する。
2. 種類はregistry IDをそのまま返し、Rustの既存表示・知識変換を使う。個体識別と世界座標を保持し、player用の敵対判定を複製しない。
3. 件数・走査範囲・ray回数・シリアライズ後の長さを上限付きにする。現行Fabric payload上限8192文字を超えない。省略と観測済み0件、未取得を区別する。
4. 見失った対象の記憶、身体の音・匂い、独立した視線制御は、それぞれ取得元と保持期限を決めて追加する。
5. 移動出力は既存[比較案](2026-10-11-body-integration-plan.md#移動出力の比較案2026-10-13未実装)で検査し、絶対／相対の意味を固定して既存MOVE_TOへ渡す。

## 実装の順序・検査

1. 計画と共有JSON契約を固定する。復旧基準はmainの`ace5b5b`。作業開始時の既存変更を別途保存し、今回の変更へ含めない。
2. 身体Mod: 姿勢、共通raycast、視認判定、API v4、snapshot接続を実装。幾何と判定、Minecraftの形状raycastを検査する。
3. adapter: API v4を読み、共有HTTP v2へ投影。版違い、欠損、不正値、tick不一致、未取得を検査する。
4. Rust: 型・validation・projectionを更新。通常会話と診断、現在／前回、重複・逆順・世代・鮮度を検査する。
5. 変更対象の検査後にRust全targetとClippy、両Modのtest/buildを行い、成果物とAPIクラスの非重複を確認する。
6. 全差分と既存変更の保持を確認し、本線へ統合してローカルcheckpointを作る。pushはしない。
7. ゲームへの配置・再起動と実モデルの確認は、現在の稼働状態を確認して別途記録する。自動検査だけを実視認・モデル理解の証明にしない。

### 受け入れ条件

| 条件 | 合格基準 |
|---|---|
| 二者が離れ、片方だけが対象を見通せる | ドギドの目からの結果になる |
| playerだけ回転 / bodyだけ回転 | 前者でbody視界は変わらず、後者でFOV判定が変わる |
| 前後左右・上下・視野境界・距離境界 | Minecraft座標規約に一致。角度の折返しを含む |
| 壁・壁内視点・部分遮蔽・ガラス・水・葉 | 文書化した形状・透過規則に一致。壁内では外を見られ、別の壁は遮蔽する |
| 未ロード・DOWNED・owner不在 | 理由付き未取得。正常なposeとは別に扱う |
| 同じsnapshot再送・逆順・世代変更 | 観測の鮮度を延ばさず、比較対象を混ぜない |
| 会話生成後に新しい観測が届く | 発話診断は生成時の不変コピーを保つ |
| API非導入・旧版・不正値 | 身体観測の理由が分かり、代用座標を作らない |
| 食料・既存player観測 | 既存の主体・採取・消費条件の回帰検査に通る |

## 実装・検証結果

### 変更した担当

- 身体: `BodyPoseObservation`で視線とownerの向きを採り、`BodyVision`で最大3点の距離・FOV・遮蔽を測る。`BodyVisibility`へ既存の食料raycastを共通化し、用途ごとの規則を保持する。`BodyVisionOrigin`は同じMinecraft raycastの形状から眼を含む連続区間だけを除く。別のworld走査は増やさない。
- 境界: Java API／entrypoint／payloadはv4。adapterの`BodyObservationJson`が同時刻・形式・値を照合し、HTTP観測v2へ写す。共有fixtureは`dogido-rust/fixtures/body-observation-v2.json`。
- Rust: `body/contracts.rs`で拡張姿勢・三値・理由・範囲を検証し、`body/state.rs`で現在／前回へ投影。既存`dialogue/body_runtime.rs`から通常会話、session診断、`body_observation_input`へ同じ不変コピーを渡す。
- 指示文: 本書末尾のユーザー承認済み文面だけを共有`companion_prompts.json`へ反映。別の生成呼出しや移動判断は追加していない。

### 自動検査

| 検査 | 結果 | 検証できた範囲 |
|---|---|---|
| Rust `cargo.sh test --all-targets --locked --offline` | 683成功、失敗0 | 既存回帰、HTTP v2拒否条件、現在／前回、鮮度と世代、通常会話のrequestと生成時診断の不変コピー |
| Rust `cargo.sh clippy --all-targets --locked --offline -- -D warnings` | 成功、指摘0 | 全targetの静的検査 |
| 身体 `./gradlew --offline test build` | 50成功、失敗・error・skip 0 | 既存39件とFOV／実ブロック形状11件。壁・葉・ガラス・水・slab、壁内視点、食料の回帰、未ロード時の読み取り抑止 |
| adapter `./gradlew --offline test build` | 140成功、失敗・error・skip 0 | Java/Rust共通fixture、角度正規化、tick不一致、旧API版・Mod不在、視覚だけ未取得、独立コピー |
| Rust release・両Mod成果物 | 生成成功 | 新しい指示文・型・APIを含むビルド。adapterへBody APIクラスを同梱しないことを確認 |

Minecraftの形状テストには、[Fabric公式のFabric Loader JUnit](https://docs.fabricmc.net/develop/automatic-testing)を使用する。実際のMinecraftのブロック形状・raycastを小さい`BlockView`で検査しており、Minecraftのワールド起動や画面の確認とは別である。テスト実行ログはbuild配下へ置く。

独立したコードレビューで、slab上面に平行に接した眼を壁内と誤認する境界を発見して修正した。現在は眼が実形状内にある場合だけ壁内視点を有効化する。厚い壁、途中の空間、別の壁、部分形状、逆向きrayを検査済み。身体の移動・HP・ダメージ条件には変更を加えていない。

### 成果物と反映の境界

- 本線: `/Users/yukin_co/Documents/DokiDoki-Dogido`のmain。計画・ソース・テスト・文書をここへ反映する。作業前からあった15件の追跡ファイル変更と17件の未追跡ファイルは今回のコミットへ含めない。
- 身体: `animation/cube_dogido/cube-dogido-lab/build/libs/cube-dogido-lab-0.5.0.jar`。API jarはコンパイル用で、ゲームへ入れない。
- adapter: `adapter/minecraft-fabric/build/libs/dogido-fabric-client-0.1.0.jar`、build識別子`2026-10-13-body-vision-p1`。
- 復旧用差分・検査ログ・生成したRust releaseは`.dogido_tools/checkpoints/2026-10-13-body-vision-phase1/`へ保管する。現在使われているRust実行ファイルとゲームのMod配置は変更していない。
- 新版はMod・adapter・Rustを同時に導入する。HTTP観測v1はv2に補完しないので、片方だけ差し替えて運用しない。

### 未確認と次の実機確認

1. 稼働中のMinecraftとサーバーappの版・PID・設定場所を確認し、停止してから対応する3成果物を配置する。
2. 同じワールドで、開けた正面／背面・playerだけ回転・bodyだけ回転、壁／slab／ガラス、壁内から外／別の壁を挟んだ配置を確認する。session診断の現在値と、質問に使った`body_observation_input`を並べて読む。
3. サバイバル／アドベンチャーの壁内損耗と、クリエイティブ／スペクテイターのダメージ無効が従来どおりか確認する。
4. 未ロード・DOWNED・owner切断・次元変更の観測が理由付き未取得へ遷移することを確認する。
5. 実モデルの「今見える？」という返答は別に評価する。今回の自動検査を位置理解・前後左右回答の改善や実視認の証明とはしない。

### 承認済み文面

既存body_observation指示の先頭4行を保ち、末尾を以下に更新する。

```text
body_observation 自体が unavailable のときは、今の位置は分からへんものとして扱ってな。
今{player}が見えるかは current.vision.player_visibility を見てな。visible は見える、not_visible は見えへん、unavailable は分からへん、やで。
座標が分かることと、目で見えてることは別やで。
```
