# 川柳専用424件・Colab学習結果（2026-10-24）

ユーザーが返却した `modernbert-senryu424-results.zip` を受領。予定した8分類、train 349 / dev 39 / test 36、seed42・43・44の3試行が完了している。GPUはNVIDIA A100-SXM4-40GB。各試行10 epochからdevで選んだモデルを保存している。

## 成績

| seed | 選択epoch | train | dev | dev macro F1 | test | test macro F1 |
|---:|---:|---:|---:|---:|---:|---:|
| 42 | 8 | 349/349 | 38/39 | 0.9654 | 36/36 | 1.0000 |
| 43 | 4 | 349/349 | 38/39 | 0.9708 | 35/36 | 0.9803 |
| 44 | 7 | 349/349 | 38/39 | 0.9708 | 35/36 | 0.9725 |

test正解率の平均は **98.15%**、シード間の標本標準偏差は **1.60ポイント**。test macro F1は **0.9843 ± 0.0142**。

今回追加した対象外4件と終了3件は、全シードで7/7正解。ただし、この7件はtrainへ加えた例である。

dev/testは教材設計で参照済みの診断データ。未知の実機精度ではなく、標準偏差もその信頼区間ではない。seed42は今回のtestで全問正解したが、ほかのシードとの差は1件で、dev macro F1では43・44が上回る。23分類からの分離の寄与を単独に測る同条件比較は行っていない。

## 残った誤分類

| seed | 分割 | 原文 | 正解 | 予測 |
|---:|---|---|---|---|
| 42 | dev | これはこのまま保存しましょう | `senryu_end` | `not_target` |
| 43 | dev | 今日の冒険は終了しましょうか | `not_target` | `senryu_end` |
| 43 | test | 敵が三体見えたから逃げたよ。 | `not_target` | `senryu_line_lower` |
| 44 | dev | 今日の冒険は終了しましょうか | `not_target` | `senryu_end` |
| 44 | test | 中核をさららサバンナに変えてください | `senryu_change_middle` | `senryu_change_upper` |

原文は音声認識の表記も含めて保持。残る確認点は、保存・終了の言い回しと川柳以外の終了話題、曖昧な行指定。今回の結果を理由にラベル変更・追加学習はしていない。

## 受領時の照合

- ZIP内の97ファイルを同梱SHA-256と照合し、元の入力ZIPに含まれる31ファイルがバイト単位で一致することを確認。
- 全3シード・全分割の1,272予測について、本文・正解・予測・確率の対応、正解数、分類別指標、混同行列を再集計し、保存結果との一致を確認。
- 3シードの平均・標本標準偏差を再計算。10 epochのdev履歴から選択条件を再適用し、保存されたepoch 8 / 4 / 7と一致。
- 3モデルの重みhash、8分類のラベル対応、分類headの形状、float32形式、共通tokenizerを確認。
- 同梱コードは実行せず、ローカルでのモデル推論・追加学習・実装テストは行っていない。本体モデル、画面表示による分類器切替、稼働中サービスは変更していない。

[照合記録](results-review.json)に全数値と重みhashを保存。元ZIPはDownloadsに残し、この実験フォルダにも同名でコピーした。展開先は `artifacts/colab-results/`。ZIP・展開物は既存のignore対象で、Gitには確認記録だけを保存する。

- ZIP SHA-256: `8eb982530fc9f3dcc9dcbd8be7c77f666b41ee221434001cf050f6184bced9a7`
- [Colabの結果概要](artifacts/colab-results/reports/seed-summary.md)
- 誤分類：[seed42](artifacts/colab-results/reports/seed-42/misclassifications.md)・[seed43](artifacts/colab-results/reports/seed-43/misclassifications.md)・[seed44](artifacts/colab-results/reports/seed-44/misclassifications.md)
