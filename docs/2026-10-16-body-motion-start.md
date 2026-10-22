# 3Dドギドの移動接続 — 2026-10-16

**0.7.0導入時の状態:** 身体0.7.0・対応adapterを配置済み。身体95件・adapter182件・Rust754件の自動テスト成功（Rustは既定の除外1件）。Clippy・通常起動用ビルドも成功。この時点ではゲーム内の動作確認は未実施。

**後継:** 0.7.1の[継続探索](2026-10-16-body-curiosity.md)で既見による打ち切りを撤去し、到着後の間と見回しを追加。この文書は0.7.0導入時の記録として保持する。0.7.0の移動はその後ユーザーから動作報告あり。

## 今回動かすもの

- `/dogidobody follow`で距離追従を開始。既定ではownerから16ブロックを超えたら14ブロック以内へ近づき、カメラの回転だけでは周囲を回らない。
- FOLLOWでは敵周辺・敵とownerの間・予測される敵の進路を避け、水平の逃げ道が閉じた場合は上昇候補を先に試す。物理衝突とロード範囲も移動側で検査する。
- 安全なFOLLOW待機中に、まだ扱っていない種類が観測候補へ入った場合、会話モデルが一件を見に行くか、その場に留まるかを選ぶ。前回選択から30秒以上、player入力から5秒以上空け、会話・警告・戦闘・専用対話を優先する。同じ種類だけでは周期的に再選択しない。
- 候補の収集は既存の身体基準の観測を使う。モデルが返す短い候補IDをコードで現物へ対応付け、対象と重ならない接近位置を選ぶ。天井に当たる場合は下側・横側の候補を試す。
- 危険・距離追従・呼び寄せ・ownerや世代の変化・モード変更を優先し、到着は身体からの実行結果で確定する。

座標・距離・安全性・移動はコードが担当する。モデルは現在の候補から興味の対象を選ぶ。既存のHTTPゲームイベント、命令結果、BodyBridgeを利用しており、追加のサービスは不要。

## 対応する構成

| 部分 | 対応版 |
|---|---|
| Minecraft | Java 1.21.11 / Fabric Loader 0.18.4以上 |
| 身体Mod | `cube-dogido-lab-0.7.0.jar` / Body API v6 |
| adapter | `dogido-fabric-client-0.1.0.jar` / build `2026-10-14-body-autonomy` |
| Rust | mainの`body::autonomy`と`dialogue::body_interest_runtime`を含むビルド |

姿勢・感覚のHTTPは`body_observation` v3のまま。制御状態と候補は`body_autonomy` v1、実行capabilityは`body.inspect_target.v1`。詳しい契約は[Body API](../animation/cube_dogido/cube-dogido-lab/docs/body-api.md)と[HTTP仕様](adapter-api.md#body-autonomy-v1)を参照。

## ゲーム内での最初の確認

1. 対応Modを配置した後、普段のサーバーとMinecraftをユーザーが起動する。
2. 既存の身体を使い、`/dogidobody status`で状態を確認する。必要な場合だけ既存手順でbindする。
3. `/dogidobody follow`を実行し、開けた場所でownerが離れたときの追従と停止を確認する。
4. まだ扱っていない花や友好Mobが見える場所で待ち、候補の選択・接近・到着を確認する。モデルはstayも選べるため、動かなかったという事実だけで不具合とは判定しない。
5. `/dogidobody direct`で自律追従・見物を終了できることを確認する。

診断ログの`body_interest_started`・`body_interest_selected`・`body_interest_result`と、身体の`activity/reason`を対応させる。命令の受付だけで到着したことにしない。

## 確認済みと残作業

- Java身体: 95件成功、失敗・除外0。0.7.0のJARを生成。
- Java adapter: 182件成功、失敗・除外0。API v6を参照してJARを生成。
- Rust: 全target 754件成功・既定の除外1件。Clippy全target成功、releaseビルド成功。実モデルは呼び出していない。
- 読み取りレビュー: FOLLOWの移動、観察移動の受理・重複・期限・取消・ID付き完了、owner消失、DOWNEDを追跡し、確定できる欠陥はなかった。
- 実Minecraftでのtick順序・見た目・接近の自然さは未確認。
- 以前準備した「敵視認またはゾンビ臭からownerへ高速帰還する」追加パッチは未統合。通常のFOLLOW内の安全回避とは別の残作業として保持する。

モデルの指示文・学習分類器・会話の知識検索は今回変更していない。学習素材の登録は別チャットへ引き継いだ。

## 導入記録

Minecraftの終了をプロセス一覧で確認し、旧0.6.1身体Modと旧adapterを退避して、上表の2本を配置した。新旧の身体ModやAPI専用JARを同時に入れていない。ゲームとサーバーは起動していない。

| 配置物 | SHA-256 |
|---|---|
| `cube-dogido-lab-0.7.0.jar` | `67e68dde6073e0e82c27a6f39a39f8104c21b80bf0d24139b020e759000f6ff0` |
| `dogido-fabric-client-0.1.0.jar` | `6bd5041226a2f6cad9d6f6d43e571a73e43121f8cc2cc2a2e35a1ddd2f418618` |

退避したMod、作業開始時のGit差分、未追跡の移動関連ソース、導入前後のハッシュは`.dogido_tools/checkpoints/2026-10-16-body-motion-start/`へ保存した。戻す場合はMinecraftを終了し、今回の身体Modを外して退避した0.6.1と旧adapterを組で戻す。保存済みワールドや会話記憶は変更していない。
