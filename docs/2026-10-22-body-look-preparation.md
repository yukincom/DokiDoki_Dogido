# 明示された方向・プレイヤーへの向き変更の準備（2026-10-22）

状態: 実装済み、BERT完成時に接続。新しい分類名・整数ID・学習データは確定せず、現在の5分類は変更しない。「これ見て」も新しいBERT分類へ含める予定で、受付側にTODOを残した。

## 用意した入口

- `adapter/minecraft-fabric/src/main/java/dogido/fabric/body/BodyLookController.java` の `direction(expectedContext, actionId, Direction)`：ドギド自身の現在の `view_yaw` を基準に、前・右前・右・右後ろ・後ろ・左後ろ・左・左前を向く。
- 同じファイルの `player(expectedContext, actionId)`：現在の本人の目のワールド座標を渡して、その点へ向く。足元の座標や推測した身長を使わない。継続追尾ではなく受付時の一点を注視する。
- `BodyLookRuntime.create(connection)`：client threadの身体snapshotと本人の目の実座標を接続する準備済みfactory。現在は生成・登録していない。
- `BodyConnection.lookAt` → `BodySnapshotReader` → 既存 `BodyBridge.requestAction(LOOK_AT)` → 身体Modの `DirectedLook`。C2S/S2Cとaction結果の経路を共用する。

既存の `MOVE_TO/OWNER_FACE` はプレイヤーの顔が見える場所へ移動する行動であり、このその場の回転とは別。発話中注視の20tick観測とも分け、座標から角度への変換は既存 `BodyVisionGeometry.facing` を共有する。

## 座標と実行

Minecraftのyaw 0度は+Z、正90度は-X。右が正回転。方向ごとの加算角は0、45、90、135、180、-135、-90、-45度とし、身体の眼から水平8blockの点を `x - 8 sin(yaw+角度), eyeY, z + 8 cos(yaw+角度)` で作る。斜めも長さ8blockで、プレイヤーの向きは基準にしない。

現在context、750ms以内の身体snapshot、有限座標、64block以内を検査する。送る観測tickは元のsnapshotから変えず、受付期限は20tick。重複action IDは元の座標・tick・sequenceで再送し、相対回転を再計算しない。既存の身体受付が所有者・世代・通番・期限・再送を再照合する。

身体API v10の `LOOK_AT` は注視点を移動先として使わない。待機・通常探索中に受け、呼び戻し・既存action・追従・危険・倒れた身体を優先する。向き変更中も通常planを確認し、優先行動や世代変更・停止で解除する。通常の上下浮遊は維持する。

実体のyaw/pitchを最大6度/4度ずつ毎tick合わせ、適用後の両角度が1度以内になって初めて `SUCCEEDED/facing_target` を返す。受付から100tick（通常20TPSで5秒）まで点を保持し、その後は通常の向きへ戻る。期限までに向けなければ `EXPIRED/look_timeout`。成功済みの保持終了を失敗に書き換えない。

## BERT完成後に残す接続作業

1. ユーザー確定の分類区分・整数ID・モデルmanifestに合わせ、Rustの `request_classifier.rs` と共通受付を更新する。方向抽出の契約も確定させる。現在は自然文の部分一致や仮ラベルを追加しない。
2. 検証済みの明示依頼を、既存のゲームイベント配送とsession/world世代照合を通じて上記2入口へ渡す。HTTP命令のparse/dispatch、capability公開、取消・重複管理、接続終了時clearはこの時に接続する。現行の観察MOVE_TOとの送信を直列化し、共有action sequenceの順序を保つ。
3. `queued`を成功として発話せず、context/action ID/sequenceが一致する身体の結果を待って結果ACKと返答へ接続する。
4. v10対応の身体Modとadapterを組で導入し、Minecraftで8方向・本人への回転・移動を伴わないこと・割込みを確認する。

## 検証状態

8方向の座標、身体yawへの追従、プレイヤーの目の送信、古い/別世代の拒否、同一要求の再送、実回転前の非成功、上下角、期限切れ、優先行動、既存actionの再送検査を自動テストで検証済み。

- 身体Mod: `./gradlew --offline test build` 成功、164 tests、失敗・skipなし。
- adapter: `./gradlew --offline test build` 成功、272 tests、失敗・skipなし。
- Rustは接続予定のコメントだけを変更し、分類コード・ラベル数は未変更。
- 読み取りレビューで見つかったFOLLOW以外の危険割込みを修正し、全テスト・buildを再実行した。

BERT接続、現行サーバーからのHTTP配送、稼働中Modへの導入、実Minecraftでの回転は未実施。
