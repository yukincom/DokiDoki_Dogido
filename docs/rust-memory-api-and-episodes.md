# Rust の記憶参照 API と評価記録

## 記憶の参照

`GET /api/v1/memory/haiku`、`profile`、`summary` は設定済み `memory_dir` を読み取り、既存の認証と `Cache-Control: no-store` を使う。GET はディレクトリ作成、保存、会話状態変更を行わない。

- `haiku` は既存 Rust 保存処理と同じ root / session の句を返す。壊れた JSONL 行は除き、評価ログは読まない。
- `profile` は既存の追加属性と四つの進捗項目を保持する。`summary` は保存済みの JSON オブジェクトを返す。
- ファイル未作成時は Python 版と同じ初期値。記憶無効時は `haiku=[]`、`profile={}`、`summary={}`。破損した profile / summary は初期値へ戻して読み取り失敗を診断ログへ残す。

## 判断の評価記録

記憶が有効な場合、重複を除いた受理済み game-event ごとに schema version 5 の決定記録を `eval/episodes.jsonl` へ追記する。観測、変更前状態、同期処理で選んだ発話・命令、adapter の新規結果を記録し、このファイルを記憶・プロンプト・判断へ読み戻さない。

記録は最大 256 件の待ち行列を通じて専用 writer へ渡す。通常停止時は受け付けた記録を書き終えてから終了する。満杯、シリアライズ・作成・追記失敗は診断ログへ残し、イベントの受理を失敗させない。したがって保存先の障害や過負荷時の全件保存は保証しない。

`result.scope=service_decision` は判断結果であり、スピーカーから聞こえた証明ではない。既存 assist の検証を通した新規 adapter receipt がある行だけ `adapter_execution_observed` を使う。同じ receipt の ACK 再送と、サーバーが推定した期限切れは新しい実行証拠にしない。

Rust の会話・発句は非同期処理なので、この記録は同期 game-event の選択までを表す。後で完成する本文・川柳、音声合成や再生完了、独立した player-input の進行は既存 turn / playback 診断の対象となる。`haiku_emitted=false`、入力の `interpreted=null` はこの境界を表す。発話の本文は選択時点のコード側原文で、後段 leaf の最終表現ではない。Rust が保持しない Python の音声プロファイル等には null / 空値を入れる。

## 確認範囲

Python 正本から生成した 12 ケースで schema version 5 の観測・発話・adapter 結果の形を比較する。ルーターでは認証、無効時の空値、未作成時と保存済みデータの GET、副作用のない参照を確認する。イベント経路では重複除外、命令発行、結果不一致の検証、ACK 再送、保存障害を確認する。これらはモデル・音声・常駐サーバーを起動しない自動検証であり、実 Minecraft / マイク / スピーカーの確認ではない。
