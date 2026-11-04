# 具体変更の語句抽出：独立学習試作

現在の句と本人の発話を入力し、発話中の `from`（変更元）と `to`（変更先）の開始・終了位置を返す。行呼称は既存BERTの担当で、このモデルの抽出対象にしない。句の生成、漢字・読みの補正、編集の実行は行わない。本体へは未接続。

モデルは [tohoku-nlp/bert-base-japanese-char-v3](https://huggingface.co/tohoku-nlp/bert-base-japanese-char-v3)（Apache-2.0、固定revision `ccddee593a9e03a589ae1feedcd4d1eb06d6ba02`）を追加学習した約9,085万パラメータのBERT。出力は `O / B-from / I-from / B-to / I-to`。

## 2026-11-04の追加学習：ひらがな・認識崩れと5 seed

今回の結論: 既知3件のひらがなASR入力は前回1/3から全5 seedで3/3へ改善。一方、旧実対話37件は前回32件正解から29〜31件へ低下。全体の精度向上は確認できない。devで選んだseed45の新実対話は18/25（具体語あり14/20、なし4/5）。漢字混じり語句の末尾・先頭欠落、説明中の語を余計に拾う失敗が残った。既存意図BERTの前段分類だけで、これらの語句欠落が解決するとは言えない。

通常表記・全ひらがな・認識崩れの合成を各2,000件、実発話11件を4回ずつ使い、計6,044行で学習した。dev/testは各960件（各表記320件）。5 seedとも同じ公開baseから4 epoch、batch 48、学習率3e-5で学習し、モデル構造と出力復元は初回から変えていない。

新たに9つの句から25発話（具体語あり20、なし5）をモデル予測前に注釈し、その句と全revisionを学習・devから除外した。旧評価と合わせて39群を予約し、残りはtrain 46群、dev 6群、test 6群。実際の素材行は85/9/18行で、合成件数ほど独立した話題があるわけではない。新実対話の注釈は本人の全件確認前。

各seedのepochと最終seedはdevの完全一致→span F1→小さいseedの順で固定し、その記録を保存してから最終評価を実施した。

| seed | dev / 960 | 合成test / 960 | 新実対話 / 25 | 旧実対話 / 37 | 既知3件のひらがなASR / 3 |
|---:|---:|---:|---:|---:|---:|
| 42 | 796 | 786 | 18 | 30 | 3 |
| 43 | 781 | 774 | 17 | 29 | 3 |
| 44 | 835 | 818 | 16 | 30 | 3 |
| 45（選択） | 850 | 832 | 18 | 30 | 3 |
| 46 | 847 | 825 | 15 | 31 | 3 |

選択モデルは **seed 45**。表記別testは、通常 316/320（98.8%）、ひらがな 264/320（82.5%）、認識崩れ 252/320（78.8%）。新実対話は 18/25（72.0%）。合成testの目安95%には未達。

旧実対話37件と既知3件は改善方針を決める際に見た回帰集であり、新しい未使用評価とは呼ばない。初回と今回では合成test・素材分割・batchが異なるため、ひらがな教材追加だけの因果効果は切り分けていない。5 seedの比較内では条件をそろえた。

認識崩れは長音、行名の先頭脱落、助詞の誤変換、文字の脱落・重複を合成したもの。崩れた文字列をそのまま正解範囲とし、原文にない語へ補正しない。既存の意図BERTで具体変更と分類された依頼を抽出器へ渡す構成を想定するが、その接続・分類BERTとの連動試験は今回行っていない。

5つの重み、教材、全予測、選択記録、失敗例はGit対象外の `.dogido_tools/edit_span_extractor/2026-11-04-v2/`。詳細は同フォルダの `REPORT.txt`、重みのパスとhashは `MODEL_FILES.json`。本体へは未接続。

## 2026-11-04の初回結果

学習用3,656行＝合成3,600件＋目視注釈した実発話14件を4回配置。調整用480件だけで選び、3エポック目を採用した。4エポックの学習と評価はMPSで約156秒。

| 評価 | 原文の全範囲が完全一致 | 用途 |
|---|---:|---|
| 調整用の合成依頼 | 468/480（97.5%） | モデル選択に使用 |
| 未使用の句・文型で作った合成依頼 | 442/480（92.1%） | 初回の未使用評価。目安95%には未達 |
| 保存済み実対話 | 32/37（86.5%） | 具体変更23/27、具体語なし9/10 |
| 既知の本人発話3件の原文 | 2/3 | 診断用回帰 |
| 以前の合成音声のWhisper出力 | 7/10 | 同じ3発話の異なる認識結果 |
| 以前の合成音声のひらがな認識出力 | 1/3 | 同上。本人録音ではない |
| 誤認識前の正しいひらがな依頼文 | 2/3 | テキストのみ |

MPSで常駐済みのモデルを1件ずつ実行し、文字入力変換・推論・範囲復元込みで中央値6.34ms（57回）。音声認識、モデル読込、LLM、編集実行の時間は含まない。CPU速度と本体連携は未確認。

実対話では旧語の一部欠落、変更先末尾の欠落、自由編集での余計な抽出が残った。全文ひらがなでは依頼の「へんこう」の一部まで拾う場合がある。中七の既知例では「ひろがるのはら」を取り出せ、末尾の依頼助詞「に」は含めなかった。初回評価を見てからの再学習や出力補正はしていない。

## 教材と評価の範囲

- SSD正本の136句・53 revision・2,261記録行から素材を読んだ。記録行数は独立した発話数ではない。
- 同じ句の版と同一全文の別句をまとめ、97群に分けた。実評価等の30群を先に予約し、残りはtrain 53群、dev 7群、test 7群に割り当てた。
- 分割間で同じ素材行を使わず、評価用の指定語も学習から除外した。実際に合成に使える独立した素材行はtrain 121、dev 21、test 13。合成件数を独立した実例数と解釈しない。部分語の短い重なりはある。
- 実例の正解は原文を目視して付けたもので、本人が全注釈を確認したものではない。保存されたLLMの編集結果を正解にはしていない。解釈が未確定の8例は採点せず保留した。
- `from` がない依頼、`to` がない相談、具体語のない感想等も含む。抽出の有無だけで操作・保存の許可を判定するものではない。
- これは原文範囲抽出の評価であり、正しい変更後の一行の生成、ASR修復、Minecraft、マイク・音声配送、保存処理の試験ではない。

## 保存先と実行

個人の原文・注釈・合成教材・重み・全予測は、Git対象外の `.dogido_tools/edit_span_extractor/2026-11-04/` に置く。`REPORT.txt` に失敗例と条件を記録した。学習済みモデルは同フォルダの `run-v1/model/`。

文字ごとの原文位置を保つ独自の入力変換を使用する。文脈は分類損失から除外し、発話だけを分類する。開始位置は含み、終了位置は含まないUnicode codepoint offset。既定のMeCab tokenizerや一般的なHugging Face pipelineではなく、次の入口を使う。

```bash
.dogido_tools/request_classifier/.venv/bin/python dev_tools/edit_span_extractor/predict.py \
  --model .dogido_tools/edit_span_extractor/2026-11-04/run-v1/model \
  --current-verse '朝の空' --text '朝の空を夜の空に変えてください'
```

モデル常駐で使う場合は `span_model.predict_rows` を呼ぶ。1文字ごとのID化はNFKCで1文字の変換だけ反映し、原文は保持する。既知vocabにない文字は `[UNK]`。512位置を超える入力は黙って切り捨てずエラーにする。未知のIラベル始まりはその位置から範囲を開始し、語句・助詞による事後トリミングはしない。

## 再学習

既存の学習環境（検証時torch 2.14.1、transformers 5.19.0）を利用した。私的データの送信やクラウド学習は行わない。基礎モデルの再取得にはネットワークが必要。

```bash
python3 dev_tools/edit_span_extractor/download_base.py \
  --output .dogido_tools/edit_span_extractor/base-model

python3 dev_tools/edit_span_extractor/build_dataset.py \
  --memory-root /path/to/dogido-memory \
  --audit /path/to/reviewed-evaluation.json \
  --manual-annotations /path/to/manual-training-annotations.json \
  --regression /path/to/regression-input.json \
  --output .dogido_tools/edit_span_extractor/new-data

.dogido_tools/request_classifier/.venv/bin/python dev_tools/edit_span_extractor/train.py \
  --base .dogido_tools/edit_span_extractor/base-model \
  --data .dogido_tools/edit_span_extractor/new-data \
  --output .dogido_tools/edit_span_extractor/new-run --device mps --epochs 4 --batch-size 24

python3 -m unittest discover -s tests -p 'test_edit_span_extractor.py' -v
```

`build_dataset.py` のaudit入力は `extraction / non_extraction / regression / ambiguous` の配列を持つJSON。各行は `source_id, entry_id, text, current_verse, spans` を持ち、spansは `role, start, end, text`。manual注釈は原文をキー、`[旧語またはnull, 新語またはnull]` を値とする。regression入力は同じ行形式の配列。句の出典と独立した注釈を先に確認する。

再現用に、初回の入力hash・学習時のソース・選択履歴を保存した。初回後に私的注釈をコードから分離し、素材群の走査順を明示したが、5分割の教材JSONLがバイト単位で一致することを確認した。既存の出力先は上書きしない。次の学習で今回の失敗例を教師へ移す場合、この評価集を未使用testとは呼ばず、別の実対話を予約する。

### 追加学習を再現する場合

教材生成へ `--augmentation-v2 --new-audit /path/to/new-independent-audit.json` を加える。new-auditは `extraction / non_extraction` の配列を持ち、各行の形式は旧auditと同じ。既存の評価例を教師へ移さず、追加評価の句・revisionも先に予約する。

同じデータとbaseを固定し、seed 42、43、44、45、46をそれぞれ `--seed N --epochs 4 --batch-size 48 --defer-evaluation` で学習する。出力先は新しい実験フォルダ配下の `runs/seed-42` 等に分ける。その後だけ、次を実行する。

```bash
.dogido_tools/request_classifier/.venv/bin/python dev_tools/edit_span_extractor/compare_seeds.py \
  --runs /path/to/new-experiment/runs --data /path/to/new-experiment/data \
  --output /path/to/new-experiment/comparison --device mps
```

`compare_seeds.py` は5 runのデータhashを照合し、devでの選択を `frozen-selection.json` に書き出してから最終評価に進む。既存の出力先は上書きしない。
