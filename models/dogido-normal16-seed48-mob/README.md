# 通常対話の採用分類モデル

通常16分類の `seed48-mob`（epoch 2）。全818件を学習し、モブ情報と関連対比例の提示回数を増やしたモデルです。受領した4ファイルのバイト列を保ち、採用根拠・ラベル順・SHA-256を `adopted-model.json` に記録しています。

基盤は [sbintuitions/modernbert-ja-30m](https://huggingface.co/sbintuitions/modernbert-ja-30m)（MIT、同梱 `LICENSE`）。重みはGit LFSで配布します。

採用候補はtestを開く前のdev規則で固定しました。教材設計で参照済みのtestは45/50で、未知の実機精度ではありません。[受領結果](https://github.com/yukincom/AI-Curious-Community/blob/main/modernbert-request-classifier-study/updates/2026-10-24/dev_tools/request_classifier/experiments/2026-10-24-four-system-colab/results-review.md)と[本体の切替仕様](../../docs/2026-10-24-bert-mode-routing.md)を参照してください。
