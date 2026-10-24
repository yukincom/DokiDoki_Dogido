# 通常対話用16分類・578件のColab学習準備（2026-10-24）

## 教材

ユーザーが採用を指示した道具・雑談の合成例32件を、通常対話用546件のtrainへ追加した。**578件＝train 482 / dev 46 / test 50**。前版の本文・ID・順序・分割と、16分類のID対応を保持した。

- [578件の教材](../dev_tools/request_classifier/releases/2026-10-24-normal-dialogue-578/README.md)：元890件から川柳7分類359件を除いた531件＋実機15件＋合成32件。
- [今回の32件](../dev_tools/request_classifier/curation/2026-10-24-normal-dialogue-tool-contrast/materials-review.md)：剣依頼8件・対象外24件。出自は合成例のまま保持。
- [実機15件](../dev_tools/request_classifier/curation/2026-10-24-normal-dialogue-feedback-1548/README.md)：対象外14件・視線依頼1件。原文・表示確率・取消等の記録を保持。
- 教材の固定commitは `f8e1d0296068dfcd9ab583cb5604b9e0cfe06701`。

## Colabで実行

[ModernBERT_Ja_30M_通常対話16分類578件_2026-10-24.ipynb](../dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-colab/ModernBERT_Ja_30M_通常対話16分類578件_2026-10-24.ipynb)をColabへアップロードする。

1. 「ランタイム」→「ランタイムのタイプを変更」でGPUを選ぶ。
2. 「すべてのセルを実行」。教材・学習コードは同梱済み。
3. 3シードの結果、分類別混同行列、誤分類、16分類のdev推移、実機15件と合成32件それぞれの学習内の結果を確認する。
4. 最後の `modernbert-normal578-results.zip` をMacへ保存してからGPUを解放する。

入力先は `/content/modernbert-normal578`。既存フォルダがある場合は停止する。専用Python 3.11.14環境をColab内に作り、前回と同じ固定依存を使う。

## 前回と共通の条件

| 項目 | 設定 |
|---|---|
| 基盤 | `sbintuitions/modernbert-ja-30m` |
| 基盤revision | `8cb03f54cb9e30e72459e5f1cedc6d89c7d8dcb5` |
| 分類 | モブ情報・剣・乗用動物・ドギド呼び出し・視線11種・対象外の16分類 |
| 初期化 | 基盤モデル＋新しい16分類head、全パラメータ学習 |
| シード | 42・43・44、独立に3試行 |
| 学習率・epoch・batch | `5e-5`、10、16 |
| 入力 | 現在発話のみ、最大128 token、切り詰めなし |
| 計算 | float32 / SDPA、TF32無効 |
| 選択 | 各seedのdev macro F1、同点ならloss、早いepochの順 |
| 評価 | 全seedの選択を固定してからtest。平均と標本標準偏差も保存 |

dev/testは過去の教材設計で参照済みの診断データ。実機15件と合成32件はすべてtrainに含むため、これらの成績は学習内確認として表示する。未知の実機精度とは区別する。

## 再利用と準備確認

前回の[川柳424件ノートブック](../dev_tools/request_classifier/experiments/2026-10-24-senryu-colab/README.md)を、作成時のcommit `7c37a7d92342ff15da7fefbe5269514b75453c5c` から読み出して利用した。その同梱入力にある `dataset.py`、`evaluation.py`、`train.py`、既定 `labels.json`、固定依存ファイルの5個はバイト単位で同一。前回取得した公開保存版の5ファイルとも一致する。既存の `--material-release` で今回の16分類を読む。

ノートブックの変更は、教材・入出力名・教材commit・16分類の表示・追加行の学習内確認。学習条件は分類headを16分類に合わせた以外は前回と同じ。出典は [source-provenance.json](../dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-colab/source-provenance.json)、条件は [training-protocol.json](../dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-colab/training-protocol.json) に固定した。

準備時に次を確認した。

- 同梱58ファイル（うち1個は入力hash一覧）と、固定commit・前回学習コードの一致。
- 全578件のラベル・ID・正規化入力の重複・group分割・空context・既存学習コードのdata reportとの一致。
- 新規32件と実機15件が全件trainに含まれること。前版546件のtrainに32件をそのまま追記し、dev/test・ラベル対応はバイト単位で同一。
- 5つのコードセルの構文、保存対象、3シードの学習コマンドと前回の条件の一致。
- 保存済みtokenizerだけで入力長を計測。trainは16–36、devは17–28、testは16–36 token。128を超える入力は0件。

[local-preparation.json](../dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-colab/local-preparation.json) に確認範囲を記録。モデル読み込み・学習・推論・Colab実行・実装テストは行っていない。本体のモデル切替は別作業。

再生成はリポジトリルートで実行する。固定した前回ノートブックと教材のGit履歴を使うため、公開アーカイブの再取得は不要。教材が固定commitと異なる場合は停止する。

```bash
python3 dev_tools/request_classifier/experiments/2026-10-24-normal-dialogue-colab/build_notebook.py
```
