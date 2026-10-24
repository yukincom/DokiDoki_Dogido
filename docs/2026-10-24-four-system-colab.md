# 2026-10-24 通常対話4系統 × 7seed：Colabの学習・合成・比較

## 状態

Colabノートブックと固定入力を作成後、2026-10-24にユーザーから主結果ZIPを受領・照合した。Tesla T4で28本の学習と44本の合成、元モデルを含む73候補の比較が完了。受領確認時点では本体未採用。その後、devで固定した通常16分類のseed48-mobを[既存のBERT切替経路](2026-10-24-bert-mode-routing.md)へ採用した。

[結果と誤分類](../dev_tools/request_classifier/experiments/2026-10-24-four-system-colab/results-review.md)：dev選択の単体はtest 45/50（元41/50）。4専門の単純平均は7seedで46〜47/50、係数1の差分加算は18〜28/50。全10,280予測を再集計して保存指標との一致を確認。主ZIPとseed42〜48の全7 ZIPを照合・保管済み。全28本の単体学習重みが揃っている。

成果物は `dev_tools/request_classifier/experiments/2026-10-24-four-system-colab/` に保存する。

| ファイル | 用途 |
|---|---|
| `ModernBERT_Ja_30M_4系統x7seed_学習と合成比較_2026-10-24.ipynb` | Colabへアップロードするノートブック |
| `modernbert-four-system-base.zip` | 実行中にアップロードする共通初期モデル。大容量のローカル生成物 |
| `modernbert-four-system-inputs.zip` | ノートブックに内蔵した教材とコードの保存用コピー |
| `training-protocol.json` | 実行前に固定した学習・合成・選択規則 |
| `source-provenance.json` | 教材、既存学習コード、TIES著者実装の出典とハッシュ |
| `preparation.json` | 配布物のハッシュ、件数、準備範囲 |

## 教材と共通初期化

教材はcommit `3228997ab113ca676f4c0a498716b0ecf43e6fe0` の `releases/2026-10-24-normal-dialogue-four-system/` と、対応する `curation/2026-10-24-four-system-contrast/` を固定した。共有train 818件は前版482件＋追加336件。dev 46件とtest 50件、16分類のIDを変更しない。

| 系統 | 担当 | 各epochの入力回数 | 固有のtrain件数 |
|---|---|---:|---:|
| spatial | 視線11分類＋ドギド呼出し | 1,184 | 818 |
| mount | 乗用動物の呼出し | 945 | 818 |
| sword | 剣への持替え | 937 | 818 |
| mob | モブ情報 | 942 | 818 |

各系統とも全818件を1回ずつ含み、担当分類と対応する対象外の対比例をもう1回提示する。専門教材は意図的にIDを重複させた入力列なので、固有IDを要求する `--material-release` へ直接渡さず、既存の `EncodedRows` に渡す。

全28本の初期値は、前回の通常対話578件Colabにあるseed43（trial-2/best）。`model.safetensors` のSHA-256は `0e8142045166ffea465ffaef6c5742fbd93949cec71641b5bfdb288aa49b813d`。分類headを含む全66テンソルを共通にし、各学習開始時に同一性を照合する。7seedは学習順などの乱数反復であり、7種類の基盤初期化ではない。初期モデルは元の482件を既に学習している。

## 学習条件

- 4系統 × seed42〜48の28本。分割・教材の所属は固定。
- 全パラメータを追加学習。毎回optimizerを新規作成。
- 学習率1e-5、10 epoch、batch16、AdamW、weight decay 0.01、linear schedule、warmup 0、gradient clipping 1.0。
- 学習率は前回の専門モデル試行に合わせる。追加336件を含むため10 epochまで学習し、devでepochを選ぶ。
- 履歴0、最大128 token、切り詰めなし。float32、SDPA、TF32無効、deterministic algorithms有効。
- Python 3.11.14。依存関係は前回Colabに保存された固定版を再利用。CUDA必須。
- epoch選択はdev macro F1降順、dev loss昇順、早いepoch順。

既存 `train.py` の `EncodedRows`、`MeasuredTrainer`、`SaveBestDevelopment`、`compute_metrics`、`evaluate_checkpoint` をそのまま再利用する。過去の2行限定学習オプションは使わない。

seed、入力・重み・コードのハッシュ、ライブラリ版、GPU、CUDA、各epochの履歴を保存する。同じseedでもGPUやドライバ等が異なる場合のビット単位一致は保証しない。

## 合成と比較候補

各seedの4専門を同じseed同士で合成する。共通初期重みをB、専門重みをEとするとTask arithmeticは `B + scale × Σ(E − B)`。係数は0.25、0.5、1.0に固定。0.25は浮動小数点の丸めを除けば4専門の等重み平均に相当する。[Task arithmetic論文](https://arxiv.org/abs/2212.04089)

TIESは4専門の差分を全66テンソルにわたって結合し、各モデルの絶対値上位20%を残し、massで符号を決め、dis-meanで合成する。係数は0.5、1.0。前回保存済みの[著者実装](https://github.com/prateeky2806/ties-merging/tree/44e7891fc84f3de7e4caa52664cd864ca3715e91)から4関数を抽出して使い、元ファイルとLICENSEも同梱する。[TIES論文](https://arxiv.org/abs/2306.01708)

| 候補 | 本数 |
|---|---:|
| 共通初期モデル | 1 |
| 学習単体：4系統 × 7seed | 28 |
| 4専門の合成：5方式 × 7seed | 35 |
| 同じ系統・方式の7seed重み平均：4系統＋5方式 | 9 |
| 合計 | **73** |

**7seedの点数の平均±標本SD**と、**7seedの重みを平均した1モデルの点数**を別に保存・表示する。元モデルは1本なので反復のSDを付けない。

## 選択と診断

1. 全73候補のdevを評価。
2. dev macro F1、dev loss、実行前の候補順で総合候補・学習単体・4専門合成を選択。
3. 5合成方式について、7seedの平均dev macro F1、平均dev loss、方式順で方式を選択。その方式の7seed重み平均モデルは別途実測する。
4. 選択・全モデルのハッシュを `selection.json` へ固定してから、全73候補のtestを評価。途中失敗後も同じ選択を照合する。
5. 元モデルとdevで選んだ主要候補を共有train 818件でも確認し、追加336件の内訳を表示。

dev/testの全予測は73 × (46＋50)＝7,008行。正解数、macro F1、loss、分類別precision/recall/F1、混同行列、誤分類、元モデルから直った誤りと増えた誤り、系統別の誤流入を保存する。

dev/testは教材設計で参照済みで、独立した未知評価ではない。trainの結果は学習内確認。testを見てseedや係数を選び直す処理は含めない。

## 中断、再実行、保存

初回はセットアップ後に空き20 GiBを要求する。途中再実行で同じ20 GiBを新たに要求しない。完了済みのモデル・評価は入力と保存結果のハッシュを照合して再利用する。中断した学習は記録を `run/interrupted/` に退避して、その1本を共通初期モデルから再実行する。optimizer途中状態からの続行ではない。

学習セルで失敗しても、保存セルを実行できる。seed内で4本揃っていなくても、完了済みの1〜3本をseed ZIPへ収録する。未完了の学習中の重みはダウンロード用ZIPの対象外。GPU切断などで後続セルが動かなかった場合は、同じランタイムへ戻れた時点で保存セルを実行する。

- 主結果ZIP：全成績・予測・誤分類・入力・出典・実行環境・主要選択モデルの重み。
- seed別7 ZIP：全28本の学習済み重み、学習履歴、設定、ハッシュ。
- 元の初期モデルZIPも保持する。選択されなかった合成重みは、これらと固定レシピから再構成可能。
- 比較レポートが揃うまで完了扱いにしない。未完了時の主ZIPには `partial` を付ける。
- ZIP内の全収録ファイルにSHA-256一覧を付け、ZIP自体のハッシュも保存する。

同じランタイム内の再実行に対応。別ランタイムへのZIP復元は自動化していない。Colabのランタイムを解放する前に、主ZIPとseed別7 ZIPをMacへ保存する。

## この作成作業で確認した範囲

配布物の作成時点では、固定commitと同梱教材の一致、前回の学習コード・共通初期モデル・TIES実装のハッシュ、候補数、ノートブック内Pythonセルの構文を確認した。`preparation.json` はこの準備段階の記録として変更せず保持する。

その後の受領時には、ZIP内910ファイルのハッシュ、準備入力43ファイル、全10,280予測、28本のepoch選択、dev選択固定とtest開始の順序、平均と標本SD、返却された3本の重みを照合した。[受領時の照合記録](../dev_tools/request_classifier/experiments/2026-10-24-four-system-colab/results-review.json)に保存。ローカルでのモデル推論・学習・合成再生成は行っていない。

さらにseed別7 ZIPを主結果に保存されたZIP hashと照合し、内部294ファイル・28モデルの重みを確認した。大容量成果物は既存の `artifacts/` に保管し、照合記録を追記した。
