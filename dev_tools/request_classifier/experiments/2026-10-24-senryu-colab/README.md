# 川柳専用8分類のColab学習

[ノートブック](ModernBERT_Ja_30M_川柳8分類424件_2026-10-24.ipynb)をColabへアップロードし、GPUを選んで「すべてのセルを実行」。教材と学習コードは同梱済みです。

- [424件の教材](../../releases/2026-10-24-senryu-only-424/README.md)：train 349 / dev 39 / test 36。今回の対象外4件・終了3件はtrainへ追加。
- seed42・43・44、学習率5e-5、各10 epoch。前回の学習コードを変更せず、基盤モデルから8分類を学習します。
- 最後に `modernbert-senryu424-results.zip` を保存してください。3つの選択モデル、全予測、誤分類、各epochのdev結果、入力と実行記録を含みます。
- 2026-10-24に3シードの学習結果を受領・照合済み。本体への切替は未実施。dev/testは既知の診断データです。

[結果と誤分類](results-review.md)・[準備・再生成の記録](../../../../docs/2026-10-24-senryu-classifier-colab.md)
