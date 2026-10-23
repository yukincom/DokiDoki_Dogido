# GitHub Actions CI（2026-10-23）

同日の一時停止後、ユーザーの再開指示を受けてリポジトリのActionsを有効化した。既存のworkflowがなかったため、`.github/workflows/ci.yml`を追加した。

- 起動条件はブランチへの`push`だけ。定期実行・PRイベント・手動実行は設定しない。
- 同じブランチの古い実行は、新しいpushで取り消す。
- Ubuntu上でRustの全target、Python補助、本棚のJavaScriptテスト、導入用shellの構文を検査する。
- Rustは既存の`rust-toolchain.toml`と`Cargo.lock`を使用する。Pythonは専用venvを作り、既存のhash固定pip bootstrapを実行してからテスト依存を入れる。
- GitHub公式actionは確認したreleaseのcommit SHAに固定し、リポジトリ権限は読取りのみとする。

## 公開版を現行実装へ統合

セキュリティ更新だけを公開したPR #46では、開発mainのOSAI撤去が公開mainへ反映されていなかった。公開版も検証済みの開発mainへ揃え、[23分類BERT接続](2026-10-23-modernbert-23class-integration.md)・身体の注意範囲・[画像と共通観測の統合](2026-10-22-stop-vision.md)を反映した。未コミットの本棚作業は含めない。

Apple Foundation Models／Foundry LocalのSDK依存・設定・worker・Rust接続・chat分類fallbackと、それらを前提にした旧テストは撤去した。最初のCIで見つかった旧OSAI補助の時刻判定を修正して維持する方針は取りやめ、補助自体を削除した。現行テストは原文ごとのBERT分類fixture、状態・対象・原文・保存・再開条件をコードで検査する経路へ揃える。OSAIを実行するテストや、旧経路へ戻る互換処理は残さない。

## 検証範囲

統合後の公開用treeでRust全34 targetは874件成功・実音声1件skip、Python補助は196件成功・11件skip・22 subtest成功、本棚JavaScriptは76件成功。実装・テスト・設定が検証済みの開発mainと一致し、追加差分が依頼されたコメントとdocsだけであることも確認した。読取りレビューで旧経路の残留がないこと、BERT失敗時の非fallback・原文と版の検査・敵観測による再開の回帰テストを確認した。更新ブランチとmainへのpushでもCIを検証する。

実機のMinecraft・音声・macOSアプリ、家庭用セットの配布はCIの対象外。既存の実機用opt-inテストも自動では有効にしない。状況要約の保持案と実機での発話品質は、静止時の視覚仕様に記録した未実装・未確認の範囲を維持する。
