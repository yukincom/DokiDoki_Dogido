# M4 Max 64GBでの動作報告：JSON連続生成時のMTP位置不整合エラーと川柳生成結果

公開ありがとうございます。Mac Studio（M4 Max・64GB）で推奨設定を試し、Qwen3.8 Flash Next 125Bを使った日本語の川柳生成が動作しました。生成から検査まで約3秒で終わる回もあり、このサイズのモデルが手元で動くことには手応えがありました。

一方、今回の構成では川柳の品質が伴わず、MTP内部でsequence位置の不整合エラーも繰り返し記録されました。本来の実力を出し切れていない可能性もあると考え、動作結果の共有と、切り分け方の相談として報告します。HTTP応答自体は成功しており、エラーと品質の関係はまだ未確定です。

私は、Minecraftの冒険に寄り添うAI相棒「[ドキドキドギド](https://github.com/yukincom/DokiDoki_Dogido)」を開発しています。周囲の景色や持ち物から川柳を詠み、プレイヤーと推敲する機能があり、今回はその生成処理で試しました。計測中はMinecraftとドギドの常駐サーバーを終了し、保存済みの場面データを使って生成・検査部分だけを実行しています。

## 確認した現象

- ウォームアップ1回＋本計測9回を完走し、計53件のAPI要求はすべてHTTP 200でした。
- 同じ区間のサーバーログに、`decode: failed to initialize batch` が52件ありました。対応して、draft側の `llama_process[0] returned -1` が37件、`mtp_flush: deferred catch-up failed rc=-1` が15件記録されています。
- `inconsistent sequence positions` と `Drafts may degrade` の警告もあります。
- 生成されたJSONは解析できていますが、日本語として意味を取りづらい句もありました。**MTPのエラーと出力品質の因果関係は、まだ切り分けできていません。**

最初のウォームアップ中のログです。

```text
1.26.679.202 E init: the tokens of sequence 0 in the input batch have inconsistent sequence positions:
 - the last position stored in the memory module of the context (i.e. the KV cache) for sequence 0 is X = 1255
 - the tokens for sequence 0 in the input batch have a starting position of Y = 1242
 for M-RoPE, it is required that the position satisfies: X < Y
1.26.679.204 E decode: failed to initialize batch
1.26.679.204 E spec        draft: llama_process[0] returned -1
```

後続の要求では、次のログも出ています。

```text
1.38.225.619 E spec    mtp_flush: deferred catch-up failed rc=-1
1.38.225.651 W spec        begin: ctx_dft pos_max=1078 < N-1=1082 - process() hook may not have run on every prefill ubatch (need_embd / output flag on every prompt position?). Drafts may degrade.
```

## 環境と起動条件

| 項目 | 内容 |
|---|---|
| 実行日 | 2026-10-12 |
| マシン | Mac Studio / Apple M4 Max / CPU 16コア / GPU 40コア / メモリ64GB |
| OS | macOS 26.6.1 |
| このリポジトリ | `65e15eb2829e712893a764bdb7fc1f1e54cf68c0` |
| llama.cppベース | `781dbc5ac98921dbdb5e5b2ec5b7a50960e937d4` |
| パッチ | 上記リポジトリの49パッチを適用。適用後のローカルHEADは `f5a5381882971a26655b2aed19a55fed228813ff` |
| ビルド | Release / Metal / embedded library / CURL無効 |
| 本体モデル | `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` の `IQ3_XXS`、2分割 |
| 本体revision | `ed59f92082b1e93c0e96d60a8b11aab089b52f09` |
| MTPモデル | `ggml-org/Qwen3.8-Flash-Next-GGUF` の `mtp-Qwen3.8-Flash-Next-Q8_0.gguf` |
| MTP revision | `052beeaca7bec4a303e59cc7bc630c4f3a1b845d` |

全3ファイルのSHA-256を配布元の値と照合済みです。以下は実際の起動設定で、実行ファイルとモデルのパスだけ相対表記に置き換えています。

```bash
GGML_ALLOC_FREE_DELAY=4 \
LLAMA_SPEC_MTP_MERGE=1 \
LLAMA_GRAPH_CACHE=16 \
LLAMA_SPEC_STOP=1 \
./build/bin/llama-server \
  -m ./models/IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf \
  -md ./models/mtp-Qwen3.8-Flash-Next-Q8_0.gguf \
  --spec-type draft-mtp --spec-draft-n-max 7 --reasoning off \
  -ngl 99 -c 16384 -np 1 \
  --host 127.0.0.1 --port 8080 --alias default_model
```

## API要求の条件と確認用データ

`/v1/chat/completions` に逐次送信し、並列要求はしていません。

| 段階 | temperature | max_tokens |
|---|---:|---:|
| 川柳の初稿 | 0.6 | 300 |
| 検査結果を受けた行の再生成 | 0.3 | 300 |
| 材料との対応・日本語の検査 | 0.0 | 512 |

共通で `model=default_model`、`stream=false`、`chat_template_kwargs={"enable_thinking":false}`。toolsやresponse_formatは使わず、プロンプトでJSON本文を指定しています。生成と検査ではsystem/userメッセージの内容が変わります。top_pなどは要求に含めていません。

最初の2要求は、初稿生成（seed 7）→検査（seed 1007）です。この2要求を実行した区間ですでに上記エラーが出ています。実際の要求・応答を `01-draft.request.json` / `02-grounding.request.json` と対応するresponseファイルに切り出し、`observed-warmup.log` とともに添付用ZIPへまとめました。

サーバーを上記設定で起動し、添付ファイルのディレクトリで以下の順に送れます。

```bash
curl --fail-with-body -sS http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  --data-binary @01-draft.request.json -o replay-01.response.json
curl --fail-with-body -sS http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  --data-binary @02-grounding.request.json -o replay-02.response.json
```

これはエラーが出た実行の冒頭2要求をそのまま切り出したもので、独立した最小再現手順としての再実行はまだ行っていません。2件目の検査対象は当時生成された句で固定してあります。

## 未改変版との事前確認

同じ量子化モデルについて、MTPとHTTPサーバーを使わない条件で、次を確認しています。

- 固定した英文コード生成プロンプトからgreedyで200 tokens生成し、未改変版とパッチ適用版の出力がバイト一致。
- context / batch = 512 / 512、ubatch = 512 と64の両方で、未改変版・パッチ適用版のPPL表示値はすべて `8.4729`。

PPL入力は固定commitの本リポジトリREADME、1 chunkのみです。一般的な精度評価ではなく、小規模な計算差確認として実施しました。これらの確認では `GGML_*` / `LLAMA_*` 環境変数を解除しています。

今回の川柳生成はtemperature > 0を含むため、この事前確認とは条件が異なります。MTP無効時や未改変版で同じ川柳要求を実行する対照実験はまだ行っていません。

## 川柳生成の結果

草地・坑道・雪斜面の3場面を各3回（seed 101 / 202 / 303）、再生成は最大6回。アプリ側の音数判定は各行±1音を許容するため、「採用」と「厳密な5・7・5」を分けています。

通常使っているGemmaの以前の計測も参考として併記します。**モデル・量子化・実行基盤が異なるため、この比較をパッチによる品質低下の根拠とは考えていません。**

| 項目 | Gemma 4 26B-A4B QAT MLX 4bit | 今回の125B IQ3_XXS＋MTP |
|---|---:|---:|
| アプリ検査で採用 | 9/9 | 6/9 |
| 厳密な5・7・5の採用句 | 9/9 | 0/9 |
| 所要時間の中央値 | 4.033秒 | 10.079秒 |
| 所要時間の平均 | 5.462秒 | 11.113秒 |
| 平均再生成回数 | 0.44回 | 2.67回 |
| HTTPエラー | 0 | 0 |

時間は候補生成・検査・再生成を含むアプリ処理全体で、不採用回も含みます。decode tok/sの比較ではありません。保存された53応答の `timings` は添付の `request-timings.json` に収録しています。生成とLLMによる意味検査の両方でそれぞれのモデルを使うため、検査側の違いも採用率に影響します。

<details>
<summary>実際に出た全9回の句（不採用は最終候補）</summary>

「／」は改行です。Gemmaはすべて採用・5・7・5でした。

| 場面・seed | Gemma | 125B | 125Bの結果 |
|---|---|---|---|
| 草地・101 | あおぞらと／フェンスのそばの／たまごある | あおぞらに／オークのさく／くさちの ひ | 採用・5/6/5 |
| 草地・202 | あおぞらと／フェンスのそばの／たまごある | あおいそら／オークのほのまに／たまごひとつ | 採用・5/8/6 |
| 草地・303 | あおぞらと／フェンスのそばの／たまごある | あおぞらに／おーくのふぇんすと／たまごならぶ | 採用・5/8/6 |
| 坑道・101 | ともしびが／くろいこころを／みつめるよ | やみのかね／まつのほのおは／ほねはこなに | 採用・5/7/6 |
| 坑道・202 | ゆらぐひが／くろいこころを／てらしだす | やみのかね／まつのほをくろい／ほねのこな | 採用・5/8/5 |
| 坑道・303 | ゆらぐひが／くろいこころを／てらしだす | やみのなか／まつのひが じっと／くろい いし | 採用・5/8/5 |
| 雪斜面・101 | ゆうぐれの／しろいしずけさ／てにのこる | ゆきのななめ／かなはつめたい／よるのそらに | **不採用** |
| 雪斜面・202 | ゆうぐれの／しろいしずけさ／てにのこる | ゆきのかた／かなてにかな／ゆうやのつめ | **不採用** |
| 雪斜面・303 | ゆうぐれの／しろいしずけさ／てにのこる | ゆきのけもの／かななてにふえる／ゆうやのつめたい | **不採用** |

雪斜面の3回は、意味の対応が取れない語、日本語の不自然さ、音数超過、同一候補の再提示が残り、6回の再生成後に不採用となりました。Gemmaも同じ句を繰り返す傾向があり、この9回で日本語能力全般を評価する意図はありません。

</details>

## メモリの観測

モデル読込み後・推論前から測定完了まで、OS全体のswap使用は21,961.75MiB → 23,787.75MiBで、約1.78GiB増加しました。同区間のswap-outは約2.09GiB、swap-inは約0.33GiBです。以前から使われていたswap約22GBを、125B単体の消費量とは解釈していません。

5秒周期の28サンプルでは、メモリプレッシャーはnormalが11回、warningが17回で、criticalは観測していません。計測後にサーバーを停止するとnormalへ戻りました。計測中はMinecraft・ドギドの常駐サーバー・モデルのダウンロードやビルドを止めていますが、OS全体の値なのでバックグラウンド処理の影響は含みます。

## 確認したいこと

このsequence位置不整合や `mtp_flush` のエラーが、既知の挙動か確認いただけると助かります。起動設定で気になる点や、MTP・catch-up merge・graph cacheなどのうち、次に比較すべき条件があれば教えていただけますでしょうか。

大きなモデルを手元のMacで試せるよう公開していただき、ありがとうございます。別の実用タスク・環境からの動作報告として参考になれば幸いです。
