# 通常対話の専門別追加学習と合成（2026-10-24）

## 結果

共通seed43から向き変更・呼び出し／呼び寄せ・剣を重点学習した3本を作り、Task Arithmetic 3構成とTIES 2構成を比較した。元モデルtest 41/50に対し、合成の最高は40/50。今回の条件では改善しなかった。本体採用は変更していない。

[教材量・全結果・次の収集目安・限界](../dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-specialist-merge/report.md)

## 条件

- ユーザー指定の「まず既存教材で試す」に従い、578件版のtrain 482件のみ使用。向き198件、呼び出し288件、剣150件。各モデルは担当分と担当外が1対1、全16分類のラベルを保持。
- 同じ学習済みseed43を全テンソルごと複製。各3 epoch、学習率1e-5、seed42。対照として3教材の同じ出現回数を1本で学習。
- 基盤・分類層の共通初期化を照合。既存の入力・Trainer・推論・指標集計を再利用し、専門別教材と分岐学習の実験ドライバーを追加した。
- TIESは以前の実験で保存した作者実装（commit `44e7891fc84f3de7e4caa52664cd864ca3715e91`）の4関数とライセンスを再利用。作者モジュール全体をimportしていない。
- 各モデル最終epochと5合成条件は実行前に固定。全10候補をdevで選択後、testを全件記録。全960予測・5合成モデルを独立照合済み。
- 元モデルは既に元train 482件をすべて正解していた。新規教材の学習効果、データ量の最適値、未知の実機精度、複数seedでの再現性はこの比較から確定しない。

## 再実行

既存の通常対話Colab結果が元の実験フォルダに必要。過去runを上書きしないため、新しい出力先を指定する。CPU・float32・4 threadで実行する。

```bash
.dogido_tools/request_classifier/.venv/bin/python dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-specialist-merge/run_pilot.py prepare --output /tmp/dogido-specialist-reproduction
.dogido_tools/request_classifier/.venv/bin/python dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-specialist-merge/run_pilot.py train --output /tmp/dogido-specialist-reproduction
.dogido_tools/request_classifier/.venv/bin/python dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-specialist-merge/run_pilot.py dev --output /tmp/dogido-specialist-reproduction
.dogido_tools/request_classifier/.venv/bin/python dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-specialist-merge/run_pilot.py test --output /tmp/dogido-specialist-reproduction
```

元のrunの独立照合は`verify_pilot.py`で実行する。結果は`runs/pilot/`、重みは同run内の`weights/`。重みと一時作業領域はGit対象外。元モデル・原教材・本体設定を変更しない。
