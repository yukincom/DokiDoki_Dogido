# 共有workerによる自由文読みの辞書token取得

**現行実装（2026-10-03）:** 読みの判定・token選択・整形・例外表はRust、UniDicのtoken取得はPython補助が担当する。旧 `dogido_server/tts_reading.py` と `_get_unidic_tagger` は廃止した。

- `dogido-rust/scripts/tts_shared_tokens.py` の `_reader = Unidic()` を、そのworker内の取得要求で共有する。
- `Unidic` は `tts_unidic_adapter.py` に置く。最初にtokenが必要になったときだけfugashiを初期化して一度warmupし、初期化失敗も同じインスタンス内で保持する。
- `tts_shared_tokens.handle` は `op="tts_tokens"`・`schema_version=1`・空でない `request_id`・文字列 `text` を受ける。応答は `schema_version`・`request_id`・`status`・`tokens`。Pythonは変換済み発話や会話状態を返さない。
- 途中の解析失敗では部分tokenを成功として渡さない。語種・品詞・読みの選択と、発話へ採用するかの判断はRustが行う。
- `.[tts-reading]` は任意の辞書依存として継続する。補助の全体像は [Rust本体の補助一覧](../dogido-rust/README.md#残すpython補助と資料)、単独取得口は [最小UniDic補助](rust-tts-adapter.md) を参照する。

## 移植時の接続・検証記録（旧Pythonの処理は終了）

以下は共有取得口を接続した当時の記録。Pythonに通常会話・workshopの他処理が残るという説明、旧辞書関数やPython最終result生成は現在の責務ではない。当時の試験件数・未確認範囲を保持する。

ワークショップの `reading` 要求は Rust `python_worker::Helper` が受け、既存の `tts_reading::prepare`、token 選択、`finish` で `spoken_text` を作る。表示本文・現在句・保存・編集の採否は変更しない。

- `off`、空、現行範囲の漢字がない文は読み用 IPC を送らない。ワークショップのほかの仕事に必要な既存 helper は存続する。
- 辞書が必要な場合だけ、同じ子へ `tts_tokens` を一回送る。新しい子は起動しない。
- `tts_shared_tokens.py` は既存の `_get_unidic_tagger` と token 投影 adapter を接続する。中立な句の読み変換と Tagger、warmup、初期化失敗 cache を共有し、第二の辞書を初期化しない。
- 語種・品詞による token 選択、優先読み表、例外表の適用は Rust が担当する。Python の旧 `reading` 要求は拒否するため、変換済みの文へもう一度例外表を掛けない。
- カタログの `reading_overlay`、句の `normalize`／`correct`／`signature` は別契約のまま。自由文読みへの overlay 適用は追加しない。

通信の要求／応答サイズ、15 秒の交換上限、呼出側の turn 全体の期限・取消と helper 回収を維持する。SDK の明示的な `unavailable`／`parse_error` だけを原文＋例外表へ戻す。成功した空 token 列は空の成功結果。不正 JSON、不完全 token、要求 ID 不一致、旧 `spoken_text` 応答、EOF、取消、期限切れは成功発話に変換しない。通信異常の子は閉じて回収し、後の要求で遅延応答を利用しない。

## 検証と残る境界

Rust の実子プロセス mock で、辞書不要時の通信ゼロ、同じ子での連続読みと後続の別操作、成功・空成功・辞書失敗、取消・期限切れ・不正応答・出力上限・終了後 PID 不在を確認する。Python の辞書 mock で、中立変換を先に呼ぶ場合／後に呼ぶ場合の factory と warmup 一回、初期化失敗の共有、途中解析失敗の全文破棄、厳格な token 投影、overlay の分離を確認する。文字と token の変換規則は既存の 5,376 件／7,730 件の正本比較 fixture を使う。

通常会話の逆向き stdio protocol も、原文の最終 result → 必要時だけ token 取得の形で接続している。会話の grounding・期待本文・想起条件・宛先・国語・知識と参照情報をすべて検証した後、Rust が音声用の読みを作る。通常会話とワークショップの読み以外の Python 処理は残る。自動発句の見どころは元々 raw 発話、句本体は確定 reading のため、新たな自由文 TTS 変換は足さない。実 UniDic を使うこの shared adapter の往復、実音声、Minecraft は未確認。

## 通常会話の最終resultと取得口

`tts_shared_tokens.handle` は `op="tts_tokens"`、`schema_version=1`、空でない `request_id`、文字列 `text` の四キーだけを受ける。応答は専用 adapter と同じ `schema_version`／`request_id`／`status`／`tokens`。独自の読み・モデル設定・履歴・overlay を渡さない。Rust の `tts_reading::tokens::decode` に未加工の応答を渡せる。追加プロセスを作らず、通常会話の既存子がこの取得口を処理する形で接続する。

通常会話の text 付き result（通常本文、宛先確認、知識、国語、無言）は Python の `emit_result` から原文のまま送る。Rust は既存の全検証後に `prepare` し、辞書不要なら stdin を閉じる。必要な場合だけ、同じ子へ `tts_tokens` 一件を送り、token 応答を `decode`／`finish` へ渡す。Python は最終原文を strip した文字列との一致を確かめ、同じ辞書 singleton から token を返して終了する。payload だけの振分け・照明判定・想起引継ぎは、この待機や読み処理に参加しない。

既に `spoken_text` を含む旧 result は拒否する。旧 fake helper が token 要求に応じず終了・無視した場合も、成功や manual fallback にしない。全体95秒・取消・子の終了待ちを維持し、tokenの要求ID、型、1MB上限、完全な改行付き応答も検査する。読み処理はモデルを呼ばない。通常会話の最終本文・メタデータを変更せず、派生した `spoken_text` だけを付ける。

この接続は実子プロセス mock で、通信なし、token 一回、SDK の成功／空／失敗、原文・メタデータ検査が辞書より先であること、旧 helper／不正応答／応答途中 EOF／異常終了、期限・取消・sender drop と PID 回収を検証する。Python の各早期 result と通常本文を検査し、実際の helper スクリプトについても SDK を mock にした stdio 往復・辞書不要時の終了を確認する。実辞書と実音声の確認を代替するものではない。
