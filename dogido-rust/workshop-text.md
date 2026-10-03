# 川柳のテキスト相談室

保存済みの句と当時の材料を使い、Minecraftなしでドギドと相談するローカル窓口。
`start_workshop_text.command` を開き、表示された `http://127.0.0.1:5057/` にアクセスする。
既存の会話モデルとPython環境を使う。新しいモデル・音声サーバーは起動しない。
この専用起動ファイルは、従来どおり実行中のプロジェクト内の `.dogido_memory/rust-migration` を読む。
通常本体と同じ設定済み保存先を使う場合は、リポジトリルートから
`python dogido-rust/scripts/launch_workshop_text.py --settings-dir .` で起動する。
`--memory-dir` で別の保存先も指定できる。相対パスは設定フォルダ基準で、既存記録は移動しない。

句を選ぶと本体と同じRustのworkshop処理が始まる。音声は使わず、表示した返答だけを
直近4往復の相談履歴へ引き継ぐ。発話の `audio_disabled` と `text_displayed` を分け、
音声の再生完了とは記録しない。検査・提案・採否も既存のコードを通す。
採用した変更は元の句の保存先へrevisionとして追記し、句集の表示を更新する。
原句・当時の材料は保持する。提案や比較だけでは保存しない。保存失敗時は元の句と案を残す。
別の相談が先に同じ句を変更した場合は保存せず、最新版を開き直すよう案内する。
「この言葉に変更しよう、どう？」で相談した一案は、続く「うん、変えて」で選べる。
会話はプロセス内に保持し、採用済みの句は再起動後も句集から開ける。
更新時の会話移行にはCLIの `backup --file` と起動時の `--resume` を使う。
終了した相談と未採用案も、元の行記録を照合して引き継ぐ。
終了は画面の「窓口を終了」または起動ターミナルでCtrl+C。

## プロンプトの調整

「プロンプト調整」からsystem指示・相談の指示・行動別の指示などを編集できる。
`{{player}}`、`{{context}}`等の差し込み欄は、コードが発言・句・材料・履歴に展開する。
差し込み欄の欠落・追加・重複はエラーにする。変更は次の発言で取り込み、処理中の発言には混ぜない。
「直近に実際に送ったプロンプト」で、展開後の要求と使用した設定バージョンを確認できる。
固定応答だけの発言ではモデルへ送信しないため、ここは更新されない。

設定はこの窓口の全接続に共有され、`.dogido_tmp/workshop-text/prompts.json` に保存する。
ゲーム用のテンプレートやソースファイルは変更しない。画面と別のツールが同時に編集した場合は
versionを照合して上書きを拒否する。初期値を読み込んで保存すれば既定の指示に戻せる。
コード側の音数・採否・保存・構造検証はプロンプト編集の対象外。

## CLIでの確認・保守

既存Pythonで `scripts/workshop_text_client.py` を実行できる。
以下の `PYTHON` は環境のPython、`CLIENT` はスクリプトのパスを表す。

```sh
PYTHON CLIENT poems
PYTHON CLIENT open --key SAVED_POEM_KEY
PYTHON CLIENT say --session SESSION_ID --text 'ふゆむって、どういう意味？'
PYTHON CLIENT prompts-get > prompts.json
# prompts.jsonのsettingsを編集
PYTHON CLIENT prompts-set --file prompts.json
PYTHON CLIENT last-prompt --session SESSION_ID
```

HTTPでも同じ操作ができる。GETは `/api/poems`、`/api/sessions`、`/api/prompts`。
POSTはJSONで `/api/open`（`key`）、`/api/input`（`session_id,text`）、
`/api/snapshot`・`/api/last-prompt`・`/api/interrupt`（`session_id`）、
`/api/prompts`（`settings,expected_version`）。返答を受け取ったクライアントは
`/api/displayed`（`session_id,turn_id`）で受領を通知し、次の会話へ引き継ぐ。
CLIの発言コマンドは待機・受領通知まで行う。
