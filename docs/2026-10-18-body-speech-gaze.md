# 発話中の対象を見る（身体0.7.8）

状態: 実装・自動検査・身体0.7.8とadapterの配布用ビルド・Mod導入済み。再起動後のゲーム内確認は未実施。

「ゾンビがおる」「ウシの声がする」など、現在の対象について発話している間、その対象の方へ身体を向ける。

## 接続

- 既存の会話snapshotと実再生`started`を使う。生成待ち、再生完了・取消・失敗、沈黙では向きを要求しない。
- 固定警告は既存のvisual／auditory対象IDを使う。その他の発話は既存カタログの名前抽出と現在の視認・実音だけを照合する。モデル呼出しとプロンプトは増やさない。
- 観測元のplayer／bodyを対象IDと共に引き継ぎ、同じIDの別観測位置を混ぜない。同じ種類の姿と実音が共にある場合は実音の位置を優先し、複数の音は新しい順にする。一般名から「見えている別個体が鳴いた」と推定しない。
- adapterは同じ身体世代の既存collectorから実座標を取り、身体bridgeへ短い有効期限付きの視線観測を渡す。音は発音位置を使い、音だけで壁の向こうの個体位置を追跡しない。匂いから方向は作らない。
- 位置差からyaw／pitchを求める処理は、身体の眼からプレイヤーを視認する`BodyVisionGeometry`と共有する。Minecraftの南0度・西90度、pitchは下向きが正という座標系を使う。
- 移動と衝突回避は既存controllerが担当する。発話中の向きは見回しより優先し、呼び寄せ中のowner注視とダウン状態は既存動作を優先する。
- 終了・通信失効・世界や身体世代の変更で解除する。同じ音の再配送で音源の寿命を延ばさない。

既存のHTTP取得とFabric通信を拡張し、別のポーリングや操作toolは作らない。身体APIはv8へ更新し、旧v7接続は残さない。前方8・後ろ1・左右2・上下3の注意範囲、最短3秒の選択、到着後5秒は継続する。

## 夕方警告の優先順位

同日の追加依頼により、川柳の生成・前置き・本文読み上げ、開いているworkshopを夕方警告より優先する。既存の環境判断のfocusへ発句中の状態を渡し、終了後の現在観測でも必要な場合だけ一度警告する。[会話仕様](main-dialogue-integration.md#4-雷と夕方)。

## 検証結果

- 身体Mod: 142件通過、`test build`成功。共有座標計算の上下・yaw境界、観測期限、別世代、再配送、明示解除を含む。
- adapter: 204件通過、`test build`成功。同種の個体固定、音源座標、player／bodyの観測元の分離、voice off、session変更を含む。APIクラスをadapter jarへ重複同梱していないことも確認。
- Rust注視: 3件通過。実再生中だけの選択、現在観測との照合、同種複数個体、視認と実音の競合、複数の実音の新旧を確認。
- 模擬HTTP・音声: 注視3件、夕方優先2件通過。終了・取消で注視を解除し、川柳中とworkshop中は夕方に割り込まれず、終了後も夕方なら一度警告、昼なら古い警告を破棄する。
- Rust debug／releaseビルドと`clippy --lib -- -D warnings`成功。
- 全対象のRust検査では、作業開始時から変更していない`foreground_runtime::tests::closing_workshop_starts_a_fresh_automatic_haiku_interval`が失敗（経過時間の`< 60_000`）。この1件を除いた全対象検査は通過し、その後の音源選択修正は注視3件と模擬HTTPで再確認した。
- `clippy --all-targets -- -D warnings`では、上記の既存時計テストの`await_holding_lock`と、既存`combat_reply_tests`の`cloned_ref_to_slice_refs`が残る。今回の変更に含めて修正していない。

配布物は身体側の`build/libs/cube-dogido-lab-0.7.8.jar`とadapter側の`build/libs/dogido-fabric-client-0.1.0.jar`。対応するRustのreleaseもビルド済み。ビルド後は使用したGradleデーモン1件を停止した。常駐ドギド本体とMinecraftは再起動していない。

実Minecraftでの音声とのタイミング・向きの見た目は、Mod導入後の確認事項として残る。

## 導入記録

2026-10-18、Minecraftが終了していることを確認し、普段の起動構成の`mods`へ身体0.7.8と対応adapterを導入した。配布物と導入先のSHA-256一致、他のModが変更されていないことを確認済み。

| 導入物 | SHA-256 |
|---|---|
| `cube-dogido-lab-0.7.8.jar` | `656d99a020a7ac84d5bfa5823dda5b4dcb50a30c4ef90c3c3f2684b67a39b9e8` |
| `dogido-fabric-client-0.1.0.jar` | `541b76d2ca59597ce2c96002d58581ee1c2f2f3b66da11b7714a2bbc82cd942d` |

復元用は直前の身体0.7.7と前方8版adapterの一組だけを`.dogido_tools/checkpoints/2026-10-18-body-speech-gaze/mods/`へ保持し、古いバックアップ・重複ビルドjar 9件を削除した。Rust releaseのソース鮮度も確認済み。常駐ドギド本体は維持しており、変更の反映にはMinecraftの起動とドギド本体の再起動が必要。
