# ModernBERT 学習教材

プロジェクトごとの分類ラベル・教材CSVから、無料T4向けのColab Notebookと教材ZIPを作るMacアプリ。Excelで編集してドロップし、Colabの「学習する」「テストする」「結果ZIPを保存」で操作する。

[日本語の使い方](使い方.md)。ドギドの元教材・既存CSVは保持し、新規プロジェクトへ混ぜない。新規プロジェクトは本文だけ、ドギドは従来の入力整形を使う。学習は公開ベースのModernBERT-Ja 30Mからの再学習。

ビルド：

```sh
python3 apps/dogido-training-builder/Tools/build_app.py --output .dogido_tools/training-builder/ModernBERT\ 学習教材.app
```

標準PythonとAppKitを利用し、ad-hoc署名を使う。共通の`train.py`・`evaluation.py`を再利用する。プロジェクト定義、分類CSV、入力形式、教材・コードのhashをNotebookと結果へ残す。

確認：`env DOGIDO_RUN_TRAINING_SMOKE=1 .dogido_tools/request_classifier/.venv/bin/python -m unittest discover -s tests -p test_training_builder.py -v`。プロジェクト分離、元教材保持、用途間混入拒否、小型CPUモデルの学習と独立評価を検査する。無料T4上の実学習とモデル品質は未確認。
