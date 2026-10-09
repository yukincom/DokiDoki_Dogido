# 2026-10-08 Fabricアダプター分割計画

更新: **2026-10-09 — P1の基準整理に着手**。

状態: **P1の関連検証完了。P2の匂い規則分離へ進む**。この文書の作成・細分化ではコード変更、テスト、ビルド、Minecraftへの導入を行っていない。

対象は `adapter/minecraft-fabric/`。`DogidoClientAdapter.java` に集まった観測・状態管理・イベント組み立てを責務ごとに分け、既存の補助クラスも対応するパッケージへ整理する。イベント内容と実行順序を保ったまま、次の修正で読む範囲を小さくする。

## 1. 現状と前提

2026-10-08の作業中ソースでは `DogidoClientAdapter.java` は5,291行。初期化、毎tickの進行、匂い、音、敵・死亡結果、周辺環境、プレイヤー、JSON生成、限定コマンド実行、HUDへの接続を一つのクラスが担当している。

すでに `SmellPolicy`、`ZombieScentPolicy`、`SoundObservationPolicy`、`NearbyComposition`、`PortalObservationTracker`、`WorldObservationFrame`、HTTPクライアント、表示状態などは別クラスになっている。これらを移動・再利用し、同じ機能の別経路を増やさない。

基準にするのはHEADだけでなく、現在の未コミット変更を含む作業中ソース。直前の匂いの方位撤去はP1で変更範囲のJava/Rustテストとコンパイルを確認済み。全体テスト・通しビルドはP17で行う。[スメルバトルv2検証記録](2026-10-08-smell-v2-test-results.md)の成功件数を現在の検証結果として転用しない。

仕様の確認先:

- [イベントスキーマ](event-schema.md)・[アダプターAPI](adapter-api.md)
- [匂い仕様](smell-policy.md)・[周辺環境への気づき](2026-10-08-surroundings-notice.md)
- [音源の同定](sound-identity-plan.md)・[視線先観測](look-target-observation-plan.md)・[村人文脈](villager-context-plan.md)
- [完成度の方針](companion-maturity.md)・[現在の実装ガイド](../AGENTS.md)

旧Pythonの[再配置計画](server-reorg-and-workshop-order.md)は終了済み。そこで使った移行用re-exportを今回のJava分割へ機械的に持ち込まない。[Rust対話モジュール分割案](2026-10-05-Grok4.5-dialogue-mod-split-proposal.md)とも対象を分ける。

## 2. 完成時の構成

以下は責務と配置の目標。新しいクラス名は仮称とし、段階ごとに必要なものだけ作る。空のフォルダや共通基盤を先に大量作成しない。

```text
adapter/minecraft-fabric/src/main/java/dogido/fabric/
├── DogidoClientAdapter.java       初期化・tick進行・配線・ライフサイクル
├── DogidoConfig.java              設定読込と既存の既定値
├── DogidoBuildInfo.java           ビルド情報
├── observation/
│   ├── WorldObservationFrame.java tick内の遅延取得とコピー
│   ├── NearbyComposition.java     近傍集計・範囲追跡の共用アルゴリズム
│   ├── MobEnvironmentObservation.java 共用のモブ環境値
│   ├── …                         複数の観測で使う型・小さな幾何処理
│   ├── smell/
│   │   ├── SmellObserver.java     近接源収集・キャッシュ・雨上がり期間
│   │   ├── SmellPolicy.java       既存スメルバトル
│   │   └── ZombieScentPolicy.java 既存ゾンビ候補規則
│   ├── player/                   player・乗車・inventory・視線先・respawn
│   ├── world/                    地形・光・天候・構造物・ブロック・portal
│   ├── sound/                    packet/再生音の記録・期限・聴覚観測
│   └── mobs/                     共用の敵対判別・友好モブ・敵追跡・死亡・boss
├── protocol/
│   ├── DogidoEventClient.java     HTTP・session・sequence・応答処理
│   ├── DogidoCommandProtocol.java command解析・重複防止・結果ack
│   └── …                         イベントJSONと表示受信データの組み立て
├── commands/                     許可済みhotbar操作のクライアント実行
├── display/                      両HUD・表示状態・配置・瞬き等
└── mixin/                        現在のMixin入口を維持
```

`src/test/java/dogido/fabric/` も製品側と対応するパッケージへ移す。`OverheadObservation` と `PortalObservationTracker` は `observation/world`、`PassiveMobSelection` は `observation/mobs`、`SoundObservationPolicy` は `observation/sound` に置く。

最終的に `DogidoClientAdapter` に残すのは、Fabricイベント登録、共通時計、接続・world変更の検出、観測呼び出し順、送信タイミング、コマンド・表示への配線。具体的な匂い計算、ブロック探索、敵の分類、音の同定、個々のJSONフィールド生成は各担当へ移す。行数だけを完了基準にしない。

## 3. 分割中も保つ境界

1. **観測と本体判断の分担を変えない。** Fabric側の既存観測・追跡を整理する。Rust側の会話、戦況判断、保存、LLM経路は変更しない。既存の `combat_active_hint` 等も意味を変えない。
2. **送受信契約を変えない。** endpoint、schema、キー、型、省略と明示値、配列順、イベント名、session、sequence、時刻、署名、送信間隔を維持する。匂い方位や撤去済みの互換入力を復活させない。
3. **一つの状態は一つの担当だけが更新する。** map、queue、最終観測時刻、雨上がり期間などをメソッドと一緒に移す。別の担当には必要な値または読み取り結果だけを渡す。
4. **呼び出し順と更新回数を変えない。** シュルカー判別のように、見た目は問い合わせでも内部状態を更新する処理がある。純粋関数だと決めつけて先読み・並べ替え・複数回実行しない。
5. **tick内の取得頻度を維持する。** 現在の `WorldObservationFrame` は必要になるまでworld/blocksを作らず、最初の結果を同じtick内で共有し、読み出し時にコピーする。全イベントで再探索したり、tickを越えて保持したりしない。近傍種類の取得は現在のsnapshot送信条件を守る。
6. **Minecraftのスレッド境界を維持する。** サーバー死亡イベントのqueue、構造物照会、HTTP応答、`client.execute`、Mixin入口を一括して別executorへ移さない。分割と並行処理の変更を混ぜない。
7. **可視性は必要な境界に限る。** パッケージ移動のために設定・内部record・全メソッドを一括で `public` にしない。外へ渡す値型と入口だけを公開し、コレクション等の可変内部状態は隠す。

Mixinが呼ぶ `DogidoClientAdapter` の静的入口と `fabric.mod.json` のentrypointは当面そのまま残し、新しい担当へ委譲する。この入口は外部接続のために残すもので、旧実装を並行維持するものではない。

## 4. 状態の持ち主と依存関係

### 主な状態の移動先

| 移動先 | 持つ状態・既存処理 | 他の担当との接続 |
|---|---|---|
| rootの進行役 | 共通tick、接続/world参照、送信周期・署名、観測順 | 各担当を順番に呼び、必要な観測を渡す |
| smell | block候補cache、現在の匂い、scan tick、前の局所天候、雨上がり期限 | player/worldから現在値を受け取り、匂い結果を返す |
| player | player・vehicle・inventory・視線先、睡眠から得たrespawn根拠 | worldへ拠点距離計算に必要な値を渡す |
| world | 構造物cache、portal追跡、近傍ブロックの範囲状態、破壊履歴等 | playerのrespawn根拠、sound由来の天候・予兆観測を受け取る |
| sound | 敵対音・環境音の記録、tick期限と実時間、雨音・雷・予兆等の時刻 | 共用のモブ判別を利用し、worldへ必要な音由来事実を渡す |
| mobsの共用判別 | 敵対判別と `awakenedShulkerIds` | soundと敵・友好モブ観測から同じインスタンスを呼ぶ |
| mobsの敵追跡 | 可視性保持、個体の前回距離・体力、死亡queue、爆発、結果の既報管理、boss状態 | packet・server死亡根拠を受け、既存条件で結果を一度だけ確定する |
| mobsの友好観測 | 友好・中立モブ選択、村人、近傍モブの範囲状態 | 共用判別と既存 `PassiveMobSelection` / `NearbyComposition` を使う |
| protocol | HTTP/session/sequence、command重複・pending結果・ack、wire解析 | typed commandと表示受信値をcallbackで渡す |
| commands | 明示commandの期限・slot・期待item照合と実操作 | 実行結果をprotocolへ返す。重複管理の第二の正本を作らない |
| display | HUD状態、表示期限、world/session由来の表示保護、配置・アニメーション | protocolの受信値をclientスレッドで適用する |

表は責務の所属であり、一行につき必ず一クラスを作るという意味ではない。敵観測の移動先が再び巨大になる場合は、死亡結果とboss観測を分ける。

### 先に解決する依存

- **音と敵は同じ敵対判別を使う。** `classifyMobDisposition` からシュルカーの覚醒状態が更新されるため、この管理先を決めてから音を移す。音側に同じ判別器を複製しない。
- **worldには別センサーの根拠が混ざっている。** `buildWorld` が読む音由来の予兆・天候時刻はsound、respawn根拠はplayerの所有とする。worldがそれぞれの記録を勝手に更新しない。
- **周辺モブとブロックは集計器を共用する。** `NearbyComposition.RangeTracker` のアルゴリズムは一つ、状態インスタンスはモブ用とブロック用を分離したままにする。外側範囲を使う保持条件も維持する。
- **匂いとworldは焚き火判定を共用する。** `isLitCampfire` は一つの実装を使う。最初の切り出しで必要な最小判定だけ適切な共用先へ移し、匂い側にコピーしない。
- **モブの識別・環境値は視線先と音にも必要。** 既存の `mobIdentity` と `MobEnvironmentObservation` を共用し、観測ごとに異なる名前付けを作らない。
- **HTTPクライアントが表示stateの解析に依存している。** 表示payloadの受信型・解析をprotocolへ分けてから、HUDとタイムラインをdisplayへ移す。protocolからHUD内部状態を参照しない形にする。

汎用の `AdapterContext` に全状態を詰めて渡したり、巨大な `Utils` に残りを集めたりしない。共用処理は利用箇所を確認してから、用途が分かる小さな型・関数として配置する。

### リセットとスレッド

最初に現行の各フィールドとreset分岐を対応づけ、以降の各段階でその表を更新する。すべてのresetを一つの「world変更」にまとめない。

| 現在の境界 | 移動後も保つ区別 |
|---|---|
| 接続変更・切断 | `DogidoEventClient` のsession切替とHUD無効化を維持 |
| player/world不在の全体reset | tick、health、respawn、pending入力を含む現在の初期化対象を各所有者へ振り分ける |
| worldオブジェクト変更 | 現在のrange/portal初期化と表示無効化・barrierを維持 |
| dimension変更・大きな位置移動 | 現在の観測履歴resetを維持。全体resetと違って保持するtick・health・respawn・pending入力まで消さない |
| HTTP応答が戻る時点 | 現在のsession/world検査とclientスレッドへの適用を維持 |
| 論理サーバー死亡・構造物照会 | 死亡queueの受渡し、構造物cacheの公開方法を維持。新たな競合対策は別の変更として扱う |

## 5. P1〜P17の実行計画

2026-10-09に、従来の段階0〜9を以下の作業単位へ細分化した。**P1は着手準備、コード分割の最初はP2・P3の匂い**。P1の状態・処理順・比較条件・ソース保全と関連検証を完了した。2026-10-09の追加指示により、挙動を変えない分割は確認待ちせず進め、途中は変更範囲の検証に絞る。全体テストはP17で行う。

各Pを一つのレビュー・検証単位とする。`P3.1` などはその中の作業順であり、必ず別コミットにするという意味ではない。実行は番号順を基本とし、表の前提は特に依存する成果を示す。後続Pが終わるまでコンパイルできない状態を、完了したPとして残さない。

以下のJavaファイル名は、特記がなければ `adapter/minecraft-fabric/src/main/java/dogido/fabric/` からの相対位置。既存クラスの移動に伴うテストのpackage/import更新は、そのPに含める。新規クラス名は責務を示す仮称であり、既存型で足りる場合は増やさない。

| P | 作業単位 | 主な前提 | 旧段階 |
|---|---|---|---|
| P1 | 分割前の基準・状態一覧・復帰点 | 直前変更のコード確認 | 0 |
| P2 | 匂い規則・結果型のパッケージ移動 | P1 | 1 |
| P3 | 匂い収集・cache・雨上がり状態の分離 | P2 | 1 |
| P4 | 共用モブ判別・identityの分離 | P1 | 2 |
| P5 | 共用の範囲集計・位置・視線計算の整理 | P4 | 2 |
| P6 | プレイヤー・所持品・乗車・視線先の分離 | P4・P5 | 3 |
| P7 | 限定hotbar操作の分離 | P6 | 3 |
| P8 | 周囲環境の補助処理・既存小クラスの整理 | P3・P5 | 4 |
| P9 | world/blocks集約・構造物cacheの分離 | P6・P8 | 4 |
| P10 | 音の受付・保持・聴覚観測の分離 | P4・P5・P9 | 5 |
| P11 | 友好・中立モブ・村人観測の分離 | P4・P5 | 6 |
| P12 | 敵追跡・可視性・死亡結果の分離 | P10・P11 | 6 |
| P13 | boss固有観測・戦闘情報の分離 | P9・P10・P12 | 6 |
| P14 | イベントJSON組み立ての分離 | P3・P6・P9〜P13 | 7 |
| P15 | HTTP・command契約・表示受信型の整理 | P7・P14 | 7 |
| P16 | HUDと表示状態の移動・root整理 | P15 | 8 |
| P17 | 総合確認・実機確認範囲・文書更新 | P2〜P16 | 9 |

### P1. 分割前の基準・状態一覧・復帰点

**成果物:** 分割直前の対象ファイル一覧、検証結果、状態所有・reset表、tick処理順、復帰点。製品コードの分割はまだ行わない。

**進捗:** [P1基準記録](2026-10-09-fabric-adapter-split-p1-baseline.md)に全78フィールド、tick順、8イベントの比較条件を整理。未検証ソースを保全し、adapter READMEの旧匂い説明を修正した。Javaの匂い関連22件、Rustの関連3フィルター（14/7/8件、重複あり）が成功。全体テストはP17で行う。記録のcommitとソースarchiveを復帰基準にする。

- **P1.1:** 作業ツリーとHEADを確認し、直前の匂い方位撤去と既存の別作業を識別する。最新のユーザー指示に従って変更範囲の関連検証を行い、撤去前の成功件数を流用しない。
- **P1.2:** Adapterの各フィールドについて、更新元・読取先・reset条件・スレッド・移動予定先を一覧化する。`onClientTick` は死亡queueの消費、各観測、結果送信、HUD適用まで現行順を記録する。
- **P1.3:** 比較対象のイベント種別と必須・任意フィールドを決める。既存fixtureや取得済みpayloadを確認し、足りないケースは固定観測を使う検査として準備する。合成入力とMinecraft実観測を区別する。
- **P1.4:** 関連検証後に、今回所有する差分だけの回復可能なcheckpointを確保する。adapter READMEに残る旧匂い説明は現行仕様と照合し、この準備段階で説明を合わせる。

**検証・完了条件:** 方位撤去後の関連Java/Rust検証と必要なコンパイルの結果が記録され、失敗が分割前からあるか識別できる。現在の作業内容を含む復帰点があり、未コミット変更があるのにHEADだけを基準としていない。分離できない別作業は勝手にstageしない。

### P2. 匂い規則・結果型のパッケージ移動

**対象:** `SmellPolicy.java`、`ZombieScentPolicy.java` と対応する2テスト。移動先は `observation/smell/`。収集処理と5つの匂い状態はまだAdapterに置く。

- **P2.1:** 両policyの呼出元と内部型の利用箇所を列挙する。`Observation`、`Component`、候補型など、Adapterが実際に参照する境界だけを確認する。
- **P2.2:** 既存ファイルを移し、外から必要な型・入口だけを利用可能にする。既存recordを使えるなら同内容の別DTOを作らない。
- **P2.3:** Adapterとテストのimportを切り替え、旧packageの実装を除く。アルゴリズム、定数、JSON builderは変更しない。

**検証・完了条件:** `SmellPolicyTest` / `ZombieScentPolicyTest` が移動後の本体を検査し、Adapterを含めたコンパイルが成立する。混合・mask・温度・抑止・雨上がりの既存期待値を移動の都合で変えていない。

### P3. 匂い収集・cache・雨上がり状態の分離

**対象:** Adapterの `collectZombieSmellCandidates`、`updateSmellObservation`、`updateRainAfterSmellWindow`、hotbar/item/block/焚き火の収集、局所天候・抑止・reset。移動先は `observation/smell/SmellObserver.java`。

- **P3.1:** `currentBlockSmellCandidates`、`currentSmellObservation`、`lastBlockSmellScanTick`、`rainAfterSmellUntilTick`、`previousLocalSmellWeather` と、その更新・resetを一緒に移す。
- **P3.2:** `isLitCampfire` はworldと共用できる小さな判定へ一度だけ移す。匂い用の局所天候判定とworldの広域天候判定は区別する。
- **P3.3:** Adapterからplayer/world・共通tick・必要な設定値を渡し、現在の匂い結果と雨上がりのwindow IDを同じ時点の読み取り結果として受け取る。収集順とscan間隔を維持する。`buildSmellObservation` / `buildSmellComponent` はP14まで既存側に残す。
- **P3.4:** 全体resetとdimension/位置移動resetから、新しい所有者のresetを元と同じ時点に呼ぶ。旧フィールドと収集本体を除く。

**検証・完了条件:** P2の回帰検証に加え、雨上がり開始・期限・reset、block再scan間隔、現在結果の受渡しを確認する。Minecraftに依存しない状態遷移を検査できる境界には意味のあるテストを追加する。匂い方位の計算・保持・payloadが復活していない。

### P4. 共用モブ判別・identityの分離

**対象:** `classifyMobDisposition`、`isShulkerAwakened`、中立モブの敵対判別・注意理由、`mobIdentity`、`MobEnvironmentObservation.java`、`SoundObservationPolicy` 内の共用定義。判別の移動先は `observation/mobs/`、共用値型は `observation/` を基本とする。

- **P4.1:** 音・敵・友好モブ・視線先からの利用を確認し、共用の判別入口と結果型を決める。現在 `SoundObservationPolicy` にある `NEUTRAL_MONSTER_TYPES` と `customName()` も共用先へ移し、音policyとモブ判別・identityが同じ実装を参照する。音policy本体の移動はP10とし、このPで共用部分を取り出してsoundとmobsの相互依存を避ける。
- **P4.2:** `awakenedShulkerIds` と判別処理を同じ所有者へ移し、すべての呼出元から一つのインスタンスを使う。呼出しごとの更新時点を維持する。
- **P4.3:** entityのidentityと環境値を共用化し、Adapter側に残る利用箇所を切り替える。内部の可変集合は渡さない。

**検証・完了条件:** 非敵対・敵対化した中立モブ、閉じた/覚醒済みシュルカー、名前付き・飼育済みentityの値を照合する。共用先へ移した名前・中立集合の検査と `SoundObservationPolicyTest` も確認する。状態の保持・resetを検査し、純粋な問い合わせだとみなした先読みや二重更新がない。実entityの確認が必要な部分は実機項目として残す。

### P5. 共用の範囲集計・位置・視線計算の整理

**対象:** `NearbyComposition.java`、距離帯・水平/絶対方位/上下判定、視線・窓・音の通り道に関する共用処理。移動先は `observation/` 配下の用途別の小クラス。

- **P5.1:** `NearbyComposition` を移動する。モブ用とブロック用の `RangeTracker` インスタンスはそれぞれの現在の所有者に残し、アルゴリズムだけ共用する。
- **P5.2:** 位置・方向・視線計算の呼出元を整理し、複数担当が使う既存実装を移す。距離閾値や遮蔽条件は変更しない。
- **P5.3:** 視線処理が使う窓・開口の小判定も必要な範囲で同時に移す。共用クラスからAdapterのprivateメソッドへ呼び戻す依存を残さない。

**検証・完了条件:** `NearbyCompositionTest` と距離・方向の境界値、範囲の出入り・保持を照合する。窓や開口のMinecraft実動作は別途確認項目にする。匂い経路へ方向計算を接続していない。

### P6. プレイヤー・所持品・乗車・視線先の分離

**対象:** `buildPlayer`、`buildVehicleState`、`buildInventory`、`buildHotbar`、武器値、`buildLookTarget`、睡眠・respawn根拠。移動先は `observation/player/`。

- **P6.1:** player/vehicle/inventory/hotbarと視線先の観測を移す。現在のJSON断片を返す処理は当面その形を使い、全項目を新DTOへ一斉変換しない。
- **P6.2:** `wasSleeping` と観測済みrespawnの位置・dimension・有無を同じ担当へ移す。worldに渡す拠点根拠と、全体reset時の扱いを明示する。
- **P6.3:** identity・幾何処理をP4/P5の共用先へ接続する。pending user textの記録・消費はrootのままにし、観測を読む回数と入力消費を結び付けない。

**検証・完了条件:** 未乗車時のvehicle省略、乗車値、inventory/hotbar、視線先なし/あり、respawn未観測/同dimension/別dimensionの形と条件が一致する。プレイヤーのhealth差分による被弾追跡はP12の所有者決定まで既存順序を維持する。

### P7. 限定hotbar操作の分離

**対象:** `handleSelectHotbarCommand`、`executeSelectHotbarCommand`、`rememberSelectHotbarResult`。移動先は `commands/HotbarCommandExecutor.java`。`DogidoCommandProtocol` 本体の移動はP15。

- **P7.1:** executorが受け取る既存command型と、結果を返す最小のcallback境界を用意する。packageをまたぐ型だけを公開・分離する。
- **P7.2:** `client.execute`、期限・slot・期待itemの実行前後照合、結果コードを一まとまりで移す。
- **P7.3:** 既処理ID、pending結果とackは既存protocolの管理に残す。設定やイベントクライアント全体をexecutorへ公開せず、必要な値と結果通知だけを渡す。

**検証・完了条件:** `DogidoCommandProtocolTest` と、期限切れ・slot不正・item不一致・成功結果の経路を確認する。明示操作の受付/結果対応と重複処理が維持され、通常観測から自動持ち替えが発生しない。

### P8. 周囲環境の補助処理・既存小クラスの整理

**対象:** `OverheadObservation.java`、`PortalObservationTracker.java`、`WorldObservationFrame.java`、光・水中・屋内・窓/ドア/ベッド・地形・召喚場面などの補助観測。

- **P8.1:** 既存小クラスを目標packageへ移す。`WorldObservationFrame` は `observation/`、頭上・portalは `observation/world/` とする。frameの遅延評価とdeep copyの意味を維持する。
- **P8.2:** 周囲探索の補助処理を `observation/world/` 内で責務ごとに移す。P3/P5で既に移した判定を再利用する。loop範囲・打切り・閾値は変更しない。
- **P8.3:** `buildWorld` / `buildNearbyBlocks` はこの段階ではAdapterに残し、新しい補助処理を呼ぶ。portal trackerは配置だけ移し、状態の所有移管はP9で行う。

**検証・完了条件:** `OverheadObservationTest`、`PortalObservationTrackerTest`、`WorldObservationFrameTest` が新配置で成立する。frameは最初の取得だけ実行し、返したJSONの変更が他の読取結果へ漏れない。補助処理の移動で探索回数を増やしていない。

### P9. world/blocks集約・構造物cacheの分離

**対象:** `buildWorld`、`buildNearbyBlocks`、`buildDroppedItems`、破壊履歴、`probeStructureAtPlayer`、`observeNearbyLightning`、portal/ブロック範囲状態。移動先は `observation/world/` の集約担当。

- **P9.1:** world/blocksの組み立てと担当状態を移す。近傍ブロックの範囲状態、portal、破壊履歴、視覚による近傍雷の時刻・距離は各更新・resetと同時に所有を移す。
- **P9.2:** playerのrespawn根拠はP6の結果として受ける。音由来の予兆・雨音・雷等はrootの既存記録から読み取り値を受け、P10で供給元を替える。匂いのためにworld全探索を呼ばない。
- **P9.3:** 構造物のサーバースレッド照会と結果cacheを一緒に移す。現在の非同期公開方法を維持し、別の競合修正やepoch機構を追加しない。
- **P9.4:** rootが同じtickで一つの `WorldObservationFrame` を作る形を保つ。種類集計のsnapshot限定条件と各resetを接続する。

**検証・完了条件:** P8と近傍集計の既存テストに加え、world/blocks/落下item/破壊履歴のpayloadとreset、同tickの取得回数を照合する。worldの取得が音やplayerの内部状態を書き換えない。

### P10. 音の受付・保持・聴覚観測の分離

**対象:** `SoundObservationPolicy.java`、音packet/実再生受付、音記録と期限切れ、`currentSoundSources`、`scanAuditoryThreats`、`scanAmbientSounds`、音由来の天候・予兆。移動先は `observation/sound/`。

- **P10.1:** policyと必要な音結果型を移し、音記録・期限・解決処理の所有者を一つにする。Mixinが使うAdapterの静的入口は維持して委譲する。
- **P10.2:** 受付から記録・期限切れ・観測出力までを接続する。共用モブ判別はP4を使い、聞いた個体の現在位置だけを更新する。
- **P10.3:** tick期限と `heardAtNanos` を区別し、視覚との重複抑止の呼出順を保つ。worldへ音由来事実を渡す供給元をここへ替える。
- **P10.4:** 爆発packetはP12まで既存の敵結果管理へ届ける。音側に別の死亡queue・結果台帳を作らない。視覚由来の近傍雷観測は音受付と区別する。

**検証・完了条件:** `SoundObservationPolicyTest`、期限・同定・遮蔽・既聴取個体の位置更新を照合する。すべてのMixin入口が生きており、packetを受けない孤立した収集器になっていない。worldへの事実とresetも一致する。

### P11. 友好・中立モブ・村人観測の分離

**対象:** `PassiveMobSelection.java`、`scanAmbientMobs`、候補判定、村人属性、モブ用 `RangeTracker` と種類集計cache。移動先は `observation/mobs/`。

- **P11.1:** 友好観測を移し、P4の共用判別・identityとP5の範囲集計を使う。
- **P11.2:** 村人の職業・種類・子供・睡眠等の既存項目と、モブ種類・個体数の集計を移す。種類取得は現在のsnapshot条件を維持する。
- **P11.3:** モブ用の範囲状態とcacheのresetを接続する。ブロック用の状態はP9のままとし、送信時機・署名はrootの調停に残す。

**検証・完了条件:** `PassiveMobSelectionTest`、`NearbyCompositionTest` と村人payloadの対応を確認する。同じ候補順から同じ代表・種類・個体数が得られ、友好観測のために敵追跡を書き換えていない。

### P12. 敵追跡・可視性・死亡結果の分離

**対象:** `scanThreats`、可視性保持、追跡map、被弾・戦闘継続状態、server死亡queue、爆発記録、`applyServerDeathObservations`、`observeCreeperDetonations`、`recordHostileOutcome`、期限切れ・reset。移動先は `observation/mobs/`。

この範囲は前回個体情報を共有するため、**追跡mapの半分だけを別のPへ移さない**。以下を内部手順として分けるが、所有移管と配線が揃って一つのPを完了とする。

- **P12.1:** 既存の敵・死亡結果型を境界へ出し、追跡情報を読む必要がある音/boss/イベントbuilderへの入口を決める。さらに、敵scan・死亡結果・boss固有処理が共有するWarden/Dragonの最終視認、座標、追跡ID、撃破時刻は、このPでboss状態の単一所有者へ移す。P13までrootに残る固有処理も、狭い読取・更新入口を通して同じ状態を使う。可変mapや状態の複製を渡さない。
- **P12.2:** 追跡・可視性・死亡結果の状態と更新処理を同じ担当へ移す。内部でクラスを分けても、状態ごとの所有者は一つにする。新たな世界探索から過去の個体情報を再構成しない。
- **P12.3:** server死亡queueの消費→爆散観測→敵scan→新規結果収集という現在のtick順と、死亡/爆散/離脱の既報管理を保って配線する。health差分の取得時点も維持する。
- **P12.4:** `combatActive` 更新、結果の消去、期限切れ、全体/dimension/位置移動resetを接続する。Mixin爆発入口はこの所有者へ一度だけ渡す。

**検証・完了条件:** 同一死亡の重複、プレイヤー撃破/その他死亡/爆散/離脱、可視性保持、再接近、切断とdimension変更を確認する。一般敵scan・死亡結果とrootに残るboss処理の双方から、同じboss状態が既存時点で更新される。`hostile_defeated` / `creeper_detonated` と後続の `combat_ended` の順序が一致し、音・消失だけを撃破へ昇格しない。

### P13. boss固有観測・戦闘情報の分離

**対象:** Wardenの仕掛け・追跡・撃破根拠、Dragonのphase・追跡・撃破根拠、`buildCombat` の観測取得部分。移動先は `observation/mobs/` のboss担当と戦闘観測担当。

- **P13.1:** `observeWardenSpecialSetups`、Warden/Dragonの固有探索・撃破確認をP12で用意した所有者へ移す。仕掛け専用の残りの状態も更新/resetと一緒に移す。既に移した追跡・撃破状態を作り直さない。
- **P13.2:** `buildCombat` 内で行う個体数・距離・仕掛け等の観測と、付随する状態更新を明示的に分け、P12/P13の結果から現在の戦闘JSONを作れるようにする。`appendDragonCombatInfo()` は追跡状態も更新するため、読取専用だとみなさず、元と同じ呼出時点・回数で更新する。
- **P13.3:** 予兆の地形探索はP9、音根拠はP10から受ける。`combat_ended` の組み立て前に行う `combatActive` 更新を含め、現行タイミングを保つ。移動を理由に毎tick先読みしない。

**検証・完了条件:** 固有状態の保持・resetと、戦闘中/終了時のpayloadが一致する。boss撃破の根拠と単なる離脱を区別する。実entity確認が必要なWarden/Dragon場面は未確認項目として残し、自動試験だけで実機確認済みにしない。

### P14. イベントJSON組み立ての分離

**対象:** `buildStatusSnapshot` から各部分イベントbuilder、`baseEnvelope`、匂い/視覚/聴覚/友好/死亡結果のJSON化、meta・materials付加。移動先は `protocol/` のイベント組み立て担当。

- **P14.1:** 各builderで行う世界の取得、sequence発行、pending user text消費、状態更新を分け、元と同じ場所・順序で担当者が実行する形にする。
- **P14.2:** 既存の観測結果・JSON断片と必要なenvelope値を受け取ってイベントを作る。全ドメインのJSON断片を新型へ書き換えることは前提にしない。
- **P14.3:** snapshot、visual、audio、ambient、detonation、defeat、player death、combat endを一種ずつ切り替え、不要になった旧builderを除く。
- **P14.4:** P1の比較ケースを使い、任意項目の省略、array順、元のJSONを破壊しないコピー、sequence発行・入力消費の回数を照合する。

**検証・完了条件:** 同じ固定観測から同じイベント形が得られ、組み立て担当が世界を再探索しない。時刻/sequenceを比較から除く場合も、元の発行時点と回数を別に検証する。送信の判定・呼出順はrootに残す。

### P15. HTTP・command契約・表示受信型の整理

**対象:** `DogidoEventClient.java`、`DogidoCommandProtocol.java`、`WorkshopDisplayState` の表示payload解析・受信型。移動先は `protocol/`。

- **P15.1:** 表示payloadの受信型・解析を表示タイムラインから分け、HTTPクライアントとHUDがその受信型を共有する。二つのparserを残さない。
- **P15.2:** EventClient/CommandProtocolを移し、root・P7のexecutor・表示側の利用を切り替える。session、sequence、既処理command、pending結果・ackの所有者は維持する。
- **P15.3:** `HttpClient.sendAsync()` の非同期送信とcallbackを保つ。新規queue・再送・強制直列化は加えない。
- **P15.4:** 表示応答のsession/world検査と `client.execute` の順序を維持する。protocolからHUD内部の可変stateへの依存を除く。

**検証・完了条件:** `DogidoCommandProtocolTest` と表示parserの既存検査を新境界へ追従させる。command/result/ackと古いsessionの表示応答を検証し、HTTP通信成功とゲーム内への適用を混同しない。

### P16. HUDと表示状態の移動・root整理

**対象:** `DogidoWorkshopHud`、`DogidoCharacterHud`、両表示state、`CharacterPlacement`、`CharacterBlink`、`ThinkingAnimation` と対応テスト。移動先は `display/`。

- **P16.1:** HUDと表示状態・補助処理を移し、P15の受信型へ接続する。設定は必要な値だけ受け、全設定を公開しない。
- **P16.2:** 両HUDへ同じsnapshotを渡す配線、表示期限、world/session切替、危険時のsequence barrierを維持する。
- **P16.3:** rootに残る処理を§2の責務と照合し、移行用の旧本体・未使用import・不要な公開口を除く。Mixin/entrypoint・設定名・resource・操作名を確認する。

**検証・完了条件:** 既存の表示state・配置・瞬き・animationテストとコンパイルが成功し、参照切れがない。rootから具体的な収集・分類・個別JSON生成が外れ、状態所有がP1の表と一致している。

### P17. 総合確認・実機確認範囲・文書更新

**対象:** Fabric全体、Rustの受信契約に関係する検証、adapter README・AGENTSの配置案内、本計画の進捗と日付付き結果記録。

- **P17.1:** Java全テスト・ビルド、P1のpayload比較、関連するRust受信側の検証を行う。途中のPの結果を新しい変更後の通し確認に流用しない。
- **P17.2:** §7の実機項目を、起動/接続→移動・world切替→各観測→明示操作→戦闘結果→HUDの順に確認し、実施結果と未実施を記録する。
- **P17.3:** package・入口・状態所有と実装が一致するよう、既存の案内と本計画を更新する。結果には使用したrevision/作業ツリー、コマンド、既知の失敗、実機範囲を残す。
- **P17.4:** 対象差分とstaged差分を見直し、検証済みの今回の変更だけをcheckpointにする。push・tag・履歴書換えは含めない。

**検証・完了条件:** 分割による未説明の挙動差がなく、関連検証を通過し、各責務の入口を追える。実機未実施なら「構造分割・自動検証済み、実機未確認」と明記し、実機確認済みとはしない。

## 6. 各Pの進め方と記録

1. **着手前:** 対象ファイル・直接の呼出元・状態所有を再確認する。先のPで同じ処理が移っていれば、その入口を使う。
2. **実装:** 境界の型・入口を整える → 状態と処理を同時に移す → 呼出元を切り替える → 旧本体を除く。広い機能の追加やバグ修正を同時に混ぜない。
3. **コード確認:** 変更した責務、残した処理、packageをまたぐ公開範囲、resetとtick順をエージェントがレビューする。挙動を変えない分割・整理は確認待ちせず進める。新機能・機能変更を入れる場合だけユーザーのコード確認を先に挟む。
4. **検証:** 途中のPでは変更範囲の関連テストと必要なコンパイルだけを実施する。全体テスト・通しビルドはP17で行う。新たな挙動差や失敗があれば、そのP内で原因を特定する。
5. **区切り:** 実施結果、残る実機確認、移動後の入口を記録し、検証済みの対象差分だけをcheckpointにする。依存先に未解決の契約差がある状態で次のPへ進めない。

既に受けた実施許可はその範囲で引き継ぎ、同じ確認を繰り返さない。2026-10-09にユーザーから分割を継続して進める指示を受けた。

各Pの報告には次の5項目を使う。詳細結果は一つの日付付き検証記録へ追記し、Pごとの同内容の文書を乱立させない。

| 項目 | 記録内容 |
|---|---|
| 対象 | P番号、変更したファイル・責務 |
| 所有 | 移動した状態、公開入口、呼出元・resetの接続 |
| 確認 | コード確認状況、検証コマンドと結果 |
| 未確認 | 実機・受信先などの未実施範囲、既知の失敗 |
| 復帰点 | 今回のcheckpointと残る未コミット変更の区別 |


## 7. 検証と作業管理

### 検証の対応表

| 対象 | 自動で確かめる内容 | 実機で確かめる内容 |
|---|---|---|
| 匂い | 既存policy・雨上がり・抑止・結果形・reset | 実ブロック/持物/天候からの観測と時間経過 |
| player・command | inventory/視線先の形、期限・slot・期待item・重複結果 | 明示hotbar操作、乗降、睡眠と拠点根拠 |
| world | `WorldObservationFrame`、`NearbyComposition`、portal、頭上と取得回数 | 移動、dimension変更、ブロック変化、構造物 |
| sound・mobs | 既存音policy、可視性と期限、queueと結果の一度性、packetからの接続 | 聞こえるが見えない敵、撃破、爆散、離脱、boss |
| protocol | snapshot/部分イベントのキー・省略・配列順・sequence発行、command往復 | session再接続と実際の送受信 |
| display | 既存表示・配置・瞬き・animationテスト、古い応答・barrier | 両HUD、危険時更新、切断・world変更 |

Javaの通し検証はP17で `adapter/minecraft-fabric/` の `./gradlew --offline test build` を実行する。途中の各段階では変更に関係するテストを選択し、必要な境界試験だけ追加する。移動した行と同じ処理をテストに書き直すだけの検査や、Minecraft全体の巨大mockは増やさない。

Rust側は同じpayloadが同じ受信結果になることを関連テストで確認する。旧検証記録には結合テストの6失敗とClippyの5警告があるが、これは分割前の記録であり、現在も同じとは未確認。P1の実測と比較し、既知の失敗は明記する。無関係な期待値・警告を分割に混ぜて直さず、関連検証の成功を「全検査成功」と表現しない。

実機確認のための常駐サーバー再起動やjar入替は、この計画書を作る作業には含めない。実装後の確認範囲に応じて扱う。

### 差分と復帰点

- 段階ごとに一人の編集担当が統合し、同じ作業ツリーで並行編集しない。独立した調査・読み取りレビューは分担できる。
- checkpoint前にstatus、対象の全差分、staged差分を確認する。今回所有する検証済み変更だけを個別stageし、ユーザーの変更や別作業を巻き込まない。
- 問題が出た場合は、その段階の差分と入力を比較して原因を特定する。未コミット変更を一括でreset・restore・stashしない。
- 分割中に見つけた挙動バグは別項目として記録し、修正が必要なら分割差分と区別して扱う。

## 8. 進捗

2026-10-09時点。P内の作業を終えていても、コード確認・関連検証を終えるまではそのPを完了にしない。

- [x] P1: 分割前の基準・状態一覧・復帰点（関連検証済み。詳細はP1基準記録）
- [ ] P2: 匂い規則・結果型のパッケージ移動
- [ ] P3: 匂い収集・cache・雨上がり状態の分離
- [ ] P4: 共用モブ判別・identityの分離
- [ ] P5: 共用の範囲集計・位置・視線計算の整理
- [ ] P6: プレイヤー・所持品・乗車・視線先の分離
- [ ] P7: 限定hotbar操作の分離
- [ ] P8: 周囲環境の補助処理・既存小クラスの整理
- [ ] P9: world/blocks集約・構造物cacheの分離
- [ ] P10: 音の受付・保持・聴覚観測の分離
- [ ] P11: 友好・中立モブ・村人観測の分離
- [ ] P12: 敵追跡・可視性・死亡結果の分離
- [ ] P13: boss固有観測・戦闘情報の分離
- [ ] P14: イベントJSON組み立ての分離
- [ ] P15: HTTP・command契約・表示受信型の整理
- [ ] P16: HUDと表示状態の移動・root整理
- [ ] P17: 総合確認・実機確認範囲・文書更新

## 9. 実施結果

各Pの変更内容・検証・checkpointは[分割の実施記録](2026-10-09-fabric-adapter-split-results.md)へまとめる。
