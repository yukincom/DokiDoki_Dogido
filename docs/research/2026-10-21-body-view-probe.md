# ドギド視点・座標枠・192pxの単発診断

2026-10-21。状態: **実装・導入済み。自動検査はadapter231件・Python8件通過。11:14に実Minecraftでドギド視点の一枚を取得し、同frameの枠と通常カメラ復帰を確認。192×108画像でGemmaが中央のプレイヤーと右の村人を区別した。単一場面の結果。**

ユーザーの「ドギド視点で撮影して、その瞬間の座標からプレイヤーに枠を付け、192pxでGemmaに渡す」という依頼を受けた独立試験。[前の村人判別](2026-10-21-vision-player-villager.md)でカメラの持ち主という説明が出たため、対象の本人確認をコードで済ませ、モデルへ見える手がかりを渡す。

## 入力と責務

- 既存`BodyConnection`で本人・身体UUID・世代・次元と1秒未満のsnapshotを検証する。身体制御や本体HTTP経路を追加しない。
- clientで同じ身体を解決し、実描画補間の位置＋目の高さ、body yaw、pitchを一描画だけのCameraへ固定する。参照snapshotはidentity用で、画像の座標にserver tickを代用しない。
- WorldRendererの実引数のview・projection行列、ownerの実描画stateと描画offsetを同じframeでコピーする。世界の描画直後・手とHUDの前に既存`ScreenshotRecorder.takeScreenshot`でframebufferを読み取る。
- local playerの描画除外だけ撮影中に解除し、身体自身は一人称として隠す。元Cameraへの復帰はfinallyで行う。身体・playerの座標や向きは変更しない。
- playerの描画寸法Boxをcamera相対にして投影し、画像境界で切る。near面を跨ぐ場合は不成立。姿勢はSTANDING/CROUCHING、非乗車だけを初回の枠対象とする。
- 遮蔽確認は既存BodyVisibilityの3本のblock ray。entityの画素マスクではなく、腕・持ち物の全体を囲む保証はない。実画像との目視照合が必要。
- 身体モデルのpitchは現行ソースと導入済み0.7.10 JARのbytecodeで使用を確認。旧`BodyPoseObservation`の0固定コメントは撮影姿勢に流用しない。

保存する`frame.json`に、camera位置・yaw/pitch・行列・補間係数・owner描画Box・投影枠・画像hash・撮影時刻・参照snapshotの年齢・Camera復帰を残す。通常の会話観測、changes、川柳材料、現在視認へは昇格させない。

## 受信とGemma

[手順](../../dev_tools/body_view_probe/README.md)。受信を先に開始し、今回のbuild・新しい起動ID・更新時刻・身体contextを含む`ready.json`を確認して初めて開始を案内する。古いreadyファイルやwatcherの起動だけをもって実機準備完了とはしない。

受信は画像hash・寸法・context・時刻・復帰・枠を検証し、元画像を保ってPillow LANCZOSで長辺192pxへ縮小する。同じ縦横倍率を枠へ掛け、1pxの緑線と`PLAYER`を描く。予測位置や別時刻の座標は使わない。

目視確認後、既存[解像度試験の一回通信](2026-10-20-vision-resolution/run_experiment.py)を再利用し、共通Base＋前回と同じ質問をローカル8080へ送信する。temperature 0、seed 42、max_tokens 400、thinking false、cache_prompt false、履歴なし。自動再送・音声化・本体会話への接続はしない。枠によって本人IDの手がかりを与える条件なので、画像だけで本人を同定できたという評価にはしない。

## 失敗と保持期限

予約とGPU待機は10秒。callbackが来ない場合も解放し、遅れた旧callbackが次の撮影状態を完了させない。描画失敗や条件変更は画像を公開せず、保存途中の失敗では今回作成した専用ファイルだけを回収する。

画像はMinecraftの専用`dogido-vision/captures/view_UUID/`へ置く。原寸と192px画像を一つのmanifestで管理し、最終利用から24時間後にhashと名前を検査して削除する。Minecraft停止中は次回起動まで残る。通常screenshotsと句の画像は対象外。Javaの削除とPythonの利用は同じmkdir lockで排他し、残留lockは自動破棄せず保留する。メタデータ・結果本文は削除しない。

## 検証の境界

- adapter: `./gradlew --offline test build`、231 tests、失敗0。投影、期限、遅延callback、世代、共有lock、期限更新、変更画像の保護、保存失敗後の回収を含む。
- Python: `python -m unittest discover -s dev_tools/body_view_probe -v`、8 tests。座標と画像の縮小一致、原寸保持、hash・期限・世代・Camera復帰・枠検査、共有lock、保存失敗、readyの鮮度。
- ゲーム・GPU・実モデルを使う確認は上の自動検査に含めない。後述の一枚でMixin適用、保存、枠の適合、モデル回答を確認した。全姿勢・遮蔽条件・カメラ復帰の体感は網羅していない。

## 導入と待機（11:10 JST）

Minecraft停止をプロセスで確認して、adapterのみ差替え済み。元JARはMinecraftの`dogido-mod-backups/body-view-20261021T020811Z/`へ保存し、SHA-256一致を確認した。身体Modは0.7.10のまま。

- 新JAR: 900448 bytes、SHA-256 `45cdc997736d7a9aa8a1d8bcb5dd839b9334ec4dd7b8904a66ad036af1aee082`
- 旧JAR: SHA-256 `24c1b53971f56d8f27b8838d3a6bd86b56f25847145e431ed44b1b3005d63f68`
- 記録: `.dogido_tools/body-view/deployment.json`
- 受信: `.dogido_tools/body-view/run-20261021T021045Z/`、11:10:45 JSTから1時間待機。起動時のstatusは`waiting_for_minecraft`。実機準備完了ではない。
- Gemma: 8080のlistener PID 91525と引数を確認。Gemma 4 26B-A4B QAT UD-Q4_K_XL / mmproj-F16 / build-14aの既存プロセスを維持。画像は未送信。

導入先のPython再検査は、画像ライブラリのある既存Python 3.11.14（Pillow 12.2.0）を明示して8件通過。先に別Pythonが選ばれた起動はPillow import前後で失敗し、画像処理・モデル送信は行っていない。

## 初回の実機結果（11:13〜11:15 JST）

11:13:04にユーザーがゲーム内の`/dogido-view prepare`を実行。受信側で今回のbuild・新規起動ID・身体context・新しい準備時刻を11:13:05に確認してから、撮影開始OKを案内した。先に観測を開始し、開始後に記録準備する順序にはしていない。

11:14:17.885に`view_8630fbbd-dcd4-449c-a05e-a79d5d8d7de4`を撮影。854×480の原寸にplayerを描画し、同frameの投影枠はleft=421.473、top=213.558、right=468.207、bottom=329.565。参照snapshotの年齢210msは本人確認用で、枠の計算にはclientの当該描画stateを使った。`camera_restored=true`。受信側は192×108へ縮小し、枠を[94,48,106,75]へ変換した。

目視では、中央のプレイヤーがコンポスターの背後にいて頭が見え、右手前に農民の村人がいる。緑枠は頭を含む本人位置に対応するが、体の隠れた部分ではコンポスターの画素も含む。身体寸法の投影枠であり、見えている画素の切抜きではない。

11:15:01にGemmaの返答を取得。HTTP往復1.334秒、prompt 349 tokens、completion 80 tokens、cached 0。TTSは実行していない。

> お、マインクラフトのスクリーンショットやな！任せとき！
>
> プレイヤーは画面の真ん中、緑色の枠で囲まれて「PLAYER」って名前が出てるやつやな。
> 村人は、プレイヤーの右側に立ってる、麦わら帽子みたいなのを被ったキャラやで。
>
> これで見分けられるはずや！次は何するん？

**この一枚では対象と左右関係を区別できた。** プレイヤー同定はコードの枠・ラベルに依存する条件で、Gemma自身が外見だけで本人を認識した証拠ではない。枠なし同一画像の比較、他の角度・衣装・複数プレイヤー、見切れや完全遮蔽、継続的な通常会話への効果は未検証。

[画像hash・座標・条件・返答の記録](2026-10-21-body-view-probe/result-01.json)。画像本体は期限管理下に保持し、docsへ複製しない。受信側の詳細記録は上記runフォルダにある。
