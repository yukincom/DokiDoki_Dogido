# 通常対話3シードの重み合成（2026-10-24）

ユーザー指定により、通常対話16分類・578件のseed42・43・44を合成し、単体と比較した。**今回の13構成はすべて単体より低下した。** 単体seed43はdev 39/46・test 41/50。合成でdev macro F1が最良の42＋44全体平均はdev 26/46・test 27/50だった。

## 範囲

- 全体を等重みで平均：42＋43、42＋44、43＋44、3本全部の4通り。
- 最初のdev結果で全4通りが低下したため、平均値から `classifier.weight` と `classifier.bias` だけを構成元のseedへ戻す9通りを追加。`head.dense` 等は平均値を維持。
- 単体3本を含む全16候補のdev macro F1、loss、事前候補順で選択を固定した後にtest50件を評価。testによる構成・比率の調整はしていない。
- 学習に追加した実機15件・合成32件も別枠で確認。追加学習・本体差替え・サービス変更は0回。

元モデルを同じCPU環境で再評価したdev/testの予測は、Colabと全件一致した。合成13本の全テンソル、全2,288予測の指標、元ファイルのhashと選択順序を照合している。

[全結果・分類層保持の内訳](../dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-seed-merge/report.md)にまとめた。dev/testは既知の教材診断であり、未知の実機精度ではない。追加9構成はdevの低下を受けた探索的な比較として扱う。

## 再利用

過去の `merge_pilot650.py` と `inspect_ties_merges.py` を公開保存アーカイブから確認した。線形合成・分類層保持・保存後のテンソル照合という既存手順を使い、今回の3モデル用の実験ドライバーだけを追加した。モデル推論、入力整形、指標・誤分類の生成は、通常対話Colab結果に同梱された既存 `train.evaluate_checkpoint` と `evaluation.py` をhash照合のうえ再利用した。

既存環境 `.dogido_tools/request_classifier/.venv` を使用し、依存の追加や更新は行っていない。Python 3.11.14・主要依存はColabと同一。CPU・float32・SDPA・batch16・128 token上限で、元モデルも合成モデルも同じ条件で測った。

## 再実行

元データ・重みは前段の[Colab結果受領先](../dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-colab/results-review.md)に必要。ネットワークは使わず、各段階は既存の出力を上書きしない。

リポジトリルートで、最初の4通りのdev比較を実行する。

```bash
.dogido_tools/request_classifier/.venv/bin/python \
  dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-seed-merge/compare_seed_merges.py \
  --phase dev --output-dir /path/to/new-equal-weights-run
```

実施済みの追加比較は `compare_classifier_fixed.py --phase dev`、固定後の全16構成の診断は同じスクリプトの `--phase test` で行った。このスクリプトは記録済みの `runs/equal-weights` を入力として固定している。再実行時は `--output-dir /path/to/new-classifier-fixed-run` を両段階に指定する。

重みは実験フォルダの各run内 `weights/` に保持し、Gitにはスクリプト・条件・全予測・結果のみ保存する。
