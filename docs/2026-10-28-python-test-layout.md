# Pythonテストの配置分離

本体起動用の `dogido-rust/scripts/` に混在していたPythonテスト74本とfixtureを `dogido-rust/tests/python/` へ移動した。共通の公開処理テストは `scripts/` から `tests/` へ移動した。

テスト内のimport、リポジトリ基準パス、模擬workerが参照する実行用補助、Rust単体テストのfixture参照、CIと文書のコマンドを新配置へ合わせた。テストの入力・期待値と本体の振る舞いは変更しない。移動前からの作業中変更も保持する。

Rustの単体テストは `#[cfg(test)]` の既存配置を維持し、通常ビルドには含まれない。今回の移動対象は実行スクリプトと混在していたPythonテスト群。
