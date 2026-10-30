# ドギド 学習教材

CSVをドロップして、Colabの無料T4向けNotebookと教材ZIPを作るMacアプリ。通常914件・川柳424件の元教材へCSVを用途別に追加し、公開ベースから再学習する。Colabの「学習する」「テストする」「結果ZIPを保存」で操作する。本体採用は別途。

利用者向け：[日本語の使い方](../../dev_tools/request_classifier/教材作成/使い方.md)。

ビルド：

```sh
python3 apps/dogido-training-builder/Tools/build_app.py --output .dogido_tools/training-builder/ドギド\ 学習教材.app
```

ローカル用のad-hoc署名を使い、キーチェーンの秘密鍵は利用しない。`builder.py`は標準ライブラリのみ。`train_colab.py`は既存`train.py`の入力・token検査・最良dev保存・予測整形と、`evaluation.py`の評価集計を再利用する。ビルド時に共通ソースと通常16分類・川柳8分類の固定教材を同梱し、ファイルhashを保存する。

確認：`env DOGIDO_RUN_TRAINING_SMOKE=1 .dogido_tools/request_classifier/.venv/bin/python -m unittest discover -s tests -p test_training_builder.py -v`。元教材保持・用途間混入拒否・小型CPUモデルの学習と独立評価を検査する。無料T4上の実学習は未実施。
