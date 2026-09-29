# 通常会話の材料オーケストレーター

`chat_materials` は通常 `player_chat` の純粋な材料生成を Rust へ移すための API。
`before_plan` と `after_plan` の間を、既存の有界 planner 一回でつなぐ。
このモジュール単独では本番の Session / bridge へ接続されず、モデル・再生・記憶保存を実行しない。

## 入力の所有者

呼出側は同じターンの `GameEvent`、設定、`input_context::Context` 由来の入力、
状態機械の mode/current_structure、実再生済み履歴、既存の復帰話題を含む digest、
workshop の閉じた三つの prompt 項目、`ChatObservationMemory` の読み取り snapshot を渡す。
履歴は queued 発話から作らず、既存の completed reader を使う。
現在構造物を event から推測せず、既存の状態機械値を渡す。

`Labels` は `chat_observation` と同じ実装を共有する。音源 block 名は正本
`block_entries` の label / japanese を使う。所持品表示用の「長い名前優先」block label
で置き換えると、溶岩などの音源名が変わるため同じ reader と扱わない。

## 分岐と実行境界

所有者は各ターン開始時に以前の `player_chat_repair` をクリアする。以下の pure API は
元の状態機械フィールドを直接変更しない。

1. `before_plan` は知識質問なら `Knowledge` を返し、既存 knowledge runner へ渡す。
2. 現在の匂い質問ならコード固定の `Fixed`。`announce_smell=true` は、所有者が同じ
   event と時刻で既存の匂い発話済み状態を更新するための要求。純粋 API は更新しない。
3. それ以外は `Prepared`。内包の既存 `planner::prepare::PreparedInput` に request が
   ある場合だけ既存 `planner::run` を使い、ない場合は準備済み fallback plan を選ぶ。
   `Prepared` は event、snapshot、履歴などを凍結し、待機中の別観測を混ぜない。
4. 所有者が取消・epoch・セッション有効性を再確認したあと、検証済み Plan だけを
   `after_plan` に渡す。入力照合、evidence、信頼度等は既存 planner の担当。
5. 後半は修復の聞き返し、音を出す条件、候補と現在観測の照合、名前ポリシー、
   根拠なしの音固定文、workshop の材料制限、grounding 固定文、本文の順。
6. `Leaf::input` を既存 `chat_validation::Turn` / runner へ渡す。初回と必要時一回の
   言い直し、最終 fallback 検査を再実装しない。温度 0.65、thinking 無効を保持。
   モデル名と token 上限は従来の chat route 設定を呼出側が与える。
7. 発話・実再生結果・assistant 履歴・repair 記録の確定は従来の Session 所有。
   `Fixed.repair` / `Leaf.repair` は同じターンの短期修復注記であり、世界観測ではない。

従来の `Handoff::resolve` は accepted-plan 一致・current rows 一致・取消・一度だけの
確定を維持する。純粋な内容計算だけを crate 内の `handoff::project` に切り出した。
既存 bridge を経由する間は `Leaf.handoff_input` と `Handoff::resolve` を照合に使える。
完全 native の呼出側も Session/epoch の取消を省いてよい、という契約ではない。
新 API への通信公開、汎用 tools、追加モデル判定、時系列記憶更新は含まない。

## 元処理との比較

`generate_chat_materials_fixtures.py` は本体 Python の `_render_player_chat_reply` を直接
呼び、planner の戻り値を模擬し、leaf の引数を捕捉する。時系列の種は本体 `process`
で蓄積し、観測 snapshot は既存の temporal fixture generator の oracle を共用する。
新しい helper の空状態を正本としない。

415 状況について planner の全入力、本文の全 details、fallback、固定文、閉じた
prompt / validation payload を比較。帰宅・天気・地下・危険・現在/過去の視認と音・
ユーザー報告と assistant 履歴・修復・workshop 中の全材料制限を含む。
元 Python と比較すると既存 Rust の匂い判定が改行をまたいでいたため、その一点は
正本の regex と同じく改行をまたがない実装に修正した。

実 Minecraft、マイク、実モデル、TTS の確認をこの比較で代替したとは扱わない。
