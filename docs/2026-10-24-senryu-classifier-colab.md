# 川柳専用8分類の学習準備（2026-10-24）

## 今回の範囲

ユーザー指定により、画面に川柳が表示されている間は川柳専用分類器へ切り替える方針とした。今回は学習データとColabノートブックを準備した。表示判定、本体の受付切替、採用モデルの差替えは未実施。

前回の890件版から川柳7分類359件と既存の対象外58件を抽出した417件を保持し、対象外4件と終了3件をtrainへ追加した。新たな合成例は作っていない。

- [今回の424件](../dev_tools/request_classifier/releases/2026-10-24-senryu-only-424/README.md)：train 349 / dev 39 / test 36。
- [表示・正解指定の記録](../dev_tools/request_classifier/curation/2026-10-24-live-feedback/README.md)：確率・推論時間・取消状態を提示どおり保持。`松松がいっぱいだね`を補正していない。取消表示は同じ学習行に重ねていない。
- 終了への追加はユーザーが指定した3文に限る。称賛一般を終了へ変更したり、終了分類だけで保存・未採用案の採用を実行したりする仕様変更は行っていない。
- 元417件の本文・分類・ID・注釈・順序・分割、8分類のID対応を保持。教材の固定commitは `3f0250cdb1838d231ca5f7411db8ded23a997be8`。

## Colabで実行

[ModernBERT_Ja_30M_川柳8分類424件_2026-10-24.ipynb](../dev_tools/request_classifier/experiments/2026-10-24-senryu-colab/ModernBERT_Ja_30M_川柳8分類424件_2026-10-24.ipynb) をColabへアップロードする。

1. 「ランタイム」→「ランタイムのタイプを変更」でGPUを選ぶ。
2. 「すべてのセルを実行」。教材・学習コードはノートブックに同梱済み。
3. 3シードの結果、分類別混同行列、誤分類、8分類のdev推移、今回追加した7件の学習内の結果を確認する。
4. 最後の `modernbert-senryu424-results.zip` をMacへ保存してからGPUを解放する。

入力先は新しい `/content/modernbert-senryu424`。同じフォルダが残る場合は停止し、前の結果を上書きしない。専用Python 3.11.14環境をColab内に作り、前回と同じ固定依存を使う。ColabでのGPU・依存導入・学習完走は未確認。

## 条件

| 項目 | 設定 |
|---|---|
| 基盤 | `sbintuitions/modernbert-ja-30m` |
| 基盤revision | `8cb03f54cb9e30e72459e5f1cedc6d89c7d8dcb5` |
| 分類 | 行指定3、具体変更3、終了、対象外の8分類 |
| 初期化 | 基盤モデル＋新しい8分類head、全パラメータ学習 |
| シード | 42・43・44、独立に3試行 |
| 学習率・epoch・batch | `5e-5`、10、16 |
| 入力 | 現在発話のみ、最大128 token、切り詰めなし |
| 計算 | float32 / SDPA、TF32無効 |
| 選択 | 各seedのdev macro F1、同点ならloss、早いepochの順 |
| 評価 | 全seedの選択を固定してからtest。平均と標本標準偏差も保存 |

基盤モデルから独立に学習し、本体の採用モデルは変更しない。dev/testは前回の教材設計で参照済みの診断で、未知の実機精度ではない。今回の追加7件はtrainであり、学習内の正解数を独立評価に数えない。

## 再利用と準備確認

[公開保存版](https://github.com/yukincom/AI-Curious-Community/releases/tag/modernbert-research-2026-10-24) の `source-evaluation-records-01.tar.gz` から、前回の `dataset.py`、`evaluation.py`、`train.py`、既定 `labels.json`、固定依存ファイルの5個をバイト単位で変更せず同梱した。既存の `--material-release` で今回の8分類を読むため、学習処理の新設はしていない。

前回のノートブックを基に、入力・出力名と教材commitを更新。結果表示は川柳8分類へ揃え、今回の7件の学習内確認と、Markdown改行の整形を加えた。学習コード自体は同一。出典とSHA-256は [source-provenance.json](../dev_tools/request_classifier/experiments/2026-10-24-senryu-colab/source-provenance.json)、条件は [training-protocol.json](../dev_tools/request_classifier/experiments/2026-10-24-senryu-colab/training-protocol.json) に保存した。

準備時に次を確認した。モデル学習・推論・実装テストは実行していない。

- 全424件のラベル・ID・正規化入力の重複・group分割・空context・既存学習コードのdata reportとの一致。
- 同梱31ファイル（うち1個は入力manifest）のSHA-256と、教材commit・保存アーカイブとの一致。
- 5つのコードセルの構文、入力と結果保存の対象、8分類の表示、学習用7件の参照先。
- 保存済みtokenizerだけで入力長を計測。trainは16–35、devは19–28、testは16–24 token。128を超える入力は0件。

[local-preparation.json](../dev_tools/request_classifier/experiments/2026-10-24-senryu-colab/local-preparation.json) に確認範囲を記録した。

再生成はリポジトリルートで次を実行する。公開保存版は事前に取得し、引数へそのファイルを指定する。教材が固定commitと異なる場合は停止する。

```bash
python3 dev_tools/request_classifier/experiments/2026-10-24-senryu-colab/build_notebook.py \
  --source-archive /path/to/source-evaluation-records-01.tar.gz
```
