# ModernBERT 学習教材

プロジェクトごとの教材CSVから、T4向けのColab Notebookと教材ZIPを作るMacアプリ。CSVを保存して「教材ZIPを作る」を押すか、CSVをドロップする。分類用ModernBERTと、変更元・変更先を抜く日本語文字BERTは別のプロジェクト・コード・教材として出力する。

語句抽出は[語句抽出の使い方](語句抽出の使い方.md)を参照。`edit_span_project.v1` のプロジェクトでは原文にある語句から文字位置を求め、同じ句・派生の分割混入を検査する。公開baseから学習し、調整成績が3周改善しなければ停止、最良重みを固定後にテスト・回帰を評価する。

[日本語の使い方](使い方.md)。ドギドの元教材・既存CSVは保持し、新規プロジェクトへ混ぜない。新規プロジェクトは本文だけ、ドギドは従来の入力整形を使う。学習は公開ベースのModernBERT-Ja 30Mからの再学習。

ビルド：

```sh
python3 apps/dogido-training-builder/Tools/build_app.py --output .dogido_tools/training-builder/ModernBERT\ 学習教材.app
```

標準PythonとAppKitを利用し、ad-hoc署名を使う。共通の`train.py`・`evaluation.py`を再利用する。プロジェクト定義、分類CSV、入力形式、教材・コードのhashをNotebookと結果へ残す。

確認：`env DOGIDO_RUN_TRAINING_SMOKE=1 .dogido_tools/request_classifier/.venv/bin/python -m unittest discover -s tests -p test_training_builder.py -v`。プロジェクト分離、元教材保持、用途間混入拒否、小型CPUモデルの学習と独立評価を検査する。無料T4上の実学習とモデル品質は未確認。

語句抽出の編集プロジェクトを作る（既存の出力先は上書きしない）：

```sh
python3 apps/dogido-training-builder/Tools/prepare_span_workspace.py \
  --output 'dev_tools/request_classifier/教材作成/プロジェクト/ドギド・語句抽出' \
  --source-run .dogido_tools/edit_span_extractor/2026-11-04-organic22-run
```

`--source-run` を省略すると空のCSVを作る。個人の原文・合成教材はプロジェクト側へ保存し、アプリのソースや分類器の固定教材には埋め込まない。

語句抽出の検査は `python3 -m unittest discover -s tests -p 'test_span_training_builder.py' -v` と、`DOGIDO_RUN_SPAN_TRAINING_SMOKE=1` を設定した `test_span_colab_runtime.py`。保存テキストの小型CPUモデル接続試験と、ColabのT4実学習・精度確認を区別する。
