# 2026-10-09 Fabricアダプター分割 実施記録

[計画](2026-10-08-fabric-adapter-split-plan.md)のP1〜P17を完了。**構造分割・自動検証済み、Minecraft実機は未確認**。対象は書類側mainの未コミット変更を含む作業ツリー。分割前のGit checkpointは `456fad3533378c1f2caa97edc5e0ae822bb52ad0`。

ユーザーの追加指示に従い、挙動を変えない分割は確認待ちせず進める。途中は変更範囲の検証だけを行い、全体テスト・通しビルドはP17で行う。新機能・機能変更を入れる場合だけユーザーのコード確認を先に挟む。

## P1 — 分割前の基準

[基準記録](2026-10-09-fabric-adapter-split-p1-baseline.md)と[manifest](2026-10-09-fabric-adapter-split-p1-baseline.json)へ78フィールド、reset、tick順、8イベントを記録。81ファイルと2削除を復帰資料へ保全した。READMEの旧匂い説明を修正。製品コードは変更していない。

| 検証コマンド | 結果 |
|---|---|
| Fabric: `./gradlew --offline test --tests dogido.fabric.SmellPolicyTest --tests dogido.fabric.ZombieScentPolicyTest` | 22件成功、製品・テストコンパイル成功 |
| Rust: `./cargo.sh test --lib --locked --offline smell` | 14件成功 |
| Rust: `./cargo.sh test --lib --locked --offline rain_after` | 7件成功 |
| Rust: `./cargo.sh test --lib --locked --offline strength` | 8件成功 |

Rustのフィルターは一部重複する。全体テスト、通しbuild、Minecraft、実TTS、実モデルはこの段階では実施していない。

復帰点はこの基準記録を含むGit checkpointと、manifestに記したローカルsource archive。既存の他作業の未コミット変更はまとめてcommitしない。

## P2〜P16 — 分割した責務と確認

製品Javaの配置は `adapter/minecraft-fabric/src/main/java/dogido/fabric/`、テストは同じpackage構成。`DogidoClientAdapter` は5,291行から624行になった。機能の追加、観測規則・送信契約・閾値の変更は行っていない。

| P | 移動先・所有する状態 | 確認した境界 |
|---|---|---|
| P2 | `observation/smell/SmellPolicy`・`ZombieScentPolicy` | 既存規則と結果型を移動。方位を復活させない |
| P3 | `SmellObserver` | block候補cache・scan時刻・現在の匂い・局所天候・雨上がり期限を同じ担当へ集約。4件の境界テストを追加 |
| P4 | `observation/mobs/MobClassifier`・共用identity/environment値 | シュルカー覚醒状態と敵対分類をsound・敵・友好観測で共有。覚醒の保持とresetを検査 |
| P5 | `NearbyComposition`・`ObservationGeometry/Numbers/Visibility` | 範囲追跡のインスタンスは別々のまま、位置・視線の計算だけ共用。距離境界を検査 |
| P6 | `observation/player/PlayerObserver`・`ItemIdentity` | player・vehicle・hotbar・inventory・視線先・sleep/respawn。ベッド取得の遅延条件とdimension時の保持を維持 |
| P7 | `commands/HotbarCommandExecutor` | Minecraftメインスレッドでの期限・slot・期待item照合と操作。重複・ackの管理はprotocol |
| P8 | `observation/world/` の小クラスと `WorldObservationFrame` | 頭上・portal・地形・光・水・遮蔽。焚き火判定を匂いと共用し、world/blocksの遅延取得・コピーを維持 |
| P9 | `WorldObserver` | block範囲・portal・破壊履歴・雷・構造物cache。構造物照会のスレッドとvolatile公開を維持 |
| P10 | `observation/sound/SoundObserver`・`WorldSoundFacts` | 音queue・観測時刻・天候/予兆。共通tickを読み、同定・TTL・実再生音の扱いを維持 |
| P11 | `observation/mobs/AmbientMobObserver` | 友好/中立・村人・範囲追跡・近傍集計。既存選択器を再利用し、送信周期と署名はrootに残す |
| P12 | `ThreatObserver`・`BossTracker` | 可視性・個体追跡・死亡queue・爆発・結果・被弾・戦闘hint。boss状態を単一所有とし、5件の境界テストを追加 |
| P13 | `BossObserver`・`CombatObserver` | boss固有観測とcombat断片。イベントごとのdragon更新を先読みせず、死亡結果JSONは共用 |
| P14 | `observation/EventObservationCapture`・`protocol/EventJsonBuilder` | 8イベントの観測取得とJSON化を分離。固定観測16ケースを分割前builderの出力と照合 |
| P15 | `protocol/DogidoEventClient`・`DogidoCommandProtocol`・`WorkshopDisplayPayload` | HTTP/session/sequence・command/result/ack・表示parser。必要な接続設定3値だけを受け取る |
| P16 | `display/` の両HUD・state・配置・瞬き・animation | 同じsnapshotの配線、期限、world/session/barrierを維持。内部world補助の公開範囲を絞り、importと説明を整理 |

途中は変更範囲のテストと必要なコンパイルのみを行った。P5・P6・P8の移動途中に出た参照・引数不足は各段階内で解消し、未コンパイル状態を次段階へ持ち越していない。P2〜P16の読み取りレビューで未解決の指摘は残っていない。

### 状態と取得順

- rootが共通tick・送信周期/署名・接続/world参照・pending入力を保持し、既存の順番で各観測担当を呼ぶ。
- 全体resetとdimension/大移動resetを区別。後者ではtick・health・respawn・pending入力を消さない。bossの座標保持など元の非対称なresetも維持した。
- `WorldObservationFrame` が同じtick内で共有するのはworld/blocksのみ。player・inventory・dropped item・視線先・combatは各イベントで元の順番に取得する。
- blocks取得 → envelope時刻/匂い → sequence発行 → 各観測 → combat → meta/pending入力消費を維持。JSON builder自体はMinecraft世界を探索しない。
- 死亡/爆発通知、構造物照会、`HttpClient.sendAsync()` とcallback、`client.execute` の既存境界を維持。新しい送信queueや強制直列化は加えていない。
- Mixinの静的入口・`fabric.mod.json`・resource・設定キー・HUD操作名は維持した。Mixinファイルの変更はimportの整理だけ。

## P17 — 最終の通し確認

以下はP16の公開範囲整理まで反映して実行した結果。最後に2ファイルの空白行の末尾空白だけを除き、assembleを再実行した。製品jarは同一で、処理変更はない。分割前の古い結果の転用ではない。

| 実行場所・コマンド | 結果 |
|---|---|
| Fabric: `./gradlew --offline test build` | 19クラス・98テスト成功、失敗/エラー0。jar・sources jar生成成功 |
| Rust: `./cargo.sh test --lib --locked --offline` | ライブラリ全488テスト成功、失敗0、118.74秒 |
| Fabric: `./gradlew --offline assemble` | 最後の空白行の末尾空白整理後、jar・sources jarを再確認。製品jarは同一で再生成不要 |
| 対象差分・staged差分 | 今回の追加行に空白エラーなし。文書のみをcheckpoint対象とし、既存の別作業差分を除外 |

Java全体には匂い26件、表示25件、コマンド契約4件、各観測とJSON境界を含む。Rustにはイベント一括検証・session再接続・command/result/ack・嗅覚の現行契約などを含む。Rustの結合テスト全体・Clippyは今回実行していないため、過去の結合6失敗・警告5件が解消したとはしていない。Rust製品コードは本分割では変更していない。

### JSON比較の根拠と範囲

`src/test/resources/dogido/fabric/protocol/event-wire-baseline.json` は、保全したP1の8builderをそのまま使い、Minecraft観測の返り値だけを固定して生成した。新builderを正解生成器にしていない。時刻とsequenceを固定した8イベント×任意項目あり/なしの16ケースについて、キー・省略・配列順・値を比較した。`none/suppressed` とmetadata省略、world/blocksコピーの非破壊も別に検査している。

この比較はJSON組み立ての同値性を示す。Minecraft APIから得る実測値の正しさ、サーバーへの実通信・ゲーム操作・画面表示まで証明するものではない。観測取得順・sequenceと入力消費の回数・HTTP callbackの順序は分割前ソースとのコード照合で確認した。

### 実機確認

| 対象 | 今回の状態 |
|---|---|
| Minecraft起動・session接続/再接続 | 未実施 |
| 移動・dimension/world変更・切断・大移動 | 未実施 |
| 匂い・音・近傍・player/乗車・天候・構造物 | 未実施 |
| 明示hotbar操作・結果ack | 未実施 |
| 死亡・爆散・離脱・boss・workshop中断 | 未実施 |
| 両HUD・危険時barrier・実音声との連携 | 未実施 |

ビルドjarのMinecraftへの導入、常駐サーバーの再起動、実モデル・実TTS・実マイクの操作は行っていない。

### 復帰点と成果物

- 分割前: Git checkpoint `456fad3533378c1f2caa97edc5e0ae822bb52ad0` とP1記録のsource archive。
- 分割後: `.dogido_tools/checkpoints/fabric-adapter-split-p17-20261009/` に検証済みソースarchive、P1からの分割差分、各ファイルのbefore/after SHA256と検証結果を記したmanifest、JSON基準出力を作った `LegacyEventOracle.java` を保存。
- 製品jar: `adapter/minecraft-fabric/build/libs/dogido-fabric-client-0.1.0.jar`。Minecraftへの導入は未実施。
- 今回の分割は開始前から変更済みのソースに重なるため、製品コードは作業ツリーに保持する。既存の別機能差分までまとめてGit commitしない。文書のcheckpointと上記の復帰資料を区別する。
- push・tag・履歴書換えは行っていない。

復帰archiveは101ファイルを展開せず読み戻し、保存元と全件一致を確認した。製品jarにも新配置のクラスと既存entrypoint/Mixinが含まれ、移動前packageのクラスが残っていないことを確認した。

| 成果物 | SHA256 |
|---|---|
| `source-after.tar.gz` | `9633b7bbfb6df05ffd2e37ab82a0ed2a50f568880c4e74ef58d0e712960b26f1` |
| `split-from-p1.patch` | `9d34e7f5fb258d491df1e24b03a2106ada5b1c9e967072708de644ca30b8b30b` |
| 製品jar | `2ec3830399c6cd3e41ce55cfd56a319df170000c8ceb8973ec4d1461e8290fae` |

リポジトリ全体の `git diff --check` は、本分割で触っていないREADME本文・docs索引の別箇所・旧RAG計画のMarkdown改行用末尾空白を報告した。これらの既存差分は修正していない。今回の文書追加・変更行と分割したJavaの差分は別に検査した。
