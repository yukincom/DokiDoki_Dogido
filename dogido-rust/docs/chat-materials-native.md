# 通常会話の材料生成

更新: 2026-10-07。現行仕様は [通常会話の一回生成](../../docs/rust-chat-grounding-boundary.md)。

`chat_materials::prepare` はSessionの同じターンの `GameEvent`、設定、今回入力、現在のmodeと構造物、確定済み短期履歴、読み取りsnapshotから材料を作る。モデル・再生・保存は実行しない。`Labels` は既存の観測readerを共有し、一般知識、現在観測、過去の視認、player報告を混ぜない。

- 正本知識の照会は `Prepared::Knowledge`。既存の知識経路へ渡す。
- 現在の匂いへの明示質問は `Prepared::Fixed`。`announce_smell` の状態反映は所有者が行う。
- 通常対話は `Prepared::Reply`。`Reply::request` が話題選択と本文生成を同じ一回へ渡す。温度0.65、thinking無効、既存chat設定のモデルとtoken上限を使う。

`before_plan / after_plan / Leaf::input` は廃止した。内容照合・候補の言い直し・定型fallbackを材料層へ戻さない。取消・epoch・実再生と履歴の確定はSessionが所有する。既存の移植fixtureは観測入力の回帰資産として保持し、一回分のリクエストを作れること、snapshotを変更しないことをRustで確認する。
