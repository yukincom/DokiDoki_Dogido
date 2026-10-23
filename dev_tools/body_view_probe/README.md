# ドギド視点の一枚撮影

Minecraftの身体視点画像に、同じ描画フレームのプレイヤー位置から緑枠と`PLAYER`を付け、長辺192pxへ縮小する独立診断です。Gemmaへの送信は画像確認後に一回ずつ行います。このコマンドは独立診断用です。通常会話では[本体接続](../../docs/2026-10-21-chat-image.md)が同じ撮影処理を自動で使います。

1. Pillowが使えるPythonで、撮影前に受信を開始します。`--game-dir`は実際のMinecraftフォルダへ置き換えます。

   ```sh
   python dev_tools/body_view_probe/probe.py watch --game-dir "Minecraftフォルダ" --output .dogido_tools/body-view/run-01
   ```

2. 試験用adapterを導入したMinecraftでワールドを開き、身体を出して`/dogido-view prepare`を実行します。受信側の`watcher.json`が新しい`updated_at`と`status=ready_to_capture`になったことを確認します。待機は10分、受信側は既定30分です。
3. **受信準備の確認後**、身体の前方にプレイヤーと村人が見えるよう立ち、`/dogido-view capture`を実行してチャットを閉じます。次の一描画だけ身体の目の位置・向きで撮影し、通常カメラへ戻します。
4. `received/`の記録と、記録内の`annotated.path`の192px画像を確認します。原寸画像は同じフォルダの`frame.png`です。枠が正しいことを確認した一枚だけ送ります。

   ```sh
   python dev_tools/body_view_probe/probe.py send --game-dir "Minecraftフォルダ" --output .dogido_tools/body-view/run-01 --capture view_撮影ID
   ```

送信先は既存ローカルGemma（8080）です。共通Baseと前の村人比較と同じ質問・設定を使用し、結果を`*-result.json`へ残します。再送・自動リトライ・TTSはありません。撮影に5055本体の起動は必須ではありません。

画像はMinecraftの`dogido-vision/captures/`だけへ置き、最終利用から24時間後、Minecraft稼働中または次回起動時にhash一致を確認して削除します。記録とモデル返答は残ります。通常のスクリーンショット・句の画像は対象外です。JavaとPythonは同じ`retention.lock`で期限更新と削除を排他し、異常終了でlockが残った場合は削除を保留します。

枠は描画時の身体寸法を画面へ投影したものです。腕・持ち物の輪郭や他entityによる遮蔽を保証しません。泳ぎ・睡眠・滑空・乗車、不可視、遮蔽、near面交差、画面外では枠を付けられない場合があります。初回は地上で立ち止まって確認します。[設計・検証記録](../../docs/research/2026-10-21-body-view-probe.md)

```sh
python -m unittest discover -s dev_tools/body_view_probe -v
```

2026-10-21以降の192px画像とPLAYER枠はMod内で作ります。この診断は完成画像のhash・寸法・身体contextを検証して再利用し、画像の再描画はしません。通常会話への接続は[会話画像の仕様](../../docs/2026-10-21-chat-image.md)を参照してください。
