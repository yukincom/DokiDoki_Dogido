# ModernBERT-Ja-30M：v1.7再評価と3 seed比較（2026-10-14）

**結論:** testの平均差は+0.44ポイントで、3 seed間の標準偏差2.04ポイントより小さい。回帰は平均+1.67ポイントだが、通常範囲の成績や誤操作扱いは安定して改善していない。今回をv1.7の基準値とし、安定した精度向上とは結論しない。

## 比較の範囲

- 既存モデル：v1.6の572件で学習済みのseed 42・学習率2e-5・epoch 9の重みを、そのままv1.7入力で再評価。
- 再学習：v1.7のtrain 566件から、同じ事前学習モデルを使ってseed 42・43・44をそれぞれ独立に学習。
- 共通評価：dev 73件・test 75件・regression 40件。全モデルが本文・履歴・正解・注釈まで同一のv1.7入力を使用。
- 各seedで前回と同じ2学習率を試す。epochは各試行のdevで選び、共通学習率は3 seedの平均dev macro-F1で決定。その後に旧モデルと全3 seedのtest/regressionを予測。
- 旧モデルの過去の予測は流用していない。旧v1.6のtest精度とも直接比較しない。本体への接続は行っていない。

## 正解率とばらつき

±は3 seed間の**標本標準偏差**（分母n−1）。信頼区間ではない。devは設定選択用、trainは学習例への適合確認。

| 評価 | 既存モデルをv1.7で再評価 | 再学習3 seed平均 ± 標準偏差 | 範囲 | 平均の差 |
|---|---:|---:|---:|---:|
| test | 65/75（86.7%） | 87.1% ± 2.04ポイント | 85.3%〜89.3% | +0.44ポイント |
| regression | 31/40（77.5%） | 79.2% ± 1.44ポイント | 77.5%〜80.0% | +1.67ポイント |
| dev | 62/73（84.9%） | 84.0% ± 0.79ポイント | 83.6%〜84.9% | -0.91ポイント |

| 評価 | 既存macro-F1 | 再学習macro-F1平均 ± 標準偏差 |
|---|---:|---:|
| test | 0.8722 | 0.8736 ± 0.0175 |
| regression | 0.7736 | 0.7874 ± 0.0266 |
| dev | 0.8502 | 0.8401 ± 0.0100 |

### 各seedの結果

| モデル | test | regression | dev | train |
|---|---:|---:|---:|---:|
| baseline | 65/75（86.7%） | 31/40（77.5%） | 62/73（84.9%） | 565/566（99.8%） |
| seed-42 | 64/75（85.3%） | 32/40（80.0%） | 61/73（83.6%） | 566/566（100.0%） |
| seed-43 | 67/75（89.3%） | 31/40（77.5%） | 62/73（84.9%） | 566/566（100.0%） |
| seed-44 | 65/75（86.7%） | 32/40（80.0%） | 61/73（83.6%） | 562/566（99.3%） |

### 操作の誤誘導と取りこぼし

誤操作扱いは正解が剣・呼び戻し以外なのにどちらかへ分類した件数。取りこぼしは正解が剣・呼び戻しなのに両方以外へ分類した件数。実際のゲーム操作を実行した率ではない。

| 分割・指標 | 既存 | seed 42 | seed 43 | seed 44 |
|---|---:|---:|---:|---:|
| test・誤操作扱い | 1/45 | 0/45 | 1/45 | 4/45 |
| test・操作依頼の取りこぼし | 5/30 | 5/30 | 4/30 | 3/30 |
| regression・誤操作扱い | 2/30 | 3/30 | 2/30 | 1/30 |
| regression・操作依頼の取りこぼし | 2/10 | 1/10 | 2/10 | 2/10 |

### 対応範囲と履歴付き例

| 評価・部分集合 | 件数 | 既存正解率 | 再学習平均正解率 |
|---|---:|---:|---:|
| test・ordinary_scope | 72 | 88.9% | 88.0% |
| test・unsupported_request | 3 | 33.3% | 66.7% |
| test・with_context | 27 | 85.2% | 82.7% |
| regression・ordinary_scope | 39 | 79.5% | 79.5% |
| regression・unsupported_request | 1 | 0.0% | 66.7% |
| regression・with_context | 4 | 100.0% | 75.0% |
| dev・ordinary_scope | 70 | 85.7% | 86.2% |
| dev・unsupported_request | 3 | 66.7% | 33.3% |
| dev・with_context | 25 | 80.0% | 84.0% |

`unsupported_request`は自然な未対応依頼、`ordinary_scope`はそれ以外。未対応依頼はtestに3件・回帰に1件しかなく、この部分の結果を広く一般化しない。履歴付き例は各範囲と重複する。

## 誤分類の変化

| 評価 | seed | 直った | 新しく誤った | 誤りが残った |
|---|---|---:|---:|---:|
| test | seed-42 | 3 | 4 | 7 |
| test | seed-43 | 5 | 3 | 5 |
| test | seed-44 | 6 | 6 | 4 |
| regression | seed-42 | 2 | 1 | 7 |
| regression | seed-43 | 3 | 3 | 6 |
| regression | seed-44 | 4 | 3 | 5 |

### 3回共通の変化と、次に補強する境界

- **3回とも直った:** `test-029`「頼むわ、手元のやつをつるぎに替えといてや。」はその他→剣、`regression-009`「イルカは今どこにいる？」は敵情報→その他。
- **3回とも誤りが残った:** `regression-010`「いま見えるイルカは何匹？」を敵情報、`regression-017`「独り言だけど、どこにいるかなあ。」をその他依頼、`regression-025`「『馬を呼んで』と私から今お願いしているんだよ。」を非依頼、`regression-028`「敵の場所を教えてから、剣に替えて。」を剣と分類した。最後は複合依頼なので正解はその他。
- **表記と文脈で揺れる:** `regression-033`「うまを　ここへ　よんでー！」はseed 42だけ正解。剣の色を気に入ったか聞かれた後の`test-051`「ああ、それでいいよ。」はseed 43だけ正解。
- **新しい誤り:** `test-045`の剣への持ち替え禁止や`test-054`の剣の色への相槌がseed 44で剣依頼になった。元の実機発話`regression-001`「さあて！今度はどこにいるかなーー？？どこかなー？？」もseed 44では敵情報へ戻った。全3 seedに共通する新しい誤りはtest・回帰にはない。
- **履歴による区別が未解決:** 下記の同じ最新発話を持つ対照は、既存モデルも新3モデルも2件を同時に正解できなかった。

| ID・直前の履歴 | 最新発話 | 正解 | 既存 | seed 42 | seed 43 | seed 44 |
|---|---|---|---|---|---|---|
| `test-070`「ここへ呼ぶ馬はどれにする？」 | 最後に乗ったやつにして。 | 呼び戻し | その他 | その他 | 呼び戻し | その他 |
| `test-075`「冒険の記録に書く紹介文やな。どの馬について文章を考えよか？」 | 最後に乗ったやつにして。 | その他 | その他 | その他 | 呼び戻し | その他 |

ここからの補強対象は、**引用を実際の依頼として採用する文、対象が敵かどうか、履歴によって同じ返事の意味が変わる対照**。今回は追加学習例の作成や、testを見た設定変更までは行っていない。

## 学習条件と選択

学習開始前にseed・データハッシュ・選択規則を`protocol.json`へ保存し、設定確定時に`selection.json`へ記録した。test/regressionの結果を見た再調整は行っていない。

| seed | 採用学習率 | 採用epoch | dev正解率 | dev macro-F1 |
|---|---:|---:|---:|---:|
| 42 | 5e-05 | 7 | 83.6% | 0.8389 |
| 43 | 5e-05 | 8 | 84.9% | 0.8507 |
| 44 | 5e-05 | 6 | 83.6% | 0.8308 |

| 学習率 | 3 seedの最良dev macro-F1平均 | 最良dev loss平均 |
|---|---:|---:|
| 2e-05 | 0.8119 | 2.2078 |
| 5e-05 | 0.8401 | 1.7249 |

- 共通学習率の同点時は平均dev loss、平均epoch、事前の学習率順で決める。全3 seedを評価し、testの最高seedは選ばない。
- 基盤モデル：`sbintuitions/modernbert-ja-30m`、revision `8cb03f54cb9e30e72459e5f1cedc6d89c7d8dcb5`。全36,772,613パラメーターを更新。
- 各試行10 epoch、batch 16、最大128 token、AdamW・weight decay 0.01、linear scheduler・warmup 0。各epoch 36 step、計360 step。v1.6も36 step/epochだった。
- MPS（Apple GPU）、float32、SDPA、CPU thread 4。全例が128 token以内で切り詰めなし。ライブラリの版は前回と同一。
- 6試行の学習合計244.3秒、評価を含む全処理250.4秒。推論専用の遅延比較は今回の測定範囲外。
- 重みは`.dogido_tools/request_classifier/2026-10-14-30m-v1.7/`以下に保存。旧モデル・旧レポート・データは変更せず保持。

## 結果の読み方と限界

- 既存モデルは1 seed、新版は3 seedなので、平均差をデータ整理だけの因果的な効果とは断定できない。設定選択も旧版の1 seed devから新版の3 seed平均devに変わっている。
- 合成例中心の小規模な固定評価であり、既存testは過去の誤分類確認にも使っている。今回の3 seedは学習の乱数による揺れを示すが、未使用の実会話での精度や評価例の標本誤差までは測らない。
- trainの高得点は学習データへの適合であり、未知の言い方への性能とは別。softmax値は未校正で、正解確率として扱わない。

## 記録と再実行

- [実験前の条件](../dev_tools/request_classifier/reports/2026-10-14-modernbert-ja-30m-v1.7/protocol.json)・[実行記録](../dev_tools/request_classifier/reports/2026-10-14-modernbert-ja-30m-v1.7/manifest.json)・[dev選択の全候補](../dev_tools/request_classifier/reports/2026-10-14-modernbert-ja-30m-v1.7/selection.json)
- [履歴付きの全誤分類比較](../dev_tools/request_classifier/reports/2026-10-14-modernbert-ja-30m-v1.7/comparison.md)・[平均・ばらつき・IDごとの比較JSON](../dev_tools/request_classifier/reports/2026-10-14-modernbert-ja-30m-v1.7/comparison.json)
- 各モデルの`baseline/`、`seed-42/`、`seed-43/`、`seed-44/`に全予測・混同行列・分類別指標・誤分類を保存。
- 検証：関連21テスト、データ報告の一致、全3,016予測行の入力と正解、scikit-learnによる全16組の指標再計算、平均・標準偏差・変更件数、6試行のdev選択と重み再読込を確認。

実行前に[学習環境](../dev_tools/request_classifier/README.md#学習評価の再実行)を用意する。次の出力先は未使用の名前へ変える。旧モデルとの比較にはv1.6の学習済み重みが必要。

```bash
HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
.dogido_tools/request_classifier/.venv/bin/python dev_tools/request_classifier/train.py \
  --output-dir .dogido_tools/request_classifier/new-v1.7-run \
  --report-dir dev_tools/request_classifier/reports/new-v1.7-run \
  --revision 8cb03f54cb9e30e72459e5f1cedc6d89c7d8dcb5 \
  --seeds 42 43 44 --learning-rates 2e-5 5e-5 \
  --epochs 10 --batch-size 16 --max-length 128 --device mps \
  --baseline-checkpoint .dogido_tools/request_classifier/2026-10-14-30m-v1.6/trial-1/best
```

`HF_HUB_OFFLINE=1`は基盤モデルが同じcacheに取得済みの場合に使う。データはv1.7を固定し、結果は新しいディレクトリへ保存する。seedを指定しても、別のOS・GPU・ライブラリ環境で重みがビット単位まで一致する保証はしない。
