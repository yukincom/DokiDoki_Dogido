# 試験資料の構成と再実行

## 保存した内容

`image-review/` に3つの有効試験と1つの除外した予備試験を保存しています。各試験の `manifest.json` は入力画像・共通指示・条件・seed・判定の記録、`request-previews.json` は画像を相対パスで示した要求、`results.jsonl` は各要求プレビューとAPIの生返答、`REPORT.md` は一覧です。内容の最終確認は `RESULTS_REVIEW.md` と `review.json` を参照してください。

各 `pair_XX/` の `before/after-native.png` は実撮影の原画像、`before/after-model.png` は既存の192pxモデル用画像です。枠比較用の `after-grid.png`、`after-merged.png` も保存しています。採用方式ではこの変化枠を付けません。画像の生成・補作はしていません。

各試験の `sources`、`source_trial`、画像の `original_path`、過去の要求内にある絶対パスは当時の来歴を示すため書き換えていません。閲覧・再送信に必要な画像は試験フォルダ内の相対パスで読めます。`PROVENANCE.json` で複写前の場所と同梱ファイルを対応付けています。

`combined/` はこの試験を参照するための必要ファイルだけを複写した構成です。リポジトリ全体や実装済み本体ではありません。現行mainへディレクトリごと上書きしないでください。

## コードの役割

| コード | 役割 |
|---|---|
| [part5_model.py](combined/dogido-rust/tests/acceptance/part5_model.py) | 準備・要求作成・取得・一覧出力。既存の送信helperを使用 |
| [part5_grid.py](combined/dogido-rust/tests/acceptance/part5_grid.py) | 今回固定した8×6判定。描画関数は枠条件の比較用 |
| [part5_grid_presentation.py](combined/dogido-rust/tests/acceptance/part5_grid_presentation.py) | 位置と明暗の文、比較条件の結合枠 |
| [part5_model_prompt.json](combined/dogido-rust/tests/acceptance/part5_model_prompt.json) | 最初の実モデル診断の追加指示・設定 |
| [BodyViewPairMetrics.java](combined/dogido-rust/tests/acceptance/java/dogido/fabric/screenshot/BodyViewPairMetrics.java) | 初回の旧32×18指標を実画像2枚へ適用するfixture |
| [BodyViewChange.java](combined/adapter/minecraft-fabric/src/main/java/dogido/fabric/screenshot/BodyViewChange.java) | 初回の試験で参照した旧本体の比較コード |
| [run_experiment.py](combined/docs/research/2026-10-20-vision-resolution/run_experiment.py) | 再利用した既存のローカルAPI送信helper |

`code-before-grid/`、`code-before-location-merged/` は段階ごとの旧コード、各試験直下のPythonも取得時点の控えです。レビュー生成スクリプトは当時の絶対パスを含むため、記録として同梱しています。

## 取得条件

Gemma 4 26B A4B の `gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf` とF16 projectorを使う常駐llama-serverへ、既存の `default_model` aliasで逐次送信しました。temperature 0.65、seed 42/43/44、max_tokens 300、thinking無効、cache_prompt無効です。プロセス起動情報は各 `approval.json`、APIが返した情報は `model-info.json` に保存しています。現在の同aliasが同じモデルであることは、再実行前に別途確認してください。

すべて前後2画像、会話履歴なしの独立した診断です。原画像の比較と192px画像への提示を分け、同一試験内は条件以外の指示・画像・設定を固定しています。コード枠付きJSONは形式違反を記録しつつ内容を評価しました。再生成と追加の採点モデルは使用していません。

## 再実行する場合

本資料の配置だけで再実行は行いません。以下は試験担当向けの手順です。結果を含む保存先はランナーが上書きを拒否するため、必ず新しい出力先を使います。

準備はNumPyとPillowを備えたPythonを使用します。今回の参照環境はPython 3.11.14、NumPy 2.4.4、Pillow 12.2.0です。8×6試験は `prepare-grid`、位置説明と結合枠の試験は `prepare-grid-presentation` で準備できます。`--source-trial` には同梱の `prepared-v2` または `grid8x6-v1` を指定します。

```sh
python3 combined/dogido-rust/tests/acceptance/part5_model.py prepare-grid-presentation --source-trial image-review/grid8x6-v1 --output /private/tmp/dogido-image-repeat-new
python3 combined/dogido-rust/tests/acceptance/part5_model.py run --output /private/tmp/dogido-image-repeat-new --base-url http://127.0.0.1:8080/v1
```

この例は資料一式のルートを作業場所にします。出力先が存在すれば別の新規名に変えます。内容のレビュー待ちは終了コード2、取得・解釈エラーがあれば1です。完走だけを品質合格とせず、実画像とすべての返答を照合してください。

元の数値試験を画像選定から作り直す `prepare` は撮影アーカイブと共有プロンプトを読みます。この最小複写には現在の共有プロンプト全体を含めていないため、元リポジトリで対象を指定して使います。当時の入力を確認するだけなら保存したmanifestと要求を使い、本体のプロンプトへ復元しません。

## 期待値の照合

`golden-cells.json` は固定した検出器で同梱の実画像13組を再計測し、各manifestの全マスと一致することを確認して作成しました。縮小後RGBの配列は `[cell_row, cell_column, sample_y, sample_x, rgb]`、形は `[6,8,8,8,3]`、一次元化した0〜255の整数です。`samples_sha256` はこの順のuint8バイト列のhashです。各マスの測定値は元manifestと同じ小数4桁の記録で、判定は丸め前に行います。

ここにある期待値はJava実装の合格結果ではありません。移植後に実際のJava出力と照合します。既存の `part5_image.rs` と通信fixtureも参照資料として同梱していますが、新しい8×6契約や実機を確認済みという意味ではありません。
