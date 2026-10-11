# 2026-10-11 Mac管理アプリと既存検査の修復

## 管理アプリ

`apps/ドギド サーバー.app` に、本体・マイクの個別起動と停止、ターミナル出力の閲覧、発言だけの履歴へのリンクを用意した。実装・再ビルドは [アプリのREADME](../apps/dogido-server-manager/README.md) を参照。

- 本体の既存設定読込と `start_dialogue.command` / `start_voice.command` を使う。モデル・VOICEVOXを別途起動する経路は追加しない。
- 誰が起動したかによらず、実行ファイル、作業フォルダ、サブコマンド、接続先を照合する。本体は待受と `/healthz` のRust会話準備完了も確認する。別プロセスのポート占有や複数候補では起動・停止を無効にする。
- 停止ボタンの操作時にPID・開始時刻・実行ファイル・引数・作業フォルダを再確認し、そのプロセスへSIGTERMを送る。アプリを閉じてもサービスは停止しない。アプリを開くだけではサービスを起動しない。
- ログは照合済みプロセスの実際の標準出力・標準エラーのファイルを読み、末尾を自動更新する。アプリ起動分は `.dogido_tools/server-manager/logs/` に追記する。外部TTYへ直接出力するプロセスの過去出力は取得できないため、起動元での確認を案内する。
- 発言履歴は既存 `/dogido` の `view=utterances` 表示を使う。診断・参考情報を隠し、保存・配送・履歴の内容は変えない。管理アプリにも同じ表示スクリプトを同梱するため、今回の変更前から稼働している本体でも履歴画面を表示できる。

## 既存の6失敗とClippyの17指摘

[前回の全体検査](2026-10-11-checkpoint-verification.md)と[地形材料の検査](2026-10-11-observed-terrain-materials.md)で残していた不一致を修復した。

| 対象 | 修復内容 |
|---|---|
| `chat_topics` 2失敗 | 正本で変更済みの関西弁policy 4種を期待値へ反映。poolの参照indexと入力は維持 |
| `chat_world` 1失敗 | 森林等12 biomeの既存注記をbiome/climate期待値へ反映 |
| `haiku_context` 1失敗 | 同じ森林注記に由来するcatalog note・出典・source atomだけを更新 |
| `haiku_preparation` 2失敗 | portal 3種の役割注記と、森林注記由来の出典・atomを更新 |
| Clippy 17指摘 | 共通引数を構造体へまとめ、入れ子if・直後return・既定値の初期化・不要clone・テスト内MutexGuardの保持範囲を整理 |

fixtureは現行出力で一括再生成せず、正本の文言と影響先を照合して5ファイルの対応箇所を変更した。入力、句本文、読み、行出典、ケース数は変えていない。Clippyの許可属性は追加せず、モデルプロンプトや生成方針も変更していない。

## 検証

| 検査 | 結果 |
|---|---|
| Rust全ターゲット `test --all-targets --no-fail-fast --locked --offline` | 644成功・0失敗・0除外（lib 512、main 2、統合17対象130）。example 13対象もコンパイル済み |
| Clippy全ターゲット `-- -D warnings` | 成功・指摘0 |
| `build --release --locked --offline` | 成功。次回起動用の実行ファイルを更新 |
| 管理機能の模擬試験 | 48項目成功。外部起動検出・二重起動拒否・別checkout拒否・停止前同一性照合・マイク単独停止・ログ読取を含む |
| Macアプリ | AppKit/WebKitビルド・ad hoc署名・署名検証成功 |
| 実画面 | 既存本体PID 64602の稼働と実ログを表示。履歴リンクから既存の20発言を表示し、診断・参考欄がないことを確認 |

既存本体・モデル・VOICEVOXは停止・再起動していない。実マイクの起動停止と新しい本体でのMinecraft E2Eは未確認。管理アプリの操作試験は模擬プロセス、本物に対しては状態・ログ・履歴の読み取りだけで確認した。

開始時の復旧点は `63459cf`。検査ログは一時ディレクトリ `dogido-manager-20261011` の `final-rust-tests.log`、`final-clippy.log`、`final-release-build.log`。既存のユーザー編集文書・ライセンスは保持した。

## アイコン

3Dボディを参考にした立方体ドギドのアイコンを組み込んだ。元PNG、参照画像と生成プロンプトは [Resources](../apps/dogido-server-manager/Resources/icon-design.md) に保存。内蔵imagegenで制作し、ビルド時にICNSへ変換する。元の3Dモデルとテクスチャは変更していない。
