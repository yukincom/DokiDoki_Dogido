# 通常対話用16分類のColab学習

2026-10-24に実験用モデル・Notebook・ZIPを[公開保存版](https://github.com/yukincom/AI-Curious-Community/blob/main/modernbert-request-classifier-study/full-archive/README.md)へ移し、ローカルコピーを削除しました。以下は保存版から取得して再実行する手順です。

[公開ノートブック](https://github.com/yukincom/AI-Curious-Community/blob/main/modernbert-request-classifier-study/updates/2026-10-24/dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-colab/ModernBERT_Ja_30M_%E9%80%9A%E5%B8%B8%E5%AF%BE%E8%A9%B116%E5%88%86%E9%A1%9E578%E4%BB%B6_2026-10-24.ipynb)をColabへアップロードし、GPUを選んで「すべてのセルを実行」。教材と学習コードは同梱済みです。

- [578件の教材](../../releases/2026-10-24-normal-dialogue-578/README.md)：train 482 / dev 46 / test 50。今回の合成例32件はtrainへ追加。
- seed42・43・44、学習率5e-5、各10 epoch。前回の学習コードを変更せず、基盤モデルから16分類を学習します。
- 最後に `modernbert-normal578-results.zip` を保存してください。3つの選択モデル、全予測、誤分類、各epochのdev結果、実機15件・合成32件の学習内確認、入力と実行記録を含みます。
- 2026-10-24に3シードの学習結果を受領・照合済み。testは38/50・41/50・38/50。本体への接続は未実施。dev/testは既知の診断データです。

[結果と誤分類](results-review.md)・[準備・再生成の記録](../../../../docs/2026-10-24-normal-dialogue-classifier-colab.md)
