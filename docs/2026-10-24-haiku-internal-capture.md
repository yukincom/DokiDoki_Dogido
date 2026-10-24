# 2026-10-24 発句の内部表現採取

自動発句の既存モデル要求に採取指定を添え、実際の生成中に得たdecoder残差を保存する。採取用の再生成、追加プロンプト、感情ラベル付け、生成中のJ-lens学習は行わない。MLXとGGUFの記録形式を揃えるが、異なる重み・量子化・実装の数値を同一条件とは扱わない。

## 記録の契約

- 対象はGemma 4 26B A4Bの30層・各2816次元。境界は各decoder blockの出力、最終正規化より前。
- APIの応答IDごとに `manifest.json` と `hidden.f32` を保存する。数値はlittle-endian float32、形状は `[記録位置, 層, 次元]`。
- `hidden[i]` は `prompt + 採用済み出力[:i]` を処理した結果で、`predicted_token_ids[i]` を予測した状態。`input_position = prompt長 + i - 1` を検査する。
- EOSや停止文字列に含まれるtokenも、実際に採用された予測として数える。API本文はnativeのchat parserで整形されるため、生のtoken列や `rendered_text` との文字単位の一致を条件にしない。
- MTPはtargetモデルが検証して採用した位置だけを記録する。棄却候補・draftモデルの活性を混ぜない。
- 既定の上限は要求ごとに256位置、`DOGIDO_CAPTURE_MAX_POSITIONS` で1〜4096位置に指定できる。上限後も通常生成を続け、記録状態は `partial` にする。`shape[0]`、`positions`、`predicted_token_ids` は保存したprefixだけに揃える。
- `complete / partial / failed / cancelled` を区別する。ファイル欠落・hash不一致・非有限値・位置不一致は、観測済みの値として採用しない。
- モデル重み、tokenizer／templateを含むモデル資産、実行実装の識別情報を記録する。GGUFの変更パッチと実行ファイルはビルド記録で結び、ソースだけ変わった古いビルドを起動しない。

1位置は約330 KiB、256位置は約82.5 MiB。これは1回のモデル要求の値であり、一句には複数の要求が含まれる。保存上限は生成長の変更ではない。自動削除や既存記録の上書きは行わない。

## ドギドとの接続

`DOGIDO_HAIKU_INTERNAL_CAPTURE_ENABLED=true` と `DOGIDO_CAPTURE_DIR` を指定する。既定は無効。採取器にも同じディレクトリを指定する。

既存の `haiku_irony / haiku_scene / haiku_draft / haiku_line_regeneration` のHTTP本文へ `dogido_capture: true` を追加する。要求本文のプロンプトとモデル呼出回数は変えない。通常雑談と川柳相談の編集はこの採取指定の対象外。

`irony / scene` は既存の会話用接続先、`draft / line_regeneration` は既存の川柳用接続先を使う。接続先を分けている場合は、両方を採取対応サーバーへ接続する。片方だけ対応している場合は、未採取の要求を欠落として残す。本体の一般起動は `python dogido-rust/scripts/launch_dialogue.py --settings-dir .` のまま。常用サーバーの実行ファイルやユーザー設定を自動で変更しない。

保存句には、セッション・発句job・要求順・生成kind・応答IDと採取記録の検査結果を観測用のmetadataとして関連付ける。完成句の文字列と生成token列の直接対応は主張しない。読みの変換や複数回の生成・行置換を経た句も、元になった各要求から追跡する。

取消・古い入力世代・保存失敗は、保存済みの句との結合を確定しない。採取器側に残った途中ファイルは未結合の観測として扱う。数値は句のJSONL本文へ埋め込まず、採取ディレクトリに置く。研究用アーカイブへ数値ファイル一式を梱包する変更は本接続の対象外。

## GGUF

現在の保存済みllama.cppソース `6190802c8d3391ee908fb254d12fc243ad4540c6` に対する小さな差分として保持する。既存の `llama_set/get_embeddings_layer_inp` を再利用し、Gemma4の最終層と、選択された出力行のbatch位置を補う。通常生成と `draft-mtp`、1 slotの文字入力に対応する。LoRA・control vector・他の先読み方式・複数候補・context shiftは対応を確認するまで観測成功として扱わない。

公式ソースと既存の保存bundleから当該revisionを用意してから、書類側リポジトリで実行する。モデルのdownloadや、常駐サーバーの切替はビルド処理に含めない。

```sh
python dev_tools/haiku_internal_probe/build_gguf_capture.py \
  --source "$LLAMA_SOURCE" --build "$CAPTURE_BUILD"

python dev_tools/haiku_internal_probe/launch_gguf_capture.py \
  --binary "$CAPTURE_BUILD/bin/llama-server" \
  --capture-dir "$DOGIDO_CAPTURE_DIR" -- \
  -m "$GGUF_MODEL" -np 1 --host 127.0.0.1 --port 8083 --alias default_model
```

`--` 以降は既存のnative起動引数を渡す。MTPでは通常の `-md / --spec-type draft-mtp / --spec-draft-n-max` も渡す。認証情報などの引数は研究記録へ保存しない。

## MLX

既存の `mlx_lm.server` を採取用入口から起動する。site-packagesは編集しない。`--identity-model` とnative側の `--model` には、同じローカルモデルディレクトリを指定する。起動時に重み・設定・tokenizer・templateをhash化し、各要求ではnativeが実際に選んだモデルと照合する。

```sh
export DOGIDO_CAPTURE_DIR="$CAPTURE_ROOT"
python dev_tools/haiku_internal_probe/mlx_capture_server.py \
  --identity-model "$MLX_MODEL" -- \
  --model "$MLX_MODEL" --host 127.0.0.1 --port 8083
```

既存のpriority wrapperを使う場合は、`--` より前に `--upstream-entry "$EXISTING_PYTHON_ENTRY"` を置き、後ろへその入口の既存引数を渡す。このwrapper併用はコード確認までで、実モデル検証はnative入口で実施した。

既存の独立採取器 `measure.py` とdecoder出力の観測hookを共有する。native生成が一token先の計算を進めるため、各出力をcacheの論理位置へ対応付け、採用tokenが届いた時点で該当位置だけ保存する。prompt cacheを利用しても位置はprompt全体を基準にする。採取要求はnativeの単一要求処理を使い、分散・MLXのdraft生成・LoRAは未対応の採取として記録する。

## 検証記録

GGUFの実モデルで、通常生成・MTP・prompt cacheあり／なし・保存上限を検証。16位置の記録は `[16, 30, 2816]`、40 token生成を32位置で区切った記録は `partial`。採取の有無で本文が一致し、数値の形状・有限性・hash・位置とtoken列を確認した。

同じ32 tokenの通常生成／MTP比較では、層ごとのcosineの最小値0.999787、平均0.999999、全体の相対L2差0.001523だった。実装内の計算形状が異なるため、bit単位の同一性は要求しない。この短い入力での対応確認であり、分析レンズの精度評価ではない。

MTPでは40 token生成時に先読み35 token中20 tokenを採用し、棄却を含む実行を確認。採取有効の要求の後にnativeの待機状態へ入り、復帰後の最初の要求でも採取できた。

起動引数から認証情報が記録へ入らないことと、ソース・実行ファイル・ライブラリの変更後に古いビルド識別を受け付けないことを自動テストした。Minecraft実プレイ・実マイク・実スピーカーの確認とは分ける。

MLXは手元の `isetnefret/Huihui-gemma-4-26B-A4B-it-qat-q4_0-unquantized-abliterated-mlx-4Bit`、revision `0305104730dfd6729d44a5caa06a06c0ba8520e2`、mlx-lm 0.31.3で確認した。以前のLM Studio QAT版とは別重みで、GGUFとの数値の直接比較には使っていない。採取ON/OFFの本文一致、同じpromptのcache再利用、別promptのprefill、16位置のcomplete・40 tokenを32位置で区切るpartialを確認。`cache_prompt: false` はGGUF固有なので、MLXのcache無効化の証拠にはしていない。

MLXの先頭4位置は既存 `measure.Capture` でも独立に計算し、nativeと同じprefill分割・同じtoken列で `[4, 30, 2816]` の全数値が完全一致した（最大絶対差・相対L2とも0）。全生成長や別実装の同一性を示す検証ではない。既存の分析記録やbindingは書き換えず、共通hookへの変更も新しい実装hashとして区別する。

両backendをRust本体の発句へ接続した隔離試験では、既存の模擬Minecraft観測・模擬音声出力と実モデルを使い、同じ句のJSONL保存まで通った。

| backend | 保存された各要求 | 採取状態 |
|---|---|---|
| GGUF + MTP | irony 145位置、scene 192位置、draft 45位置 | 3要求ともcomplete |
| MLX | irony・scene・draft各32位置 | 試験用32位置上限により3要求ともpartial |

どちらも `[位置, 30, 2816]` の形状・有限値・hash・token位置をRustが検査し、セッション・job・応答IDとともに既存entryへ保存した。ここでのcompleteは実際に生成されたtokenの採取完了を表し、モデル本文の完成度やfinish_reasonがstopであることを意味しない。

- Rust全体検査は882件成功・1件ignore。採取の失敗理由保持を補った後の関連6件とreleaseビルドも成功。
- Pythonは起動設定37件、MLX採取契約12件と13 subtest、GGUF起動識別2件成功。
- 模擬通信は採取・保存・取消・期限切れ・保存失敗の6ケースと、既存発句16ケース成功。旧LLM終了actionを返していた模擬テストを、現行のBERT `senryu_end` に合わせた。
- `clippy -D warnings` は変更行外の26箇所の警告で未通過。今回追加した採取処理の警告は修正済み。

常用8080のプロセス・起動設定は切り替えていない。実Minecraftからの入力、実TTS、priority wrapper併用、長時間連続運転は未確認。
