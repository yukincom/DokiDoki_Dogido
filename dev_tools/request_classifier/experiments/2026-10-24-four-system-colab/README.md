# 通常対話・4系統 × 7seed のColab

固定教材で28本を追加学習し、合成44本と元モデルを含む73候補を比較するノートブック。**2026-10-24にColab結果を受領・照合済み**です。

[結果と誤分類](results-review.md)：dev選択の単体はtest 45/50（元モデル41/50）。4専門の単純平均は7seedで46〜47/50。係数1の差分加算は18〜28/50へ悪化。既知データの診断で、本体採用は未実施です。主ZIPとseed別5/7 ZIPを受領済み。残りはseed45・48です。

## 使い方

1. `ModernBERT_Ja_30M_4系統x7seed_学習と合成比較_2026-10-24.ipynb` をColabへアップロード。
2. GPUを選び、「すべてのセルを実行」。アップロード欄で付属の `modernbert-four-system-base.zip` を選択。
3. 完了後、主結果ZIPとseed別の重みZIP 7個をMacへ保存。

教材と処理は内蔵済み。`modernbert-four-system-inputs.zip` の追加アップロードは不要です。初期モデルZIPは大容量のためローカル生成物として保持し、Gitへ含めません。

## 実験

- 4系統：視線＋ドギド呼出し／乗用動物／剣／モブ情報。各モデルは共通の16分類。
- 共有train 818、dev 46、test 50。全818件に系統別の再提示を加えて学習。
- seed42〜48。全28本で前回seed43の初期重みを共有。学習率1e-5、10 epoch、batch16、128 token。
- Task arithmetic 3係数、TIES 2係数を各seedで比較し、同方式の7seed重み平均も作成。
- devで選択を固定してからtestを診断。全予測・分類別F1・誤分類・平均と標本SDを保存。

dev/testは教材設計で参照済みの診断資料です。未知の実機精度や本体採用を示しません。

同じランタイム内の再実行では完了済みを照合して再利用し、未完了の学習1本を最初からやり直します。別ランタイムへのZIP復元は自動化していません。

設定・選択規則は `training-protocol.json`、入力出典は `source-provenance.json`、配布物のハッシュと準備範囲は `preparation.json`。再生成は `python3 build_notebook.py`（前回のローカル学習成果物が必要）。詳細は [作成記録](../../../../docs/2026-10-24-four-system-colab.md)。
