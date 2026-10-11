# 2026-10-11 コミット整理前の全体検査

坑道らしいという推定を撤去する**前**のコードを対象に、未保存の本体・Fabric変更をレビューし、現在の不合格と確認済み範囲を確定した。復旧点の開始HEADは `0d99592`。常駐サーバー、モデル、VOICEVOX、Minecraftへの変更・再起動・Mod導入はしていない。

## 自動検査

| 検査 | 結果 |
|---|---|
| Rust `./cargo.sh test --all-targets --no-fail-fast --locked --offline` | 634成功・6失敗。lib 512、main 2、統合16対象 120成功・6失敗。example 13対象はコンパイルされ、テスト件数0 |
| Fabric `./gradlew --offline test build` | BUILD SUCCESSFUL。全10 taskはup-to-date、保存された21 test suiteは123成功・失敗0。今回123件を再実行したという意味ではない |
| Python `pytest tests/test_voice_echo.py -q -p no:cacheprovider` | 16成功、3 subtests成功 |
| Rust `./cargo.sh clippy --all-targets --locked --offline -- -D warnings` | 17指摘で不合格。実行時不具合の件数とは別 |

Rustの通常停止条件を外し、失敗したtargetの後ろも実行した。ただし各テスト関数内のfixture走査は最初のassert不一致で停止するため、その後の個別ケースを全件確認できた意味ではない。変更に関係するHTTP試験は直前の[JSON採用記録](2026-10-11-qat-json-adoption.md)等を参照し、この回では再実行していない。

## Rust統合テストの6不一致

| 対象 | 件数 | 失敗した比較 |
|---|---:|---|
| `chat_topics` | 2 | 正本の通常会話policyは関西弁へ変更済み、fixtureは旧文面。最初のnone文面とprojection case 0で不一致 |
| `chat_world` | 1 | 森林等12 biomeへ追加した「影が多く、視線が遮られる。」が旧期待値にない |
| `haiku_context` | 1 | context 301で森林注記がcatalog note・出典・source atomへ加わり、旧期待値と不一致 |
| `haiku_preparation` | 2 | portal 3種の役割注記と、emission 208の森林注記由来の出典・atomが旧期待値にない |

これらはモデルの応答成否や実Minecraftを試すテストではない。現行データと保存された期待値を直接比較している。既存文言・データの変更が不一致を説明するが、期待値の一括上書きは行わない。各変更の意図を確認したうえで対応する期待値を直し、途中で止まっていたケースを含めて再実行する必要がある。

## Clippyの17指摘

以前から記録されていた5件:

- `dialogue/combat_runtime.rs`: 引数過多1件。
- `dialogue/story.rs`: 引数過多2件。
- `environment/ambient.rs` と `workshop_projection/mod.rs`: 入れ子ifの整理各1件。

その後の文字配送変更等で加わった12件:

- `dialogue/update.rs`: 入れ子ifの整理1件。
- `dialogue/workshop_text.rs`: 一時変数の直後returnの整理1件。
- `dialogue/text_delivery/tests.rs`: 既定値作成後のfield代入1件、awaitをまたぐMutexGuardの検査2件、参照から一要素sliceを作る際の不要clone 6件。
- `server/tests.rs`: 既定値作成後のfield代入1件。

await関連2件はテストコードで明示的dropを記載しているが、Clippyが保持範囲を警告している。ロック区間をblockへ閉じるなど、意図を静的検査でも確認できる形への整理が必要。今回は指摘を抑制・修正せず、現在の残件として記録する。

## 実機確認と保存範囲

2026-10-11、ユーザーより「音声ON/OFFと文字入力」「実機で確認okでした」と報告。確認済みとして記録し、個別の試験操作・組合せは未記録のままにする。QATでの川柳検査・局所編集・保存を通す確認を、この報告だけで完了扱いにしない。

既存の本体・Fabric・関連自動試験と技術仕様を復旧点へ保存する。ライセンス、root README、ユーザーの文書推敲、別レビュー原稿は混ぜない。技術索引に混在するユーザー推敲は保持し、今回に関係するリンクだけを保存する。pushは行わない。

証跡の一時保存先: `dogido-checkpoint-20261011`（一時ディレクトリ）。`rust-tests.log`、`fabric-tests-build.log`、`voice-echo-tests.log`、`clippy.log`。恒久的な判断根拠は本記録と各日付付き変更記録へ残す。
