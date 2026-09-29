# 通常会話 planner の入力準備

`planner::prepare::prepare(model, &Input)` は、会話焦点を決めるモデルに渡す
`PreparedPlan` とコード所有の `fallback` を作る純粋関数。空入力または
`model=None` は `request=None` を返すので、呼出側はモデルを呼ばず fallback を使う。
通常の prompt・生成・schema retry・採否・repair 検査は既存 `planner::run` が担当する。

呼出側は現在観測の summary/observed_entities、実再生完了済みの履歴、現在文・認識原文、
所持品/音の質問フラグを渡す。帰宅時の受動モブ観測抑制、workshop の repair 無効化、
時間付き観測の生成は呼出側の責務で、ここでは履歴から世界観測を補わない。
現在文160文字、履歴の最後10件、観測の先頭16件、原文1000文字を維持する。
履歴・観測は件数制限を先に適用してから不適合行の除外と重複除去を行う。
所持品→音→不在指摘→在否質問→同定の優先と、平叙報告を質問扱いにしない条件を維持する。

構造物検索は共有 `chat_catalog` と `chat_topics` の filter、名前の最長一致は
`chat_validation::mentioned`、会話修復は `planner::repair` を再利用する。
観測正規化は grounding handoff と同じ関数を共有する。新しい辞書・モデル呼出は追加しない。
この単位は純粋 API の実装までで、実行 bridge への接続は別の統合作業。

正本 Python の `plan_player_chat` から採取した6,927例で、fallback と request の
全項目を比較する。引用・仮定・伝聞・複数対象・直前assistant一意参照、カタログ全ラベル、
所持品/音/帰宅の入力、pending repair・原文差・境界長・Unicode空白を含む。
不正な観測型は入口で拒否する。モデル・音声・Minecraftの実動作確認とは区別する。

```sh
python dogido-rust/scripts/generate_planner_prepare_fixtures.py
./dogido-rust/cargo.sh test --offline planner --lib
./dogido-rust/cargo.sh test --offline --test planner --test chat_grounding
```
