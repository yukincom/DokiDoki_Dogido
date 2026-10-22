# ModernBERT依頼5分類の組み込み準備（2026-10-15）

**2026-10-21の対象・後継仕様:** [発句の生成処理と知識回答の対象](2026-10-21-generation-and-knowledge-scope.md)。自動発句の意味・自然さの評価LLMは撤去。本体の国語は詩の基本知識だけを残し、専用学習対話は対象外。Minecraft知識・現在観測の拡張分類は次回学習用で、本体未採用。以下の以前の日付の記録は当時の状態。

## 状態と責任境界

学習済みModernBERTをRustから呼ぶ部品を追加し、同日に採用されたv1.9 seed43を本体の共通受付へ接続した。10月15日のユーザー実機試験で履歴中の依頼による再発行が判明し、10月16日に本体の分類入力を現在発話だけへ変更した。現行仕様と修正後の検証は末尾「依頼分類を現在発話だけに限定」を参照。以下の履歴付き接続・評価は変更前の記録として残す。

- `dogido-rust/src/request_classifier.rs`：原文の整形、5分類の型、応答検証、取消と回収。
- `dogido-rust/scripts/request_classifier_worker.py`：採用記録・重みのhash検査、ローカルCPUでのtokenize・推論・確率算出だけ。
- `dogido-rust/src/python_worker.rs::Helper`：既存の上限付きJSONL通信、timeout、子プロセスの終了を再利用。
- `dogido-rust/examples/request_classifier.rs`：原文履歴を含むJSONLを一件ずつ検査する入口。本体の受付や操作queueは作らない。

分類結果だけでMinecraft操作を実行しない。hotbar、候補、所有者、期限、観測鮮度、session/world epoch、命令・結果・ACKの照合は既存Rust/Fabricの担当を維持する。

## 既存経路の調査結果

共通の接続候補は `dialogue/submit.rs::submit_impl` の入力世代更新・取消後、剣／敵／知識／馬などの早期returnより前。外部のテキスト・音声APIはここへ合流する。

Minecraftイベントは `dialogue/observe.rs` → `combat.observe` → `combat::select_body` → `queries::answer` が `event.meta.user_text` を先に処理する場合がある。将来の接続では既存 `GameEvent::without_player_input()` を使い、危険の観測・警告は保ちながら入力の意味判定を共通受付へ合流させる必要がある。

| 分類 | 接続時に置換する依頼認識 | 再利用する処理 |
|---|---|---|
| `equip_sword` | 語句即決、`assist_route`、intent生成・再生成と依頼性の再検査 | `assist/state.rs::AssistState::issue` のhotbar選択・capability・期限・重複・結果検証 |
| `recall_mount` | 通常会話と戦闘支援の `Stage::Intent` / `list_horses` | `horse_recall_runtime.rs` の `Prepared::resolve`、候補取得、個体選択、実行前再検証、enqueue |
| `enemy_information` | 敵情報を求めているかの入口判定 | `combat/queries.rs` の現在観測・個数・方位・距離計算と発話直前の再計算 |
| `not_request` / `other_request` | 上記3操作の開始を抑止するための共通結果 | 知識、workshop、国語対話、通常会話など残りの既存分岐 |

5分類には「個数／方角／距離／対象」の情報がない。敵情報の詳細抽出は別に残る。分類結果を既存 `queries::answer` に渡すだけでは、語句判定で再び回答なしになる場合があるため、依頼認識と詳細抽出・計算を分離して接続する。

`chat_runtime.rs`、`input_context.rs`、`chat_tools_runtime.rs`、`turn.rs` の馬候補準備も同じ結果を参照させる。旧intentの `confidence` や `evidence` を捏造して既存検証を通す構成にはしない。

## モデル受け渡し契約

Colab側の既存 `adopted-model.json` を読み込む。追加の採用理由や評価情報はそのまま保存できる。新規に必要な別形式のmanifestはない。

- `schema_version: 1`、`path_base: "repository_root"`。
- `checkpoint_path` は**モデルを保管するリポジトリ**からの相対パス。ドギドのルートへ読み替えず、CLI/APIで `model_root` を明示する。
- `checkpoint_files_sha256` に `config.json`、`model.safetensors`、`tokenizer.json`、`tokenizer_config.json` のhashが必須。指定全ファイルを読込前後に照合する。
- `evaluated_input.max_history_utterances`、`max_length`、`truncation: false` を尊重。旧v1.8 seed44は最大10発話・256 token。再学習側で512を採用・評価した場合は、その採用記録を使う。
- `config.json` の `model_type=modernbert`、`id2label` / `label2id` の正確な5分類と順序を検証。
- float32、CPU 4 threads / interop 1、SDPA。ローカルsafetensorsのみ、remote codeなし、自動ダウンロードなし。

学習用依存は `dev_tools/request_classifier/requirements-train.txt` の既存環境を使う。常駐ドギド用Pythonへ重い依存を自動追加しない。診断では使用するPythonを明示する。

## 入力・出力契約

`Input { text, context: [{role, text}] }` を受け取る。履歴は古い順、user/assistantのみ。入力の全文をそのまま次の既存学習形式にする。注釈・gold・id・分類理由はモデルへ渡さない。

```text
履歴:
ユーザー: 過去の発話
ドギド: 配送済みの発話
現在のユーザー発話:
現在の原文
```

空履歴は `（なし）`。`dataset.model_example` と同じ整形で、空白・改行・長文を正規化／短縮しない。現在発話は履歴に重複させない。履歴件数やtoken上限超過は失敗として返す。

応答は `schema_version`、毎回の `request_id`、採用記録全体の `manifest_sha256` を照合する。5ラベルはID順に `not_request / enemy_information / equip_sword / recall_mount / other_request`。確率は同じ順序の5要素、全値・合計・argmaxとの一致、token数、有限の計測時間をRustで検証する。confidence閾値や別分類へのfallbackは追加していない。失敗と `not_request` は別である。

成功応答に `error` キーは含めない。取消、通信失敗、ID/model不一致、不正値ではworkerを回収し再利用しない。通常終了も既存Helper経由で待つ。読込timeoutと推論timeoutは呼出元が別々に指定する。

## 準備時点の接続作業（同日、末尾の本体接続で実装）

1. **全文履歴**：`History::push` は `clip` で空白整理と80文字への短縮を行う。`rows()` には未回答userやevent/silent等も入る。既存の履歴正本に、分類用の全文を配送・取消契約に沿って保持する方法を決める。`completed_pairs()` は直近3ペアだけで、単独の敵警告assistantを落とすため代用しない。
2. **一入力一分類**：ロック外で実行し、既存watch/jobsで取消。完了時にsession instance・入力世代・pending ID・原文・sourceを照合する。`submit_inner(expected_generation)` は再開に使えるが、値の存在だけを「分類済み」とみなさない。
3. **再送**：`reply_queue::resume_reply_input` は世代を更新して再送する。分類結果は入力に付随して保持し、世代だけのcacheで再分類しない。新規入力と内部再開を分ける。
4. **置換の接続**：上表の依頼認識を同じ分類結果へ統一し、二段目の個体選択・敵の詳細抽出・既存操作検証へ渡す。常駐workerの所有、設定、起動時の失敗方針を本体へ接続する。
5. **新モデルと実機**：再学習重みの採用記録・実予測を確認後、現行受付との比較とMinecraftの敵質問・剣・乗用動物、取消・再送を検証する。

## 診断手順

ドギドのルートから実行。`MODEL_ROOT` はモデル保管リポジトリ、`CASES` は `id/text/context` を持つJSONLのパスを指定する。

```bash
./dogido-rust/cargo.sh run --locked --example request_classifier -- \
  --manifest "$MODEL_ROOT/adopted-model.json" --model-root "$MODEL_ROOT" \
  --python "$PWD/.dogido_tools/request_classifier/.venv/bin/python" \
  --worker "$PWD/dogido-rust/scripts/request_classifier_worker.py" \
  --input "$CASES"
python3 -m unittest discover -s dogido-rust/scripts -p 'test_request_classifier_worker.py' -v
./dogido-rust/cargo.sh test --locked --lib request_classifier::
```

CLIの既定timeoutは読込60秒／一件5秒。結果はstdoutへJSONLで返す。重み・モデル・本体の一般起動設定は変更しない。

## 検証記録

- Python補助の標準ライブラリ検査：10件成功。
- Rust分類器の通信・不正応答・timeout・取消・回収：6件成功。
- Rust全体 `test --all-targets --locked`：747件成功、1件ignore、失敗0。作業開始前からの別作業の変更を含む現在のmain作業ツリーで確認。
- 旧v1.8 seed44を実際にCPUへ読み込み、以前の比較199件をRust → 既存Helper → Python推論で一巡：**199/199件で既存予測と一致**。確率の最大差は1.11e-16、最大入力154 token。群をまたぐID重複を避けて `suite/id` に一意化した。
- [検証結果と全予測](../dev_tools/request_classifier/reports/integration-prep-20261015.json) に元データ・採用記録・重み・実装のhashを保存。診断一巡の時間は正式な速度比較には用いない。
- 独立したコードレビューで修正必須の問題なし。

旧seed44の一致検査は接続の互換確認であり、778件再学習モデルの精度・採用、本体・Minecraftの動作確認とは別である。現在の受付・実行処理は未変更。既存ファイルへの今回の差分は `lib.rs` のモジュール登録1行と `dogido-rust/README.md` の案内1行のみ。

## 新seed43の採用と追加確認（同日）

ユーザーがv1.9 seed43を選択し、モデル保管側の `adopted-model.json` を更新した。用意した読込処理のコードは変更せず、履歴10発話・全文512 tokenの条件で読み込めた。固定199件は検品時の新43予測と199/199件一致（元の正解177/199）。本体の会話受付・操作処理への接続は引き続き未実施。

ユーザー指定で現在の `data/regression.jsonl` の `regression-025` を「『馬を呼んで』と私は今、お願いしているんだよ。」に修正し、採否台帳と検査ハッシュを同期した。新43で修正文を別途確認すると `not_request` のままだった。正解 `recall_mount` と過去の比較結果は保持する。詳細はモデル保管リポジトリの `integration-v19-verification.json` に保存。


## 本体接続（同日）

### 実装

- `dialogue/submit.rs` の既存共通受付で、初回入力だけを `request_runtime` → 既存 `request_classifier` / `python_worker::Helper` へ渡す。モデルはCPU上に一度読み込み、共有workerで直列推論する。起動時の読込失敗は待受前に終了し、推論失敗ではその入力を失敗として記録する。
- `equip_sword` は既存 `AssistState::issue`、`enemy_information` は既存の敵情報計算、`recall_mount` は既存の候補選択・命令queueへ接続する。有効時は剣・馬の依頼判定LLMを呼ばない。敵情報は個数・対象等の詳細解釈と発話直前の再計算を保つ。
- `not_request` / `other_request` は上記3経路を開始せず、知識・workshop・国語対話・通常会話の既存処理へ進む。本体接続時点では共有プロンプト本文を変更していない。その後の文面整理は下記「呼び寄せ選択プロンプトの整理」を参照。
- 初期接続では既存 `History` 内に分類用の直近10発話を保持し、userを初回受付時、assistantを配送確定時に追加していた。10月16日の変更で分類専用履歴を廃止し、本体の分類入力を現在発話だけへ限定した（末尾参照）。
- 通常の分類待ちは新入力で取消す。危険中は既存 `reply_queue` の予約・取消を使い、後続入力で先行する知識質問を失わない。古い操作依頼は実行しない。再送時は原文・sourceに結びついた分類結果を保持し、宛先確認やFIFO再開で再分類しない。
- 非同期結果と再送の受付で、接続個体・入力世代・原文・sourceを照合する。手動停止、次元変更、接続し直し、終了の取消を既存の状態管理へ接続した。新しいHTTP入口・操作queue・プロンプトは追加していない。

### 通常起動の設定

既存の `.env` で `DOGIDO_REQUEST_CLASSIFIER_MODEL_ROOT` に採用記録 `adopted-model.json` があるモデル保管リポジトリを指定する。`DOGIDO_REQUEST_CLASSIFIER_PYTHON` は準備済みの分類用Python環境を指定する（省略時は設定フォルダの `.dogido_tools/request_classifier/.venv/bin/python`）。この端末では新43の保管場所と既存の分類用Pythonを設定済み。

`DOGIDO_REQUEST_CLASSIFIER_LOAD_TIMEOUT_MS=60000`、`DOGIDO_REQUEST_CLASSIFIER_TIMEOUT_MS=5000` が共有既定値。`model_root` 未指定の環境は従来動作を保つ。有効にした後の不備・推論失敗で旧依頼判定へ自動的に戻すことはない。

### 接続後の検証

- 追加のRust回帰7件：5分類、旧剣判定による上書き防止、取消・先行原文保持、FIFO順と再送、古い操作の破棄、失敗時の予約解除、同一IDでの再登録（job満杯を含む）、10発話・配送重複防止。
- 起動設定・Python workerの検査39件成功。全体のRust検査754件成功、1件ignore。`clippy --all-targets --locked -- -D warnings`、releaseビルド成功。
- 新43の実重み＋本体HTTPを隔離ポートで起動し、5分類を各1件確認。乗用動物の呼び寄せは**模擬LLMへの個体選択1回**だけで既存commandを発行し、模擬ACKを確認した。依頼判定の `list_horses` 呼出は0回。共通分類を含む6入力の限定接続検証であり、分類精度の再評価や速度比較ではない。
- [実重みを使った接続結果](../dev_tools/request_classifier/reports/integration-runtime-20261015.json) に採用記録・実行ファイルのhash、原入力、予測確率、接続先、呼出数を保存した。実行後に今回所有した本体・補助プロセスは終了済み。
- 既存LLMサービスやMinecraftへの操作は行っていない。ゲーム内の実行・音声・実表示は未確認。


## 呼び寄せ選択プロンプトの整理（2026-10-15）

変更案の確認・承認後、`dogido-rust/src/assist/horse_recall/prompts.json` の `selection` から「今回の原文が依頼でなければ選択を中止して会話する。」の1文だけを削除した。新43で `recall_mount` と分類してから個体選択へ進む経路で、LLMに依頼かどうかを重ねて判定させる説明を省く。本文は813文字から788文字へ25文字減少（トークン数ではない）。

名前・種類・直近の乗用履歴による個体選択、曖昧な場合の確認、候補の利用可否、evidence・confidence・出力形式、未実行の操作を成功扱いしない指示は維持する。共有人格・通常会話の文面は変更しない。分類器未設定時に使う旧 `intent` / `native_intent` も維持する。今回の変更はコード側の受付条件・検証条件を変更しない。

### この文面変更後の検証

- `./dogido-rust/cargo.sh test --locked --lib assist::horse_recall`：17件成功。人格・候補の段階別提示、tool選択、候補が利用不可の場合、evidence等の既存検証を確認した。
- `./dogido-rust/cargo.sh test --all-targets --locked`：全体754件成功、失敗0件、1件ignore。文字列削除だけの変更のため、削除文との完全一致だけを検査する新規テストは追加せず、既存の契約・接続テストを再実行した。
- `./dogido-rust/cargo.sh build --release --locked`：成功。埋め込みプロンプトを通常起動用実行ファイルへ反映した。
- 準備済みPythonによる `dogido-rust/scripts/launch_dialogue.py --settings-dir . --check`：成功。
- JSON全項目を照合し、`selection` の承認済み1文削除以外に差がないことを確認した。今回変更した追跡対象ファイルはプロンプトと本書の2件だけ。
- 今回は実LLM・Minecraft・音声の検証と常駐サービスの再起動は行っていない。上記の実重み＋模擬通信の接続結果は文面整理前の記録であり、今回の文面による実モデル応答の証拠としては扱わない。


## 依頼分類を現在発話だけに限定（2026-10-16）

### 実機で確認した問題

10月15日23時台のユーザー実機試験では、呼び寄せ後の「ありがとう」「こっちが来ました」で再び呼び寄せ命令が発行された。剣の持ち替え後も「お、センキュー バッチリじゃん」「今度は」を新43が `equip_sword` と判定し、現在の原文をevidenceに新しい命令を発行していた。これは過去命令の再配送ではなく、分類誤りに基づく新規発行である。

新43の実重みを既存workerで確認すると、「とりあえず剣に持ち帰ってもらっていいかな」→「剣やな、持ち替えるで！」を履歴にした「お、センキュー バッチリじゃん」は `equip_sword`（約0.999965）、同じ現在発話の履歴なし入力は `not_request`（約0.884115）になった。短い手作業の文脈による比較であり、実機の全履歴の完全再現ではない。

v1.9の学習データには、完了後の感謝を `not_request` とする例（`train-497`、`train-582`、`train-586` など）が既に含まれていた。今回の実機事例は、その区別が別の言い回し・会話列へ安定して適用されなかったことを示す。上記例の存在だけを実運用での再発防止の証明にしない。

### 現行仕様

ユーザー指定により、通常受付と危険中の待機入力の両方で、既存 `classify_request` が現在の原文だけを受け取る。既存の入力形式は維持し、`context` を必ず空にする。

```text
履歴:
（なし）
現在のユーザー発話:
そのターンの原文
```

- 分類専用の全文履歴保持を廃止した。通常会話・馬の個体選択が使う既存会話履歴は維持する。
- 空白・改行・長文を含む現在の原文、source、入力世代、接続個体、取消、待機列の順序、内部再送時の分類結果保持は維持する。
- 「うん、お願い」「それで」などの省略を、過去の会話から操作依頼へ補完しない。現在発話だけでもモデルの誤分類はあり得るため、全入力の正答を保証する変更ではない。
- 重み・5分類・確率の扱い・既存操作検証は変更しない。過去の履歴付き精度177/199はこの運用条件の精度ではない。診断用の低層 `Input { text, context }` と学習時の入力整形は、過去条件の再現用に維持する。

### 修正後の検証

- 分類受付の回帰7件成功。通常時・危険中FIFOの両方で現在原文だけを送ること、通常会話履歴を保つこと、取消・再送・世代照合を確認した。
- Rust全体754件成功、失敗0件、1件ignore。clippy、releaseビルド、通常起動の `--check` 成功。
- 新43の実重み＋本体HTTP、模擬LLM・Minecraft・TTSで9入力を検査。呼び寄せ完了後の「ありがとう」「そうだね来たね」と、剣の持ち替え完了後の3発話では操作を再発行しなかった。明示的な敵情報依頼も正しく接続した。
- **「こっちが来ました」は履歴なしでも `recall_mount`（約0.999951）と誤分類し、模擬環境で余分な呼び寄せ1件を発行した。全件成功ではない。** 独立した現在発話だけの診断では「ポチが来ました」も誤分類。「今度は」は操作を発行しないが、期待 `not_request` に対して `other_request` だった。
- [全予測・接続結果](../dev_tools/request_classifier/reports/current-input-runtime-20261016.json) と[再現用スクリプト](../dev_tools/request_classifier/reports/current-input-runtime-20261016.py) を保存。分類器とRust本体は実物だが、ゲーム内実行・音声・実LLM応答の再確認ではない。検査専用プロセスは終了済み。常駐サービスは再起動していない。

### 次回学習用の保存

ユーザー指定により、次回は長文を外し、今回失敗した短い現在発話を集める。モデル保管リポジトリ（`colab-modernbert-ja-30m-2026-10-14`）の `dev_tools/request_classifier/curation/2026-10-16-short-runtime-failures/` に5件を保存。候補の履歴は空で、実機の原文・合成対照・実際の予測・検証条件を記録した。現在条件でも誤る3件と、履歴を外すと正答する過去の失敗2件を区別する。既存データへの統合・再学習・採用モデルの変更は未実施。
