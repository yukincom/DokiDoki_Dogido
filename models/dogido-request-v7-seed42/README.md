# ドギドの採用分類モデル

890件版の元seed42、23分類。採用記録は `adopted-model.json`、各ファイルのSHA-256も同記録にあります。重み・tokenizer・設定は採用時のバイト列を保っています。

基盤は [sbintuitions/modernbert-ja-30m](https://huggingface.co/sbintuitions/modernbert-ja-30m)（MIT、同梱 `LICENSE`）。ドギド用に追加学習した分類器です。会話文の生成には使いません。

`model.safetensors` はGit LFSで配布します。リポジトリを取得した後、ルートで `git lfs install` と `git lfs pull` を実行してください。約147MBの重みと約6.7MBのtokenizerがあります。研究用フォルダやHugging Faceキャッシュへのリンクは使いません。

推論環境と起動設定は [本体のREADME](../../README.md#1-本体の準備と起動)、移設・検証は [記録](../../docs/2026-10-24-bundled-request-classifier.md) を参照してください。学習・評価・過去モデルは [知りたがり屋](https://github.com/yukincom/AI-Curious-Community/tree/main/modernbert-request-classifier-study) へ保存します。
