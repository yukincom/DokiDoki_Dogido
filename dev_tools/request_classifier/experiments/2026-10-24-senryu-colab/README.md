# 川柳専用8分類のColab学習

2026-10-24に実験用モデル・Notebook・ZIPを[公開保存版](https://github.com/yukincom/AI-Curious-Community/blob/main/modernbert-request-classifier-study/full-archive/README.md)へ移し、ローカルコピーを削除しました。以下は保存版から取得して再実行する手順です。

[公開ノートブック](https://github.com/yukincom/AI-Curious-Community/blob/main/modernbert-request-classifier-study/updates/2026-10-24/dev_tools/request_classifier/experiments/2026-10-24-senryu-colab/ModernBERT_Ja_30M_%E5%B7%9D%E6%9F%B38%E5%88%86%E9%A1%9E424%E4%BB%B6_2026-10-24.ipynb)をColabへアップロードし、GPUを選んで「すべてのセルを実行」。教材と学習コードは同梱済みです。

- [424件の教材](../../releases/2026-10-24-senryu-only-424/README.md)：train 349 / dev 39 / test 36。今回の対象外4件・終了3件はtrainへ追加。
- seed42・43・44、学習率5e-5、各10 epoch。前回の学習コードを変更せず、基盤モデルから8分類を学習します。
- 最後に `modernbert-senryu424-results.zip` を保存してください。3つの選択モデル、全予測、誤分類、各epochのdev結果、入力と実行記録を含みます。
- 2026-10-24に3シードの学習結果を受領・照合済み。本体への切替は未実施。dev/testは既知の診断データです。

[結果と誤分類](results-review.md)・[準備・再生成の記録](../../../../docs/2026-10-24-senryu-classifier-colab.md)
