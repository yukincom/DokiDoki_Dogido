# Rust本線への接続手順

2026-10-12追記: P1を本線mainへ統合し、身体0.4.0と対応adapterの導入・起動・Minecraft接続を確認しました。[本線導入記録と次の実機操作](../../../../docs/2026-10-12-body-p1-install.md)、[現行の連携境界](../../../../dogido-rust/docs/body-integration-boundary.md)を参照してください。以下は0.3.0時点の履歴です。

2026-10-11追記: 専用ブランチで身体ソースのGit管理を開始しました。観測地点の変更、ファイル分割、起動と合流の方針は[身体統合の作業計画](../../../../docs/2026-10-11-body-integration-plan.md)を先に参照してください。以下の本線対応表と検証件数は2026-10-06時点の記録です。

状態: **2026-10-06時点の接続計画を保存した履歴文書。**

2026-10-11以降の現行計画・実装状態は[身体統合計画](../../../../docs/2026-10-11-body-integration-plan.md)を参照。P1でBody API v3／0.4.0と自他位置の読取り接続を追加した。以下のv2・未実施表記は当時の基準であり、再実装の指示ではない。

本線の確認基準はmainの`9fff5c890195457175f676a423f49706c7dfe1af`と、その時点の作業中ファイルです。本線は更新中なので、接続開始時に下記の入口を読み直します。本書の記録をRust化の試験完了とは扱いません。

## 読む順番

1. 本線の[AGENTS.md](../../../../AGENTS.md)、[連携構成](../../../../docs/integration-architecture.md)、[Rust本体README](../../../../dogido-rust/README.md)
2. 本線側の[身体との連携境界](../../../../dogido-rust/docs/body-integration-boundary.md)
3. 身体の[README](../README.md)と[Body API v2](body-api.md)
4. 本線の[移行後レビュー記録](../../../../docs/rust-review-followup-2026-10-04.md)と[実機チェック](../../../../dogido-rust/manual-dialogue-check.md)

現在の本体はRustです。旧Python本体・比較oracleや、旧worktreeの起動手順を再導入しません。起動設定は本線READMEに従い、通常ポートの既定は5055です。古い5056固定の独立Rust起動を身体接続用に復活させません。

## 0.2.0から変わった分担

今回の身体実験は、**モデルが移動・休息・食事等の次の行動を選び、その結果を観測する**方針です。モデルの接続自体はまだありません。

| 担当 | 実装・接続方針 |
|---|---|
| モデル／本線の身体用処理 | 次の一手と対象を選ぶ。身体の観測・実行結果を次の判断へ渡す。今後接続する |
| 身体Mod | 指定移動の実行、実位置・HP・食料の観測、食料一個の消費、ゲームモードに従う損耗、休息回復、倒れる／復活の物理ルール |
| APIとadapter | 所有者、世代、観測の古さ、操作の外形、読み込み範囲、実行結果・再送を照合する |
| 本線の既存会話・戦闘・川柳・保存 | 現行の所有者と規則を維持する。身体の損耗や敵の接近をプレイヤーの被弾・戦闘へ自動変換しない |

HPが低いから休む、食料を見たから食べる、敵が近いから後方へ退避する、という行動選択はDIRECTではコードに入れていません。身体は落下・溺れ無効、ブロック通過可能です。火・溶岩や長い壁内滞在にはダメージがあり、指定先を危険回避用の別目標へ差し替えません。

旧`FOLLOW/EXPLORE/HOLD`と退避優先の制御器はローカル比較用に残っています。外部からは`DIRECT/retreat=false`のみ受理します。旧制御器をモデル接続時の既定へ戻さないでください。

この身体用の行動選択は、通常会話のread plannerや`select_sword`の権限を拡張する変更ではありません。別の身体用入出力として接続し、一般会話モデルへMinecraftの汎用操作一覧を渡さない構成にします。

## 接続するコードの対応表

以下の本線側変更は**未実施**です。パスはリポジトリルートからの相対表記です。

| 入口 | 現在の実装 | 身体接続で必要な仕事 |
|---|---|---|
| `adapter/minecraft-fabric/src/main/java/dogido/fabric/DogidoClientAdapter.java` | 接続先変更でHTTP sessionを破棄し、次元変更ではsessionを保ってworldEpochを更新する | 任意BodyBridgeの検出、client threadでの操作、snapshot採取、身体Context変更時の保留操作破棄 |
| `adapter/minecraft-fabric/src/main/java/dogido/fabric/DogidoEventClient.java` | HTTP送信、未ackの操作結果の再送、session/worldEpochが変わった応答の破棄 | 身体専用の観測・要求・結果を追加。HTTP終了後にも世代を照合してclientへ渡す |
| `adapter/minecraft-fabric/src/main/java/dogido/fabric/DogidoCommandProtocol.java` | 現在は`select_hotbar`のみを解釈し、未知commandは実行しない | 身体用commandの型・検査・結果を追加。SelectHotbarのpayloadへ流用しない |
| `dogido-rust/src/server/contracts.rs` / `server/runtime.rs` | SessionRequestのcapabilities/execution_capabilitiesを登録する。player名はあるがowner UUID専用欄はない | API v2が実際に利用可能なときだけ身体capabilityを宣言。HTTP sessionと実owner UUIDの結び付け・照合方法を定義する |
| `dogido-rust/src/events/models.rs` / `events/mod.rs` | イベントを型として読み検証する。未知extraの保持だけでは専用型・consumerにならない | 身体観測、身体commandと実行結果の型、上限、enum、UUID・世代・時刻の検証を揃える |
| `dogido-rust/src/dialogue/observe.rs` | 観測の順序を照合し、game-event応答の`state`と`outputs`はnull。commands/ackは独立 | 身体用処理へ検証済み観測を渡し、commandsと結果ackの往復を接続する |
| `dogido-rust/src/dialogue/mod.rs` | 完全なゲーム観測の最新値を保持し、操作結果だけの部分イベントで空にしない | 身体snapshotと既存player観測を独立して保持し、片方の欠損をもう片方へ代入しない |
| `dogido-rust/src/assist/selection.rs` | 明示依頼によるSelectHotbarの操作・結果を管理する | 既存支援は維持し、身体行動の台帳や判断を別の専用モジュールへ置く |

`body_observation`等の**HTTP上の項目名・capability名はまだ未確定**です。確定済みなのは身体Java APIの`BodyProtocol`です。HTTP形を決めたときに本線の`docs/event-schema.md`・`docs/adapter-api.md`と両側のfixtureを同時に更新します。

旧資料の`response.state.mode`から戦闘退避を導く案は、現在のRust応答には使えません。身体の観測と行動は専用経路で結びます。

## 世代と結果の往復

1. adapterが身体Modとprotocol 2の利用可否を確認し、snapshotを読む。Modがない／未ロードなら欠損と理由を送る。
2. 本線がHTTP session・owner・身体Context・server tickを検証し、身体用の観測として保持する。
3. 身体用のモデル処理が観測から一手を選び、本線が型と対象を検証する。判断開始時のContextと観測tickを保持する。
4. adapterがHTTP session/worldEpochを再照合し、client thread上で`applyState`と`requestAction`を呼ぶ。身体serverが実際の送信者と現物を再検査する。
5. `QUEUED → ACCEPTED → SUCCEEDED/REJECTED/INTERRUPTED/EXPIRED`を別々に扱い、結果を本線へ返す。HTTP結果のackと、身体の操作結果を分ける。
6. モデルへ次の実測と結果を渡す。受理しただけの行動を、到着・回復・食事完了として記録しない。

`REST`の成功は静止して休息に入った意味です。回復量は後続vitalsで読みます。移動が塞がった場合もsnapshotのactivity/reasonと位置で判断します。

- **HTTP session**、**adapter worldEpoch**、**Body Context(ownerId/bodyId/epoch/dimension)**は別の識別子です。同じ文字列で代用しません。
- 古いモデル応答に最新Contextを付け直しません。新しい世代では観測から取り直します。
- lease更新で同じ状態を保つことと、新しい行動を出すことは別です。外部制御が失効すると移動／休息は停止し、物理的な損耗・復活処理は続きます。
- 一操作の再送は同じID・sequence・payloadで行います。身体は直近32件を記憶します。この上限を越えた再送や古い世代への結果配送はadapter／本線でも止めます。
- snapshotには直近のactionResultだけを載せます。新しい操作や拒否で表示が置き換わるため、adapterは受領した結果を自分の未ack台帳へ保存します。必要な結果を受ける前に次の操作を連打しません。HTTP側の結果保証は今後の実装・試験対象です。
- leaseと受付期限はserver tickで計算します。client snapshotの受信後3秒の失効とは別です。通信が遅ければ古い一手を棄却し、再観測します。

## 観測を混ぜない

身体のposition・health・DOWNED・respawnCountは、プレイヤーの位置・HP・`player_died`とは別です。身体のfoodは8ブロック以内の読み込み済み領域から、見通せる対応食料を最大16件返します。未視認・未対応の食料が存在しないことは表しません。

身体からの一般モブ視認・嗅覚・構造物センサーは未実装です。`sensors=NOT_IMPLEMENTED`を「敵がいない」「匂いがない」と読み替えません。現在の本線スメルは`smell_observation`で、廃止済み`zombie_scent_clues`へ接続しません。身体の正確な座標やfood IDを、匂いの会話入力へ混入させません。

発話、音声の取消・配送、実再生completed後の履歴確定は本線が担当します。身体Modにモデル・HTTP session・TTS・会話履歴は作りません。食料消費やHP変化の診断と、ユーザーへの発話を同じ成功記録にしません。

## 接続の順番と確認項目

本線Rust化の検証を継続し、その到達点を記録してから接続作業へ進みます。

### 1. 身体単体（本線接続前）

- [x] `./gradlew --offline test build`成功、39テスト、失敗・skipなし。時間ルール、DIRECT指定、要求検証、再送、移動の幾何を検査。
- [x] Minecraft 1.21.11のAPIに対するModのコンパイル、API専用JARの生成。
- [ ] ゲーム内でDIRECTの待機・移動・休息・食事、既存モデルの描画と手の動きを確認。
- [ ] サバイバル／アドベンチャーで被弾し、クリエイティブ／スペクテイターで通常ダメージが無効になること。
- [ ] 葉・壁・火・溶岩・水を区別し、壁内損耗と休息回復が指定した時間で進むこと。
- [ ] 持ち主の右クリック給餌、落下物と植物の一個消費、消費効果・容器、満タン／倒れている時の拒否。
- [ ] HP0から待機し、復活時点の持ち主の現在座標へ一体だけ復活。HPと世代、所有関係、経験値・通常ドロップなしを照合。
- [ ] 保存再開、owner切断／死亡／次元変更、身体未ロード・読み込み済みだがtick対象外、転送失敗時の待機と再開。

ダメージを受ける仕組みは実装しましたが、各敵モブのAIが身体を攻撃対象として選ぶことは未確認です。専用の敵ターゲットAIは追加していません。

### 2. adapterとRustの型・模擬往復

- [ ] 身体なし／版違いでも既存adapter・2D表示・会話が起動する。
- [ ] compileOnlyのAPI v2を参照し、APIクラスをadapterへ重複同梱しない。
- [ ] playerと身体で座標・HPが異なるfixtureを使い、両方が独立して保持される。
- [ ] owner不一致、古いepoch／sequence／tick、部分観測、未知enum、過大payloadを拒否する。
- [ ] 遅いモデル結果、HTTP再送、操作ID重複、結果ack喪失、再接続・次元変更後の古い完了を試す。
- [ ] EATの再送で消費が一度、ACCEPTEDの再送で移動・休息が再初期化されない。
- [ ] `state=null`の現行game-event応答から身体commandが正しい専用経路へ届く。

### 3. モデルとゲームの結合

- [ ] 実モデルが休息／食事／移動を選び、理由をコードの自動行動で代行していないことを確認。
- [ ] 選択した一手、基にした観測、実行結果、次のHP・位置を同じ操作IDで追える。
- [ ] 主人の操作・会話・戦闘・川柳・音声に影響がないか、本線の実機チェックと併せて確認。
- [ ] 統合サーバーと専用サーバーを分けて確認。描画・通信・体感・モデルの判断品質も別々に記録。

本線レビュー記録には2026-10-05のRust全587件・Clippyと模擬HTTP試験の成功が記載されています。これは本作業での再実行結果でも、身体との接続試験結果でもありません。過去の未通過項目は同記録の後続修正まで読み、古い残件をそのまま持ち越さないでください。

## 配布と戻し方

成果物は`build/libs/`の0.3.0 Mod／body-api／sources JARです。ゲームへの導入は別工程です。モデル・テクスチャの元データは更新していません。

2026-10-06の引き渡し時点では本実験ディレクトリはGit追跡対象外でした。2026-10-11から専用ブランチでソース・テスト・Gradle wrapper・モデル元データを追跡します。ビルド成果物と復旧アーカイブはGit対象外です。今回の更新前ソースと成果物の検査記録は`.recovery/body-v03-20261005/`へ保存します。戻す際はゲームを終了し、この変更以降の編集を保存したうえで更新前ファイルと旧Modを選び直します。ワールド保存の互換性は実機確認待ちです。
