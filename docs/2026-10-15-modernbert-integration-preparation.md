# ModernBERT依頼5分類の組み込み準備（2026-10-15）

## 状態と責任境界

学習済みModernBERTをRustから呼ぶ部品と診断CLIを追加した。本体の会話受付への接続・設定追加・常駐サービス切替はまだ行っていない。再学習モデルの検品・採用を待つ段階で、既存経路の接続点と入力・出力・取消の契約を準備する作業である。

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

## 本体接続で残る作業

1. **全文履歴**：`History::push` は `clip` で空白整理と80文字への短縮を行う。`rows()` には未回答userやevent/silent等も入る。既存の履歴正本に、分類用の全文を配送・取消契約に沿って保持する方法を決める。`completed_pairs()` は直近3ペアだけで、単独の敵警告assistantを落とすため代用しない。
2. **一入力一分類**：ロック外で実行し、既存watch/jobsで取消。完了時にsession instance・入力世代・pending ID・原文・sourceを照合する。`submit_inner(expected_generation)` は再開に使えるが、値の存在だけを「分類済み」とみなさない。
3. **再送**：`knowledge_queue::resume_knowledge_input` は世代を更新して再送する。分類結果は入力に付随して保持し、世代だけのcacheで再分類しない。新規入力と内部再開を分ける。
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
