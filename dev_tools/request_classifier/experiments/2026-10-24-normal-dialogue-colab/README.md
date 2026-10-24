# 通常対話用16分類のColab学習

[ノートブック](ModernBERT_Ja_30M_通常対話16分類578件_2026-10-24.ipynb)をColabへアップロードし、GPUを選んで「すべてのセルを実行」。教材と学習コードは同梱済みです。

- [578件の教材](../../releases/2026-10-24-normal-dialogue-578/README.md)：train 482 / dev 46 / test 50。今回の合成例32件はtrainへ追加。
- seed42・43・44、学習率5e-5、各10 epoch。前回の学習コードを変更せず、基盤モデルから16分類を学習します。
- 最後に `modernbert-normal578-results.zip` を保存してください。3つの選択モデル、全予測、誤分類、各epochのdev結果、実機15件・合成32件の学習内確認、入力と実行記録を含みます。
- 準備済み・学習未実施。本体への接続は別作業です。dev/testは既知の診断データです。

[準備・再生成の記録](../../../../docs/2026-10-24-normal-dialogue-classifier-colab.md)
