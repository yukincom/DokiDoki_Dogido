# GitHub Actions CI（2026-10-23）

同日の一時停止後、ユーザーの再開指示を受けてリポジトリのActionsを有効化した。既存のworkflowがなかったため、`.github/workflows/ci.yml`を追加した。

- 起動条件はブランチへの`push`だけ。定期実行・PRイベント・手動実行は設定しない。
- 同じブランチの古い実行は、新しいpushで取り消す。
- Ubuntu上でRustの全target、Python補助、本棚のJavaScriptテスト、導入用shellの構文を検査する。
- Rustは既存の`rust-toolchain.toml`と`Cargo.lock`を使用する。Pythonは専用venvを作り、既存のhash固定pip bootstrapを実行してからテスト依存を入れる。
- GitHub公式actionは確認したreleaseのcommit SHAに固定し、リポジトリ権限は読取りのみとする。

実機のMinecraft・音声・macOSアプリ、家庭用セットの配布はCIの対象外。既存の実機用opt-inテストも自動では有効にしない。

初回のCIでは、公開版に残るOSモデル補助で、OS起動から300秒未満だと未実行の初回probeまで再確認間隔の内側と判断していた。未確認状態を時刻0から`None`へ変更し、初回は必ずprobeし、二回目から間隔を適用するよう修正した。時刻0秒・42秒の回帰テストは修正前に失敗し、修正後に成功した。OSを偽装して回避する変更ではない。

書類側の開発mainではこのPython補助は撤去済みのため、公開版の補助とそのテストだけを修正し、開発mainへ再導入しない。CI設定とこの記録は両方へ反映する。

ローカルの公開版検証はPython 230件成功・11件skip・38 subtest成功、本棚JavaScript 76件成功。YAMLの起動条件がpushだけであること、shell構文も確認した。UbuntuでのRust全targetは初回CIで成功。反映前に更新ブランチ、反映後にmainへのpushでCIの完了を確認する。
