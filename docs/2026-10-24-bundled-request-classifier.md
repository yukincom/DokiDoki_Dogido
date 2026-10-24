# 採用分類モデルの本体同梱（2026-10-24）

現在は、川柳8分類のseed42と通常16分類のseed48-mobを同梱する。研究フォルダへの絶対パスを使わず、通常導入と家庭用セットの両方で同じ採用済み2本を取得する。一つの入力は[現在の相談状態に対応する片方](2026-10-24-bert-mode-routing.md)だけへ渡す。

| 用途 | 同梱先 | 重みのSHA-256 |
|---|---|---|
| 川柳8分類 | `models/dogido-senryu424-seed42/` | `2e95a4f304867161adeff507ca5e5e2e89f13df0b7f0ac6bd257538dfc58411d` |
| 通常16分類 | `models/dogido-normal16-seed48-mob/` | `51c5aaf94c1b6f71cbd38e6d3b57b3aa0bfab0bad2f60408d3e488b2e6481b04` |

## 導入

GitHubの「Download ZIP」には採用済み2本のモデル本体を含める。Gitでcloneする場合はGit LFSを導入して `git lfs pull` を実行する。本体の `.venv` に `python -m pip install -e ".[request-classifier]"` で推論依存を入れる。`.env.example` をコピーすると本体内のモデルと `.venv/bin/python` を参照する。既存環境の `DOGIDO_REQUEST_CLASSIFIER_PYTHON` は準備済みの別Pythonを指定できる。学習用のaccelerateやscikit-learnは推論に不要。

`python dogido-rust/scripts/launch_dialogue.py --settings-dir . --check` は各モデルの採用記録・4ファイルのSHA-256検査を行う。LFSのpointerだけがある場合は取得コマンドを示して停止する。モデルや依存を起動時に自動ダウンロードしない。

## 配布と出典

- 重みはGit LFSで配布し、現在の取得対象を採用済み2本に限定する。tokenizer・設定・採用記録・MITライセンスは各モデルと同じフォルダに置く。
- 基盤: [SB Intuitions ModernBERT-Ja-30M](https://huggingface.co/sbintuitions/modernbert-ja-30m)。同梱の基盤LICENSEを保持する。
- 学習・評価・旧モデルの記録: [知りたがり屋](https://github.com/yukincom/AI-Curious-Community/tree/main/modernbert-request-classifier-study)。採用記録の `distribution` に移設元のcommit、採用記録hash、元のcheckpoint相対パスを残す。
- 家庭用セットの作成も各7ファイル、計14ファイルだけをコピーし、初期設定時に分類用推論依存を準備する。個人の記憶・設定は配布しない。
- 旧23分類 `models/dogido-request-v7-seed42/` は現在のGit追跡と配布指定から外し、開発環境の実体も削除した。公開保存版と過去のGit履歴は保持する。新規cloneと現在のソースZIPには旧モデルを含めない。利用者が既存cloneに取得済みの旧ファイルを自動削除する処理は設けない。
- 旧・実験用モデルと元ZIPは[研究用の保存版](https://github.com/yukincom/AI-Curious-Community/blob/main/modernbert-request-classifier-study/full-archive/README.md)から任意で取得する。研究Releaseへのリンクから通常導入時に自動取得する処理はない。

## 現行2モデルの確認

採用時のhash照合・全教材の再推論・モード切替の確認は[接続記録](2026-10-24-bert-mode-routing.md#通常モデル採用時の確認)に記載した。今回の配布整理では重み・推論処理を変更せず、Gitの取得対象と家庭用セットの対象が同じ2本であることを確認する。モデルの再推論やMinecraft実機試験はこの配布整理では行っていない。

- 公開commit `686266e` の[GitHub CI](https://github.com/yukincom/DokiDoki_Dogido/actions/runs/40094827056)は成功。
- GitHubのソースZIPへLFS実体を含める設定を有効にし、同commitのZIPを再取得した。モデル関連ファイルは2フォルダ・計14ファイルで、重み2本のサイズとSHA-256は上表の採用記録と一致。旧23分類モデルは含まれない。
- 研究用の重み・Notebook・受渡しZIPは公開保存版との照合後に開発環境から削除した。軽量な教材・予測・評価記録は残し、手元で利用するモデル本体は採用済み2本に限定する。

## 旧23分類の初回同梱時の検証記録

以下は旧23分類 `8645b0c742c9f71df13e940e252619648df04b1243644c4a705b5d20a3256bbd` を初めて同梱した時点の結果。現在の2モデルの検証結果へ読み替えない。

- Python全体検証: 206 passed、11 skipped、22 subtests passed。モデル完全性、LFSポインターの案内、通常導入・家庭用配布の接続を含む。
- `launch_dialogue.py --settings-dir . --check` 成功。実環境の設定も本体内のモデルと既存の推論用Pythonへ接続した。
- 同じCPU環境で移設前後を8発話・全23確率で比較し、最大絶対差0。採用モデル4ファイルのSHA-256も一致した。精度の再評価ではない。
- 公開commit `7ddc1ee` を別の空ディレクトリへ新規cloneし、Git LFSから重みを再取得。4ファイルのSHA-256、リンクを使わない配置を確認した。取得先のworkerとモデルでも同じ8発話・全23確率が完全一致。Python依存は既存の検証済み環境を利用し、新規環境への依存インストールは再実行していない。
- 同commitの [GitHub CI](https://github.com/yukincom/DokiDoki_Dogido/actions/runs/40047090626) 成功。
- Minecraftでの操作・実音声は今回の確認対象外。
