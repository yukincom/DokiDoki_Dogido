# GitHub Actions CI（2026-10-23）

同日の一時停止後、ユーザーの再開指示を受けてリポジトリのActionsを有効化した。既存のworkflowがなかったため、`.github/workflows/ci.yml`を追加した。

- 起動条件はブランチへの`push`だけ。定期実行・PRイベント・手動実行は設定しない。
- 同じブランチの古い実行は、新しいpushで取り消す。
- Ubuntu上でRustの全target、Python補助、本棚のJavaScriptテスト、導入用shellの構文を検査する。
- Rustは既存の`rust-toolchain.toml`と`Cargo.lock`を使用する。Pythonは専用venvを作り、既存のhash固定pip bootstrapを実行してからテスト依存を入れる。
- GitHub公式actionは確認したreleaseのcommit SHAに固定し、リポジトリ権限は読取りのみとする。

実機のMinecraft・音声・macOSアプリ、家庭用セットの配布はCIの対象外。既存の実機用opt-inテストも自動では有効にしない。

設定と既存テストをローカルで確認した後、更新ブランチへのpushでCIを検証し、mainへ反映する。
