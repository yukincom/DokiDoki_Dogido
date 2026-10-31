# 川柳の具体変更を一行全体で受け取る（2026-10-30）

本人の「あさひさして → あさひさし」という指定に対し、旧語・置換語の部分置換では末尾が残る取り違えが可能だった。具体変更のnative `stage_player_edit` は、LLMから `replacement_text` として変更後の一行全体を受け取る。部分変更でも残す言葉を含める。行ID・元句はBERTと現在snapshotからコードが補い、旧語を要求しない。

既存の `fill_classified_edit_target` で一行置換を指定し、既存Engineが元の行と比較する。同じなら `no_change` で保存しない。違えば他の二行を保持して `Pending` と `workshop_edits` の版照合・保存へ渡す。古い形式の `target_fragment` が付随しても、この入口では部分置換へ戻さない。保持中の相談案を選択する内部の部分編集処理はそのまま使う。意味・音数・品質の追加検査、モデル呼出しの追加はない。

## 検証

- Rust `workshop` フィルタ: 120件成功、5件失敗。新規の末尾削除・部分変更・同値指定、既存の他行不変・CAS・保存の試験は成功。
- 失敗5件はpromptの全文fixture比較4件とprojectionのgolden比較1件。今回の変更前のソースを一時領域に複製して再ビルドし、同じ5件の失敗を確認した。期待値の更新はこの変更の対象外。
- `check_workshop_edit_tool.py`: 模擬HTTPの26ケースが全成功。実入力「まず上の句をあさひさしに変えましょう！」を上五の具体変更に固定し、LLMが返した一行「あさひさし」から上句だけを変更、revision1件、他二行・元entry不変を確認。同じ元行「あさひさして」を返すケースは `no_change`、revision0件。いずれもモデル呼出し1回。
- `check_workshop_candidate.py`: 相談案の部分採用後の具体変更を含む既存ケースが成功。具体変更の固定応答を一行全体へ更新し、相談案を選ぶ内部の部分置換との接続を確認。
- `check_workshop_records.py`: 15個のroute/outcome assertは成功。ただし自動発句側の既存模擬handler（`check_haiku_runtime.py:41`）で `IndexError` が2回出ているため、例外なしの完走とは数えない。このhandlerは変更していない。
- 試験用debugバイナリのビルド、Python構文解析、`git diff --check` は成功。検査では一時領域の記憶と所有プロセスだけを使った。

Rust試験・試験用ビルドは `./dogido-rust/cargo.sh` に `--locked --offline --target-dir /private/tmp/dogido-part45-20261029/target` を指定。HTTP試験は `DOGIDO_RUST_BINARY` でそのdebugバイナリを指定した。生の要求・応答とログ、変更前の失敗再現は `/private/tmp/dogido-complete-line-20261030/` に保存。

書類側mainのコード・テスト・仕様へ反映済み。通常起動用releaseは未更新。実モデルの解釈、Minecraft、実音声は未確認。常駐サービスは再起動していない。
