# 2026-10-09 Fabric分割 P1 基準記録

状態: **P1の基準整理・ソース保全・変更範囲の検証を完了。全体テストと通しビルドはP17。次はP2のコード分割。**

[全体計画](2026-10-08-fabric-adapter-split-plan.md)のP1用。対象は書類フォルダ側のmain。以下は現在のソースを読んだ結果であり、Minecraft実行時の確認ではない。2026-10-09の追加指示で、コード確認待ちは新機能・機能変更の場合だけに限定された。挙動を変えない分割は継続し、途中は変更範囲の検証、全体テストは計画完了時に行う。

## 基準と作業範囲

- 基準HEAD: `71c178b20086b7dc69fe50ccdf4ac74a2e6c77ba`。作業ツリーには複数作業の未コミット変更があり、HEADだけでは現在の実装を再現できない。
- `DogidoClientAdapter.java`: 5,291行。SHA256 `6c4d198aa7cd86b0d6d2fb1b045df653434b9f0ca46587fd113b4c6c33bab823`。
- 直前の匂い方位撤去25パスは、撤去時の結果hashとすべて一致した。23更新と2削除を含み、その後の変更は検出されていない。この一致は動作検証の代わりにはしない。
- P1で変更する説明はadapter READMEの旧匂い仕様。種類・強度の成分一覧、視認/聴取から独立したゾンビ収集、方位撤去、雨上がりの扱いを現行仕様へ合わせる。
- Java/Rust製品コード、共有プロンプト、既存テストはこの準備作業で変更していない。実機・常駐サーバー・モデルへの接続も行っていない。

対象ファイルのhash、削除状態、Adapterの全フィールドと参照メソッドは[基準データ](2026-10-09-fabric-adapter-split-p1-baseline.json)に保存する。参照メソッドの機械抽出は調査用索引であり、間接更新やスレッド安全性を証明する解析ではない。

## 状態の所有とreset

以下で「全体」は `resetTransientState()`、「dimension」は `resetThreatStateForDimensionChange()`。大きな位置移動は後者をそのまま呼ぶ。現在の状態はAdapterが所有し、「移動予定先」は分割後の担当を示す。定数は状態表に含めず、そのまま移す対象として元ファイルを保全する。

| 状態（全78フィールド） | 移動予定先 | 主な更新元 → 読取先 | resetの区別 |
|---|---|---|---|
| 初期化・参照: `INSTANCE`, `config`, `eventClient` | root | `onInitializeClient` → `static入口・各処理` | 初期化時。個別session状態はEventClientが所有 |
| 共通時計: `tickCounter` | root | `onClientTick / resetTransientState` → `全観測・期限・送信判定` | 全体resetのみ0。dimension/位置移動で維持 |
| 送信時機: `lastSnapshotTick`, `lastThreatTick`, `lastAudioEventTick`, `lastAmbientMobEventTick`, `lastAudioDispatchObservationTick` | root | `onClientTick / reset群` → `shouldSendSnapshot / shouldSend*Event` | lastSnapshotTickは全体resetのみ。他は全体とdimension |
| 送信署名: `lastThreatSignature`, `lastAudioSignature`, `lastAmbientMobSignature` | root | `onClientTick / reset群` → `shouldSend*Event` | 全体とdimensionで空文字 |
| world境界・前回位置: `lastDimensionId`, `lastPlayerX`, `lastPlayerY`, `lastPlayerZ` | root | `onClientTick / reset群` → `dimension比較 / hasLargePositionJump` | lastDimensionIdは全体のみ。XYZは全体とdimensionでNaN |
| 匂い: `currentBlockSmellCandidates`, `currentSmellObservation`, `lastBlockSmellScanTick`, `rainAfterSmellUntilTick`, `previousLocalSmellWeather` | P3 smell | `updateSmellObservation / updateRainAfterSmellWindow / resetSmellState` → `匂い更新 / buildSmellObservation` | 全体・dimensionからresetSmellStateを呼ぶ |
| モブ共用判別: `awakenedShulkerIds` | P4 mobs | `isShulkerAwakened / reset群` → `classifyMobDisposition経由の各観測` | 全体とdimensionでclear |
| 睡眠・拠点根拠: `wasSleeping`, `respawnPointObserved`, `observedRespawnPos`, `observedRespawnDimension` | P6 player | `onClientTick / resetTransientState` → `睡眠遷移 / estimateRespawnDistance` | 全体のみ。dimension/位置移動で維持 |
| ブロック破壊履歴: `recentBlockBreakObservations` | P9 world | `rememberBlockBreak / expireBlockBreakObservations / reset群` → `buildRecentBlockBreaks` | 全体とdimensionでclear |
| portal: `portalObservationTracker` | P9 world | `scanNearbyPortals / onClientTick / reset群` → `scanNearbyPortals` | 全体・dimension・接続変更・world参照変更でreset |
| ブロック種類: `nearbyBlockKinds` | P9 world | `buildNearbyBlocks / onClientTick / reset群` → `buildNearbyBlocks` | 全体・dimension・world参照変更でreset |
| 構造物: `structureAtPlayer`, `lastStructureProbeTick` | P9 world | `probeStructureAtPlayer / reset群` → `buildWorld / probeStructureAtPlayer` | 全体とdimensionでnull/-1000。結果cacheはvolatile |
| 視覚の雷: `lastNearbyLightningObservedTick`, `lastNearbyLightningDistance` | P9 world | `observeNearbyLightning / reset群` → `buildWorld` | 全体とdimensionで-1000/99.0 |
| 音記録: `recentSoundObservations`, `recentAmbientSoundObservations` | P10 sound | `recordSoundObservation / recordAmbientSoundObservation / expire* / reset群` → `currentSoundSources / scanAuditoryThreats / scanAmbientSounds` | 全体とdimensionでclear |
| 音由来の環境: `lastOminousSoundObservedTick`, `lastRainSoundObservedTick`, `lastThunderSoundObservedTick`, `lastEnderEyeLaunchObservedTick`, `lastOminousSoundKind` | P10 sound | `recordOminousSoundObservation / recordWeatherSoundObservation / onGlobalSoundPacket / reset群` → `buildWorld / detectBossOmenKind / 次の音の採否` | 全体とdimensionで時刻/種類を初期化 |
| 友好・中立の種類: `nearbyMobKinds`, `nearbyMobComposition` | P11 mobs | `scanAmbientMobs / onClientTick / reset群` → `scanAmbientMobs / attachNearbyMaterials` | trackerは全体・dimension・world参照変更でreset。composition cacheの明示resetはない |
| 敵個体追跡: `lastThreatDistances`, `lastThreatSeenTicks`, `lastThreatHealths`, `lastThreatTypes`, `lastThreatEntityIds`, `lastThreatPositions` | P12 mobs | `scanThreats / expireThreatMemory / reset群` → `scanThreats / isApproaching / 死亡・爆散照合` | 全体とdimensionでclear |
| 可視性保持: `lineOfSightStartedTicks`, `confirmedVisibleTicks` | P12 mobs | `shouldTreatAsVisible / expireThreatMemory / reset群` → `filterVisibleThreats` | 全体とdimensionでclear |
| 死亡・爆散結果: `hostileOutcomes`, `pendingServerDeaths`, `recentExplosionObservations`, `lastExplosionObservedTick` | P12 mobs | `AFTER_DEATH / applyServerDeathObservations / recordHostileOutcome / onExplosionPacket / reset群` → `scanThreats / observeCreeperDetonations / onClientTick / buildCombat / buildWorld` | 全体とdimensionで初期化。結果clear・期限切れは個別の既存時点 |
| 被弾・戦闘継続: `lastDamageTick`, `lastVisualThreatObservedTick`, `lastAudioThreatObservedTick`, `lastCombatSignalTick`, `combatActive`, `lastHealth`, `wasDead` | P12 mobs | `onClientTick / updateCombatTracking / reset群` → `shouldSendCombatEnded / buildCombat / HUD危険判定` | health/wasDeadは全体のみ。他は全体とdimension |
| boss共有状態: `lastWardenSeenTick`, `trackedWardenEntityId`, `lastWardenDefeatObservedTick`, `lastDragonSeenTick`, `trackedDragonEntityId`, `lastDragonDefeatObservedTick`, `lastWardenSeenX`, `lastWardenSeenY`, `lastWardenSeenZ` | P12で所有確定、P13で固有処理 | `scanThreats / recordHostileOutcome / observeTracked* / appendDragonCombatInfo` → `buildCombat / boss固有の確認` | 時刻・IDは全体とdimension。Warden XYZには明示resetなし |
| Warden仕掛け: `lastWardenEndCrystalObservedTick`, `lastWardenTntSetupObservedTick` | P13 mobs | `observeWardenSpecialSetups / reset群` → `isWardenRangedTrapActive` | 全体とdimensionで初期化 |
| 表示の配線: `workshopHud`, `characterHud`, `displayWorld`, `displayConnection`, `displayThreatSignature` | root / P16 display | `onInitializeClient / onClientTick` → `onClientTick / 表示callback` | HUDは個別reset。参照/署名はonClientTickで管理しresetTransientStateの一括対象ではない |
| 入力: `pendingUserText` | root | `rememberUserText / consumePendingUserText / resetTransientState` → `buildMeta` | 全体のみnull。最初のevent生成で消費 |

### reset時に一括化しない差

- `tickCounter / lastSnapshotTick / lastHealth / wasDead / wasSleeping / respawn根拠 / pendingUserText / lastDimensionId` は全体resetの対象で、dimension resetでは保持する。
- Wardenの前回XYZ、`nearbyMobComposition`、`displayThreatSignature` は両resetメソッドの明示対象ではない。分割時に「初期値に戻すはず」と推測して変更しない。
- world参照変更だけの分岐では、モブ/ブロックのrange、portal、表示要求、両HUDを無効化・同期する。全体resetとは別。
- 設定がdisabledのtickではcharacter HUDをresetしてreturnする。その他すべての状態をresetする処理へ置き換えない。
- `clearCombatOutcomeObservations()` と `expireThreatMemory()` は全体resetとは別の時点で呼ぶ。

### スレッドと更新入口

| 入口 | ソースで確認できる受渡し | 分割時の保持事項 |
|---|---|---|
| `END_CLIENT_TICK` | `onClientTick` へ登録 | 共通tick・観測・送信・表示の呼出順 |
| 論理サーバー `AFTER_DEATH` | `ConcurrentLinkedQueue`へ追加、client tickで消費 | queueの一つの所有者、消費時点 |
| 構造物照会 | `server.execute` 内で探索し、volatile cacheへ書込 | 検出元と公開方法。現在ないepoch検査を分割と同時追加しない |
| packet / SoundManager Mixin | 対象メソッドのHEADからstatic入口を直接呼ぶ | このコードに再dispatchはない。すべてclient threadだと断定せず、既存入口を維持 |
| chat / command / block break callback | 登録したメソッドを呼ぶ | pending入力と破壊履歴の記録時点 |
| HTTP応答→hotbar | handlerから `client.execute` へ渡す | 期限・slot・期待itemの再照合、結果通知 |
| HTTP応答→表示 | parser後に `client.execute`、そこでsession/worldを再検査 | 両HUDへの同じsnapshot適用、古い応答の排除 |

## tick内の処理順

次の順序は `onClientTick` の現状を写したもの。eventのHTTP到着順を保証する記述ではない。

1. disabled判定。接続変更時のsession無効化・HUD/portal reset。
2. player/world不在なら表示無効化・全体resetして終了。world参照が変わればrange/portalと表示をreset・同期。
3. 接続参照と送信前sequenceを記録。dimension差・大きな位置移動を判定して該当reset。現在座標を記録。
4. 共通tickを進め、sessionを確保。音・破壊履歴の期限処理、視覚の雷、構造物照会。
5. 睡眠への遷移からrespawn根拠を更新。health差分から被弾時刻を更新。
6. server死亡queueを適用 → クリーパー爆散照合 → 敵scan → 今tickの非爆散死亡結果を収集。
7. Wardenの仕掛け、Warden/Dragonの撃破確認 → 可視性保持を適用。
8. 既聴取個体の現在位置 → 敵対音 → 環境音 → 可視敵と音の重複を除いた一覧。
9. 匂い更新 → snapshot時機を判定 → 友好モブ観測 → 戦闘継続状態更新 → player死亡判定。
10. worldとblocksのSupplierを持つ `WorldObservationFrame` を一つ作る。この時点では探索しない。
11. 条件に応じ、snapshot → visual → auditory → ambient mob → detonation → hostile defeatの順にbuilder/送信を呼ぶ。
12. 新しいplayer死亡ならpayloadを作って送信後、combatを解除し結果を消す。そうでなければcombat end条件を判定し、**combatを解除してから**payloadを作り、送信後に結果を消す。
13. `wasDead` 更新 → 敵記憶の期限切れ処理 → 危険状態・sequence barrierを両HUDへ適用。
14. 8tickごとに表示snapshotをpollし、同じ結果を両HUDへ渡す。

## 8イベントのraw JSON比較基準

比較は **Fabricの生成JSON** と **Rust受信後の結果** を分ける。Rustは省略値をdefaultで補うため、受信後だけの比較では「省略」と空配列の差を検出できない。

全8イベントで出すキーは `schema_version / game / adapter / observed_at / event / sequence / smell_observation / player / world / visual_threats / auditory_threats / passive_mobs / companion_candidates / inventory / nearby_resources / dropped_items / recent_block_breaks / combat / meta`。該当配列が空でもキーは出す。

| event.name | source / priority / certainty | 固有条件 |
|---|---|---|
| `status_snapshot` | system / background / high | `ambient_sounds`を常時付加。最大12件、空なら`[]` |
| `threat_approaching` | visual / urgent / high | visual/audioを添付。`ambient_sounds`は省略 |
| `hostile_audio_detected` | auditory / normal / medium | visualは`[]`、audioは未視認分。combatの敵引数は別の全scan結果 |
| `ambient_mob_detected` | visual / background / high | visual/audioは`[]`、combatへ渡す両一覧も空。保持中のcombat状態や結果台帳を勝手に消さない |
| `creeper_detonated` | system / urgent / high | `combat.hostile_outcomes`を今回の爆散集合だけに差し替える |
| `hostile_defeated` | system / urgent / high | 同欄を今回の非爆散死亡集合だけに差し替える |
| `player_died` | system / urgent / high | `meta.death_cause`は非空時に追加。状態・結果の消去前に構築 |
| `combat_ended` | system / normal / high | visual/audioは`[]`。combatActiveをfalseにした後に構築し、その後で結果を消す |

### 順序・省略・副作用

- 各builderは **blocks取得 → envelope生成 → sequence発行 → player生成 → worldコピー** の順。`observed_at` とsequenceはeventごとに一度生成する。Fabricは `event_id` を生成していない。
- world/blocksだけが同tickのframeでそれぞれ一度取得される。`buildPlayer / buildInventory / buildDroppedItems / buildLookTarget / buildCombat` はeventごとに実行される。分割だけで一括cacheにしない。
- `nearby_composition` はevent名で判断せず、frameのblocksに `types` がある場合に付く。snapshotと同tickの部分eventにも付き、snapshot外tickの部分eventでは省略される。
- `buildMeta` がpending user textを消費する。最初に構築されたeventだけに `meta.user_text` が付き、同tickの後続eventへ複製しない。
- inventoryのキー挿入順と配列順を維持する。Rust側で元のinventory順を保持するため、すべてのobjectをsortした比較だけで同一と判定しない。
- 匂い成分は `smell_id / category / valence / source_kind / effective_strength` の5項目。`suppression_reason` は非null時だけ、`rain_after_notice` は該当時だけ。方位・距離・entity IDは付けない。
- 雨上がりの `window_id` は `rainAfterSmellUntilTick` から作る。P3では匂い結果と窓IDを同じ時点の読取結果として渡す必要がある。policyの `Observation` だけ移すと旧builderに期限フィールド参照が残る。
- `player.vehicle`、`look_target`、custom name等の任意項目は現在の省略条件を維持する。遮蔽中の保持されたvisual敵の `environment` は `{}`。
- 友好代表は最大4件でnamed/tamed候補は別配列。`world.visible_villager_count` は制限前のambient scanから数え、eventごとのworldコピーへ付ける。
- `appendDragonCombatInfo()` はJSON付加だけでなく追跡状態も更新する。P12/P13で単一所有者へ移し、元の呼出時点・回数を保つ。
- HTTP送信時にEventClientがpayloadをdeep copyし、`command_results` を常時付加する。sessionは `X-Dogido-Session-Id` ヘッダー。builderのJSONと送信bodyを別々に比較する。

### 用意した比較ケース

基準データに8イベントと以下の追加条件を記録した。**ソース由来の期待条件であり、Minecraftから採取したgolden payloadではない。** 現時点では8builder全体の実出力fixtureは未取得。

| ケース | 比較する差 |
|---|---|
| 8イベントを同じ固定観測から組み立て | descriptor・常時キー・省略・配列順 |
| 同tickの複数event | world/blocks各1取得、sequence各1、user textは一度だけ |
| snapshot同tick / 別tickの部分event | nearby compositionの有無 |
| 匂いnone / suppressed / 混合 / 雨上がり | 成分5項目、抑止理由、窓ID、方向不在 |
| 可視性保持、named/tamed、村人複数 | 空environment、代表上限と全体countの区別 |
| vehicle / look targetのなし・あり | 欠落と明示値の区別 |
| 爆散と通常死亡とcombat end | 今回結果集合と台帳、clearのタイミング |
| Dragonの戦闘情報生成 | JSON生成時の追跡更新 |

既存の `WorldObservationFrameTest / SmellPolicyTest / ZombieScentPolicyTest / NearbyCompositionTest / PassiveMobSelectionTest / SoundObservationPolicyTest / DogidoCommandProtocolTest` は各部品の検証に使える。Adapterの8builderを直接検査するJavaテストは見つかっていない。P3で窓ID受渡し、P14で固定観測からのraw JSONを守る検査を加える。

Rustの `environment/ambient/tests.rs` はv2形式・方向復活の拒否、`notice/tests.rs` は差分、`tests/combat_core.rs / combat_names.rs` は部分event・死亡と消失の扱いを検査する。`fixtures/combat-name-updates.jsonl` 等はRust側の保存済み結果であり、Fabric実出力と表示しない。ルートの旧部分入力fixtureも現行goldenへ流用しない。

## 保全と復帰点

- JavaアダプターのGit管理対象と、匂い方位撤去の25パスを合わせた **83パス** を記録。現在存在する81ファイルを、リポジトリ相対名でarchiveへ保全し、2削除をmanifestへ記録した。
- 保存先: `.dogido_tools/checkpoints/fabric-adapter-split-p1-20261009/source-before.tar.gz`。既存のGit除外先を使い、ソースの複製を製品packageへ追加しない。
- archive SHA256: `34ac99225afc1b3d8a72a130bb7e73ceeb4a71fc86554545166d240f27d0df03`。全memberを開いてmanifestのSHA256と一致することを確認した。
- この保全は **未検証ソースの復帰資料**。検証済みGit checkpointではなく、リポジトリ全体の他の未コミット変更を再現するarchiveでもない。
- 復帰時はmanifestと現在の差分を確認し、必要なファイルだけを取り出す。archiveを一括上書きして、その後のユーザー変更を消さない。
- この基準記録・manifest・今回の説明更新だけをGit checkpointにする。コードの復帰は基準HEADとarchiveを併用する。複数作業を含む既存の未コミット変更を、ツリーをcleanにするためにまとめてcommitしない。

## 検証状況と次の区切り

| 項目 | 現在の状態 |
|---|---|
| 匂い撤去25パスと当時の結果hash照合 | 一致。ソース確認のみ |
| 全78フィールド・24グループ・reset・tick順 | 整理済み |
| 8イベントと追加比較条件 | 準備済み。実出力goldenは未取得 |
| archiveの内容hash照合 | 実施。動作検証とは別 |
| adapter READMEの旧匂い説明 | 現行仕様へ更新済み |
| Java匂い2クラスを選択した `test` | 22件成功。製品・テストのコンパイルも成功。全体テスト・通しbuildは未実施 |
| Rust `--lib` の `smell` / `rain_after` / `strength` | 14 / 7 / 8件成功（重複あり）。全体テストは未実施 |
| Minecraft / TTS / 常駐モデル | 未実施 |
| 復帰点 / P2開始 | 本記録のGit checkpointとarchiveを基準にP2へ進む |

P1で選択した関連検証に失敗はなかった。実施コマンドと後続Pの結果は[分割の実施記録](2026-10-09-fabric-adapter-split-results.md)へ記録する。過去の全体検査の失敗が解消されたことは、この関連検証だけでは断言しない。
