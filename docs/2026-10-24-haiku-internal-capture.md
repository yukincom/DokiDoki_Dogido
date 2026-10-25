# 2026-10-24 発句の内部表現採取

自動発句の既存モデル要求に採取指定を添え、実際の生成中に得たdecoder残差を保存する。採取用の再生成、追加プロンプト、感情ラベル付け、生成中のJ-lens学習は行わない。MLXとGGUFの記録形式を揃えるが、異なる重み・量子化・実装の数値を同一条件とは扱わない。

## 記録の契約

- 対象はGemma 4 26B A4Bの30層・各2816次元。境界は各decoder blockの出力、最終正規化より前。
- APIの応答IDごとに `manifest.json` と `hidden.f32` を保存する。数値はlittle-endian float32、形状は `[記録位置, 層, 次元]`。
- `hidden[i]` は `prompt + 採用済み出力[:i]` を処理した結果で、`predicted_token_ids[i]` を予測した状態。`input_position = prompt長 + i - 1` を検査する。
- EOSや停止文字列に含まれるtokenも、実際に採用された予測として数える。API本文はnativeのchat parserで整形されるため、生のtoken列や `rendered_text` との文字単位の一致を条件にしない。
- MTPはtargetモデルが検証して採用した位置だけを記録する。棄却候補・draftモデルの活性を混ぜない。
- 見どころ（`haiku_irony`）は `dogido_capture_full: true` を同じ要求へ添え、native生成が終了するまで採用済みの全位置を保存する。採取上限は `max_positions: null` として記録し、生成自体のtoken上限・停止・取消は変えない。全採取の指定だけでは採取を開始せず、`dogido_capture: true` も必要。
- その他の要求の既定上限は256位置、`DOGIDO_CAPTURE_MAX_POSITIONS` で1〜4096位置に指定できる。上限後も通常生成を続け、記録状態は `partial` にする。`shape[0]`、`positions`、`predicted_token_ids` は保存したprefixだけに揃える。
- `complete / partial / failed / cancelled` を区別する。ファイル欠落・hash不一致・非有限値・位置不一致は、観測済みの値として採用しない。
- モデル重み、tokenizer／templateを含むモデル資産、実行実装の識別情報を記録する。GGUFの変更パッチと実行ファイルはビルド記録で結び、ソースだけ変わった古いビルドを起動しない。

1位置は約330 KiB、256位置は約82.5 MiB。これは1回のモデル要求の値であり、一句には複数の要求が含まれる。保存上限は生成長の変更ではない。自動削除や既存記録の上書きは行わない。

## ドギドとの接続

`DOGIDO_HAIKU_INTERNAL_CAPTURE_ENABLED=true` と `DOGIDO_CAPTURE_DIR` を指定する。既定は無効。採取器にも同じディレクトリを指定する。

既存の `haiku_irony / haiku_scene / haiku_draft / haiku_line_regeneration` のHTTP本文へ `dogido_capture: true` を追加する。要求本文のプロンプトとモデル呼出回数は変えない。通常雑談と川柳相談の編集はこの採取指定の対象外。

`irony / scene` は既存の会話用接続先、`draft / line_regeneration` は既存の川柳用接続先を使う。接続先を分けている場合は、両方を採取対応サーバーへ接続する。片方だけ対応している場合は、未採取の要求を欠落として残す。本体の一般起動は `python dogido-rust/scripts/launch_dialogue.py --settings-dir .` のまま。採取有効時は起動ログに保存先を表示する。一般起動処理は常用サーバーの実行ファイルやユーザー設定を自動で変更しない。

保存句には、セッション・発句job・要求順・生成kind・応答IDと採取記録の検査結果を観測用のmetadataとして関連付ける。完成句の文字列と生成token列の直接対応は主張しない。読みの変換や複数回の生成・行置換を経た句も、元になった各要求から追跡する。

取消・古い入力世代・保存失敗は、保存済みの句との結合を確定しない。採取器側に残った途中ファイルは未結合の観測として扱う。数値は句のJSONL本文へ埋め込まず、採取ディレクトリに置く。保存句に結合済みの採取物だけを、下記の句集・研究保全へ渡す。

## 句集の「内部表現」欄と研究保全

句の原記録 `internal_capture` を、既存の句集投影から「内部表現」欄へ表示する。見どころ・情景・句の生成・行の再生成を実要求順で並べ、採取状態、モデル、トークン数・層数・次元数、容量を示す。モデル名は採取時に照合したGGUFファイル名（拡張子を除く）またはMLXモデルディレクトリから取得し、Hugging Faceのsnapshotではキャッシュのリポジトリ名を使う。APIの `default_model` 等の別名は表示せず、モデル資産の記録がなければ「モデル未記録」とする。照合情報には応答ID、要求ID、hash、採取位置と採取時の失敗理由を残す。採取前の句は「この句には採取記録が保存されていません」と表示し、遡って採取したようには扱わない。

2026-10-25から、各要求の数値を「前（応答開始直前）」「中（生成位置を選択）」「後（生成末尾）」の三角で開閉する。前は最初のtokenを予測した位置、後は最後に採用されたtokenを予測した位置で、終了後に追加の推論は行わない。`partial` の記録に生成末尾があるとは表示しない。最初の比較対象だった「見どころの応答直前」「見どころの生成中」「川柳の生成直前」は、それぞれ見どころの前・中と、句の生成の前から参照できる。

表示は読み取り専用で、層・生成位置・次元を選び、保存済みfloat32値を64次元ずつ返す。値を丸めた説明文・要約・感情ラベルへ変換せず、J-spaceの読出語とも呼ばない。APIが返した生成文も別の折りたたみに原文のまま置く。数値全体と採取記録のダウンロードは維持する。

既存の30秒周期の研究保全workerが、選択済み研究保存先の `YYYY/MMDDHHMM/internal-representations/request-<要求順>-<要求記録hash>/` へ `manifest.json` と検証済み `hidden.f32` をコピーする。初回コピーではhash・長さと同じ要求の対応を検査し、完了札を最後に書く。成功・一部採取は数値を、失敗・取消は採取記録だけを保全する。未結合のファイルは取り込まない。保存先の新規選択、元ファイルの移動・削除、既存ファイルの上書きは行わない。

保全結果は句の研究snapshotへ `internalCaptureStorage` として添付する。コピーできない場合も元句と採取時の記録は残す。「保全済み」等の保存状態は句集の欄内に表示しない。一度保全した完了札は欠損後も保持し、ローカルコピーで破損を隠したり自動修復したりしない。再同期では完了札・採取記録・配列の長さを確認し、大容量配列の全hashは初回コピーとダウンロードで照合する。

欄内の「採取記録を保存」「数値データを保存」は既存の本棚HTTP窓口を使う。`GET /api/poems/{句ID}/internal-capture/{要求順}/{manifest|hidden}` は原句・要求・保存札を照合し、固定ファイル名だけを開く。設定済み保全先に保存済みならそちらを読み、未保全なら既存の採取先を読む。絶対パスをAPI入力にせず、シンボリックリンクをたどらない。GETは保存・修復を行わず、数値は全量をメモリへ載せずに配信する。数値ファイルが欠けても採取記録自体は取得できる。

同じ窓口の `/values?position=0&layer=0&offset=0` は、同じ検証済みファイルから1位置・1層の最大64次元だけを返す。queryは0始まり、画面は1始まり。範囲・積のoverflow・非有限値を検査し、読み出す前に従来どおりファイル全体のhashを照合する。古い要求の遅延応答は、別の表示位置や別の句の画面へ反映しない。

本棚関連のRust検証は90件成功・実音声1件ignore、画面側は93件成功、releaseビルド成功。コピーの再送、元ファイル削除後の保全先閲覧、欠損後の再試行、保存済み破損・完了札欠落時の非修復、リンク・パス逸脱の拒否、HTTPの送信元制約と配信内容を確認した。先の常用8080隔離試験の実採取物（145・192・46位置）を一時的な句集へコピーし、Mac側の句・採取先を参照できない条件で研究保存先から計6ファイルを取得、全hash一致と実画面の3件表示を確認した。検証用の句は通常の句集へ追加していない。

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

上記の実装検証時点では常用8080を切り替えていない。その後の明示依頼による常用設定は次節のとおり。実Minecraftからの入力、実TTS、MLXのpriority wrapper併用、長時間連続運転は未確認。

## 常用8080への適用と他クライアントとの境界

2026-10-24、ユーザーの明示依頼により、常用Gemma 4 GGUFを採取対応版へ切り替えた。境界はHTTP要求ごとの `dogido_capture: true`。サーバーの採取先設定だけでは、要求の採取は始まらない。

| 要求元と用途 | 内部表現の採取 |
|---|---|
| ドギドの自動発句4種（irony / scene / draft / line_regeneration） | ドギド側の採取設定が有効なときだけ指定を送る |
| ドギドの通常雑談・川柳相談の編集 | 指定を送らない |
| ヘルメス・にゃむる等の通常API要求 | 指定がないため採取しない |

これは要求の用途を分ける仕組みで、アプリの身元を認証するアクセス制御ではない。他のクライアントが明示的に同じ採取指定を送れば採取される。通常のOpenAI互換クライアントにこの独自項目を追加する変更は行っていない。

- 常用の実行ファイルは `/Users/yukin_co/mlx-server/gguf/runtime-capture-20261024-e6235ad/bin/llama-server`。元の `runtime/bin/llama-server` は保持する。
- 既存の管理アプリと `gguf/profiles.json` を使って切り替えた。重み、MTP、context、batch、alias、reasoning offは切替前と同じ。管理アプリによるプロセス識別も有効。
- `profiles.json` のGemmaプロファイルだけに採取先・重み等の識別ファイル・256位置の上限を設定した。Qwenプロファイルには採取環境変数を追加していない。現在の設定はGGUFのGemma用で、管理アプリでMLXへ切り替えた場合の採取を有効にするものではない。
- ドギドのローカル `.env` に `DOGIDO_HAIKU_INTERNAL_CAPTURE_ENABLED=true` と絶対パスの `DOGIDO_CAPTURE_DIR` を設定した。公開既定値は無効のまま。
- 採取先は `/Users/yukin_co/Documents/DokiDoki-Dogido/.dogido_memory/internal-capture`。句の保存先は既存の `.dogido_memory/rust-migration`。Git追跡外で、自動削除しない。
- 配備時の設定・実行識別・確認結果は `.dogido_tools/haiku-internal-gguf/deployment-20261024/` に保持する。元の管理設定は `profiles.previous.json`、ドギドの元設定は読み取り権限を限定した `dogido.env.previous`。重み・実行ファイル・起動引数を後から変更する場合は、対応する識別記録も再作成する。

常用8080に通常要求→採取要求→通常要求→ストリーミング要求を順に送り、採取要求だけ `[16, 30, 2816]` のcomplete記録が作られた。通常要求と採取要求の本文は一致し、採取後の要求へ採取状態が持ち越されないことを確認した。ヘルメス・にゃむるのアプリそのものを操作した結合試験ではなく、同じ共有APIで独自指定のない要求を検査した結果。

常用8080を使った隔離済みの模擬プレイでは、irony 145位置・scene 192位置・draft 46位置がすべてcompleteで、数値を検査済みの参照が保存句へ付いた。記録は配備フォルダ内の `live-haiku/result.json` と `entry.json`。試験の句は常用の句・記憶へ追記していない。これは実Minecraftや実音声の検証ではない。

ドギド本体は通常の管理アプリから起動し、「稼働中」と起動ログの「発句の内部表現採取: 有効」を確認した。マイクは停止状態。Minecraftへ通常どおり接続し、自動発句が行われると採取される。音声を使う場合は管理アプリでマイクを起動する。採取確認には、保存句の `internal_capture.requests` の状態・応答IDと、対応する採取先の `manifest.json` を照合する。

## 2026-10-25 見どころの全位置採取と生数値の閲覧

見どころの要求だけ256位置の採取上限を外した。実Gemma GGUF + MTPの隔離試験で300位置すべてを記録し、通常指定へ戻すと256位置のpartialになった。同じ生成文が保たれ、短い要求ではEOSを含む106位置までcompleteになった。全採取指定だけ・採取無効の要求はファイルを作らない。MLXの今回変更は単体・模擬native検証であり、全位置採取の実MLX試験は未実施。

保存済みの実採取物を使い、Mac側の元採取先を参照できない隔離句集から9箇所の数値を取得し、元バイナリの対応する64値とすべて一致した。実画面では三角の開閉、層変更、次の64次元、最後の位置と停止token ID 106の表示を確認した。64値の表示は実測1,491〜1,497文字。現モデルは1位置あたり30層×2,816次元なので、300位置で25,344,000値・約96.7 MiBとなる。全量を十進数で展開すると数億文字規模になるが、画面は開いた箇所の64値ずつを読み込む。

今回の検証はPython採取14件・起動識別2件、Rustの要求同一性1件・本棚70件、画面側97件、GGUFとRustのreleaseビルドが成功。レビューも完了。実Minecraftと実音声の検証は含まない。実測と画面証跡は `.dogido_tools/haiku-internal-gguf/full-capture-20261025/` に保持する。

常用Gemma GGUFも `/Users/yukin_co/mlx-server/gguf/runtime-capture-20261025-41c05ee/bin/llama-server` へ更新した。切替前のPID・起動引数・cwd・管理識別・待機状態を確認し、既存の管理アプリ経由で再起動した。重み・MTP・context・batch・通常の256位置上限は維持し、実行ファイルと採取識別記録だけを更新した。常用8080でも300位置completeと後続の通常要求の採取なしを確認。旧実行環境と変更前の設定を保持する。

`/Applications/ドギドの本棚.app` を書類側の最新版で起動し、既存111句を読み込み、以前に採取された句の前・中・後と生数値を実画面で確認した。検証用の句は通常の句集へ追加していない。ドギド本体は作業時点で停止していたため、停止状態を維持した。次回の通常起動から見どころ全採取の指定を送る。
