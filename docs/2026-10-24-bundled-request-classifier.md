# 採用分類モデルの本体同梱（2026-10-24）

890件版の元seed42・23分類を `models/dogido-request-v7-seed42/` に同梱する。研究フォルダへの絶対パスを使わず、通常導入と家庭用セットの両方で同じ重みを使う。分類ラベル、推論上限、重み・tokenizerの内容は変更しない。

## 導入

Git LFSを導入した環境で `git lfs pull` を実行し、本体の `.venv` に `python -m pip install -e ".[request-classifier]"` で推論依存を入れる。`.env.example` をコピーすると本体内のモデルと `.venv/bin/python` を参照する。既存環境の `DOGIDO_REQUEST_CLASSIFIER_PYTHON` は準備済みの別Pythonを指定できる。学習用のaccelerateやscikit-learnは推論に不要。

`python dogido-rust/scripts/launch_dialogue.py --settings-dir . --check` は従来の採用記録・全4ファイルのSHA-256検査を行う。LFSのpointerだけがある場合は取得コマンドを示して停止する。モデルや依存を起動時に自動ダウンロードしない。

## 配布と出典

- `model.safetensors`: 147,115,660 bytes、SHA-256 `8645b0c742c9f71df13e940e252619648df04b1243644c4a705b5d20a3256bbd`。
- tokenizer・設定・採用記録・MITライセンスは同じフォルダに置く。Git LFSの対象はこの採用重み1個に限定する。
- 基盤: [SB Intuitions ModernBERT-Ja-30M](https://huggingface.co/sbintuitions/modernbert-ja-30m)。同梱の基盤LICENSEを保持する。
- 学習・評価・旧モデルの記録: [知りたがり屋](https://github.com/yukincom/AI-Curious-Community/tree/main/modernbert-request-classifier-study)。採用記録の `distribution` に移設元のcommit、採用記録hash、元のcheckpoint相対パスを残す。
- 家庭用セットの作成も同じ7ファイルをコピーし、初期設定時に分類用推論依存を準備する。個人の記憶・設定は配布しない。

## 検証

- Python全体検証: 206 passed、11 skipped、22 subtests passed。モデル完全性、LFSポインターの案内、通常導入・家庭用配布の接続を含む。
- `launch_dialogue.py --settings-dir . --check` 成功。実環境の設定も本体内のモデルと既存の推論用Pythonへ接続した。
- 同じCPU環境で移設前後を8発話・全23確率で比較し、最大絶対差0。採用モデル4ファイルのSHA-256も一致した。精度の再評価ではない。
- 公開commit `7ddc1ee` を別の空ディレクトリへ新規cloneし、Git LFSから重みを再取得。4ファイルのSHA-256、リンクを使わない配置を確認した。取得先のworkerとモデルでも同じ8発話・全23確率が完全一致。Python依存は既存の検証済み環境を利用し、新規環境への依存インストールは再実行していない。
- 同commitの [GitHub CI](https://github.com/yukincom/DokiDoki_Dogido/actions/runs/40047090626) 成功。
- Minecraftでの操作・実音声は今回の確認対象外。
