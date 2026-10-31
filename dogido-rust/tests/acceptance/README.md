目的: 8パートの受け渡しと保存を確認します。冒頭に目的、各テスト直前に狙いを200字以内で書いています。実機・実モデルの品質は別途確認します。

| パート | テスト |
| --- | --- |
| 1 | [位置の主語と視点](position.rs) |
| 2 | [履歴の記録と復元](history_metadata.rs) |
| 3 | [履歴表示と今回の用件](history_display.rs) |
| 4 | [聴覚の事実と辞書の区別](hearing.rs) |
| 5 | [画像の未比較と変化](part5_image.rs) |
| 6 | [身体行動のJSON例](part6_body_schema.rs) |
| 7 | [身体の実行結果](part7_body_results.rs)・[会話への引継ぎ](part7_body_context.rs) |
| 8 | [川柳の具体変更](part8_workshop.rs)・[保存材料との接続](part8_materials.md) |

リポジトリ直下から実行。番号を省略すると、この8パートを順に確認します。

```sh
python3 dogido-rust/tests/acceptance/run.py --part 3
python3 dogido-rust/tests/acceptance/run.py
```

RustがPATHにない環境は `DOGIDO_RUST_TOOLCHAIN_DIR`、ビルド先は `CARGO_TARGET_DIR` を指定。パート5はJDK21以上と既存Gsonを使います。見つからなければ `JAVA_HOME` と `DOGIDO_ACCEPTANCE_GSON` を指定します。

CIのRustジョブはJDK21とUbuntuの `libgoogle-gson-java` を用意し、`DOGIDO_ACCEPTANCE_GSON=/usr/share/java/gson.jar` で画像のJava境界試験も実行します。ローカルのGradleキャッシュはCIの前提にしません。

[結果](results/latest/summary.json)と各ログを残します。終了コードは0=全成功、1=実行済みの失敗、2=準備不備。0件実行やignoredは成功にしません。

説明検査: `python3 dogido-rust/tests/acceptance/check_explanations.py`。実モデル用のパート8は [別の実行手順](part8_model.md) を使い、通常の8パート実行には含めません。

パート4・5は[見込み・合格条件・確認範囲](part45.md)を各テストにも記載。モデルの返答評価は[別ケース](part45_model_cases.json)として未実行のまま管理します。`run.py --part 4 --part 5` はモデルを呼びません。パート5のHTTP先はその試験内の固定応答サーバーです。
