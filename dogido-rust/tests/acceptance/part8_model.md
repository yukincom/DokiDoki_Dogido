目的: 実モデルが指定語・保持案・置換語なし・提示材料の説明を扱えるか測ります。通常の受入試験とは別に実行し、生の要求・応答・保存結果を確認します。STT・BERT精度・Minecraft・実音声は対象外です。

ケース確認だけならモデルもサーバーも起動しません。

```sh
python3 dogido-rust/tests/acceptance/part8_model.py --list
```

測定時は検証対象のバイナリ・API・モデルを指定します。出力先は新規ファイルにしてください。

```sh
python3 dogido-rust/tests/acceptance/part8_model.py --binary /path/to/dogido-rust --base-url http://127.0.0.1:PORT/v1 --model MODEL --output dogido-rust/tests/acceptance/results/model.jsonl
```

終了コード1は自動検査失敗、2は自動検査済み・内容レビュー待ちです。各ケースのreview項目を確認するまで、意味の改善は合格にしません。

通信の配線だけを再確認する場合は、固定応答を使います。実モデル品質の結果には数えません。

```sh
python3 dogido-rust/tests/acceptance/check_part8_model_transport.py --binary /path/to/dogido-rust
```

材料説明ケースは、通常の一回返答または`inspect_verse`一回＋続きの返答を認めます。二回目の生成は、同じtool IDで本体の参照結果を受け取る場合だけです。既存3ケースの一回生成条件は変えません。

生成準備は現行の材料選択形式を使い、三行共通の材料IDと`provided_materials_unverified`を保存した句から開始します。材料の意味による選択・STT・BERTは固定です。各ケースの`expectation`・`review`・`scope`を先に確認してください。

[保存材料との接続と確認結果](part8_materials.md)。実モデルは未実行です。
