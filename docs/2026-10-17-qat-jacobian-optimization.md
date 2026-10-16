# 同じGemma QAT重みのJacobian計算をまとめる改善

2026-10-17。状態は**Macでの小演算検査と二入力の実測が完了、CUDA再測定とレンズ本学習は未実施**。
前提とColab A100の従来方式の結果は[内部表現の実測記録](2026-10-16-haiku-internal-representations.md)を参照。

## 変更した計算

`dev_tools/haiku_internal_probe/qat_vectorized.py` は既存 `JacobianTape` を共有し、16個の微分方向をまとめて計算する。モデルに渡す文章のbatchは1のまま。量子化重みを展開・変更せず、通常forwardは元のMLX演算へ渡す。

- dense量子化行列の入力VJPは、方向軸を先頭へ移して同じ `quantized_matmul(transpose=False)` を呼ぶ。
- MoEは整列済みexpert行を保ち、方向軸を各expert行列の行へ移して同じ `gather_qmm(transpose=False)` を呼ぶ。離散expert indexだけを微分対象外とし、選ばれたexpertの混合重みは微分する。
- RMSNormの既定vmapは別の微分実装へ進むため、方向ごとに元の入力を共有してnative VJPを呼ぶ。

MLX 0.32.0では量子化行列のvmapが未実装で、custom vmapより先に本体も走査される。専用の内部placeholderを全て実演算へ置換したことを検査し、漏れた場合は例外またはNaN検査で停止する。適用範囲は固定重み・eval・batch=1・1〜16方向・整列済みMoEに限定する。クラスの差し替えは独立プロセスの測定中だけで、例外時も復元する。モデル学習や常駐サーバーには使用しない。

参照: [MLX custom function](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.custom_function.html)、[0.32.0の量子化演算と微分](https://github.com/ml-explore/mlx/blob/v0.32.0/mlx/primitives.cpp)、[vmapの走査](https://github.com/ml-explore/mlx/blob/v0.32.0/mlx/transforms.cpp)。

## 固定条件と実測

モデルは `lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit`、revision `aa5e2e50376f54254d503276ca2222ea8569b95e`。重み・tokenizer等の全対象ファイルと、既存比較元の7実装ファイルをSHA-256で照合した。MLX 0.32.0、mlx-lm 0.31.3。MacのMetal上で、メモリ上限24GiB、cache上限512MiB。常駐8080のPID 9601は測定後も同じだった。

最初の試作で使った先頭32座標から離し、0〜2815を均等に覆う32座標へ変えて確認した。各入力128token、29source層を同時に測定し、最終層の同じ位置集合を総和、source位置集合で平均する既存推定量を維持する。既存発句要求のprefixと、保存済みWikiText testのsource row 256を使用。指示文の追加・変更はない。

各方式は一度warmupし、従来→改善→改善→従来の順に測定した。計時は計算グラフ構築からGPU評価完了までで、数値比較と保存は除く。warmupとメモリ量は方式ごとに別記録。

| 128tokenの入力 | 従来方式・2回（32座標） | 16方向まとめ・2回 | 読み方 |
|---|---:|---:|---|
| 既存発句要求のprefix | 8.887 / 8.886秒 | 4.120 / 4.075秒 | 平均時間で2.17倍 |
| 独立したWikiText prefix | 20.024 / 10.904秒 | 4.473 / 4.460秒 | 従来側の時間変動が大きい。3.46倍という平均比を安定した速度とは扱わない |

改善版の最大MLXメモリは約16.88GiB、従来方式は約14.49GiB。これは測定プロセス内のMLX値で、Mac全体の使用量ではない。

両入力とも、全30層のforward残差・expert集合・混合重みの差は0。全ての実VJPが返した最終primalもnativeとの差0で、微分値は有限だった。微分そのものはbitwise一致ではない。

| 入力 | 従来方式の繰返し・全体相対L2差 | 改善版と従来方式の差（2組） | 各層で最大の相対L2差・方式間 |
|---|---:|---:|---:|
| 既存発句要求 | 0.3131% | 0.3087% / 0.3159% | 2.37% / 2.42% |
| WikiText | 1.5416% | 1.5253% / 1.5204% | 3.35% / 3.34% |

今回の方式間の差は同じ方式を繰り返した差と同程度だった。後から許容幅を作って精度合格にはしていない。二入力・32座標の結果であり、全2816座標、長文、日本語の多様な場面、完成レンズの精度やCUDAの速度を保証しない。既存Mac/CUDA間の大きな勾配差も未解決。

試作ではコンパイルだけの改善は数%だった。expert重みを全て展開してcacheする案は、三射影×30層で追加約42.5GiBになるため採用していない。今回の改善は解析の事前計算用で、通常生成のtok/sを測ったものではない。

## 再現と配布物

実測正本は `.dogido_tools/experiments/2026-10-17-qat-jspace/optimization-colab/qat-colab/baseline/`。`report.json` に実行4ソース・spec・ケースのhash、実token列、時間、層ごとの差を保存し、各方式の生の微分配列とforward配列も保持する。初期の探索試行は同実験の `optimization/` に分けて残した。

`optimization_probe.py` は失敗時も途中記録を残し、既存出力フォルダへの上書きを拒否する。小演算5検査では、bias付きdense、複数expert、RMSNorm、placeholderの漏れ、例外時復元を確認済み。加えて上記の実モデル比較、読み取りレビュー、notebookの5コードセルと内部2スクリプトの構文、ZIPのCRCと18ファイルのhashを検査した。

```sh
python dev_tools/haiku_internal_probe/build_colab_bundle.py prepare-optimization \
  --destination "$OPTIMIZATION_DIR" --baseline-bundle "$EXISTING_COLAB_DIR/qat-colab"
"$MLX_PYTHON" dev_tools/haiku_internal_probe/optimization_probe.py \
  --bundle "$OPTIMIZATION_DIR/qat-colab" --model "$MODEL_SNAPSHOT" \
  --output "$OPTIMIZATION_DIR/qat-colab/baseline"
python dev_tools/haiku_internal_probe/build_colab_bundle.py package-optimization \
  --destination "$OPTIMIZATION_DIR"
```

配布物は同実験の `optimization-colab/qat_colab_optimization.ipynb` と `qat_colab_optimization_support.zip`。既存のColab準備処理を再利用し、従来の比較notebookとは別名で保存する。入力・比較元・コードを含み、モデル重みや認証情報は含まない。新notebookのCUDA実行は未実施で、Colabを再開していない。

## GGUF系へ移る場合

J-spaceの手法と、採取済みベクトルに対する解析はファイル形式へ固定しない。今後は次の境界を維持する。

1. レンズ作成: 対象重みでの微分計算と校正。今回のMLX用高速化はこの部分。
2. 実行時の採取: 各backendから、同じ層境界・token位置の残差を取り出す。GGUFを読む実装側のhookが必要。
3. 共通解析: 保存済み活性・token・モデル識別とレンズから、語彙投影、差分、疎な分解を行う。

日常の生成ごとに全Jacobianを再計算する設計にはしない。同じモデル名でも量子化、重み、tokenizer、層境界、計算backendが変われば、レンズ転用を別の検証として扱う。現時点でGGUF採取器やStrata接続を実装したわけではない。

## Google公式テンプレートとの差

ユーザーからの指摘後に、今回固定したsnapshotの `chat_template.jinja` と[Google公式QATテンプレート](https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-unquantized/blob/main/chat_template.jinja)を読んで差を確認した。公式はツール呼出しの反復・turn終端・思考部分の順序を修正した旨を記載し、[履歴](https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-unquantized/commits/main)では2026-07-15にcanonical templateへ更新している。ローカルには公式の次の非toolメッセージを使う終端処理、`preserve_thinking`、引数型検査等との差がある。

ここで確認した修正対象はテンプレート。モデル重みの更新が必要だとは、この差だけから結論しない。今回の実験は保存済みtoken列での同一条件の比較なので記録として有効だが、修正後の実運用入力を測ったことにはしない。テンプレートを変える場合も実token列とbindingを更新し、生成・採取を新しいrunで再検証する。同じ重み・同じtoken列での校正資産を無条件に廃棄する必要はない。本作業ではテンプレートを変更していない。
