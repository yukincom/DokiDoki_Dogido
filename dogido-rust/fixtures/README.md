# Rust回帰データ

2026-10-03に旧Python本体と移植用oracle・fixture生成器の運用を終了しました。このディレクトリと `src/` 内のfixture JSONは、移植時に照合した入力・期待値を保持する回帰データです。JSON内の旧Pythonファイル名や `python` という検査名は当時の出典を示し、現在その処理を起動するものではありません。

現行の判断・prompt・検査はRustが正本です。仕様を変えるときは、その仕様から入力と期待値を個別に見直し、Rustから取得した出力で期待値を一括上書きしません。`src/` 内のtemplatesなどと、共有する `dogido_server/runtime_defaults.json` は現役の実装資料で、旧Pythonから再生成しません。共通の人格・状況文は `dogido_server/llm/companion_prompts.json` と `reaction_situations.json` を編集し、再ビルドして反映します。

検証手順は [Rust本体の案内](../README.md#検証)、廃止までの経緯は [移行記録](../../docs/rust-migration-plan.md) を参照してください。
