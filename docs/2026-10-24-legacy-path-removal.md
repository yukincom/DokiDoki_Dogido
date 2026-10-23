# 旧実装・後方互換経路の削除（2026-10-24）

実装・検証済み。10月23日から、本体・補助Python・Fabric・身体・本棚UI・配布設定・fixtureを確認し、使われない旧経路を削除した。変更は書類側のmainへ統合した。

## 削除したもの

| 範囲 | 削除内容 |
|---|---|
| 通常会話 | 旧planner本体、`plan-chat` CLI、比較スクリプト、Repair履歴、未使用の話題・名前分類、旧grounding用prompt項目。参照先を失ったfixture 10ファイル（13,540,793 bytes）も削除 |
| プレイヤー入力 | `meta.user_text` の送受信と、観測から入力を処理する分岐。入力は既存の `/api/v1/player-input` で受ける |
| 川柳相談 | `meter` の許可・feedback分岐、終了確認の状態とaction、`after_validation` 段階、旧戦闘／明示相談操作、emissionから相談材料を復元する分岐 |
| モブ観測 | 現在の `environment` の代わりに旧flat水・火フラグへ戻る分岐。戦闘側で現用するflat観測項目は別契約として維持 |
| 通信型 | `EventTime` のnaive／Unix時刻と混在時刻管理、数値文字列・bool→数値・0/1や文字列→boolの共通変換。現行のoffset付き日時・JSON型へ限定 |
| 起動・配布 | プロセス内MLXの設定、backend別名、使われないprovider・header・署名・音声単発設定。Rustが利用するChat Completionsとnoop、設定プロファイルを維持 |
| UI・接続部品 | 試作画面のlocalStorage／demo操作、使われない読み書込API、未使用Java constructorと入力Supplier |
| ローカル残骸 | 旧learnの空ディレクトリ、旧モジュール入りのbuild残骸、ソースを失ったpyc 151ファイル |

旧操作を成功させるfixtureは削除し、拒否を確認する回帰だけを残した。編集fixtureの残り1,682件は構造的に変更前と同一。検証fixtureの削除511件も旧操作472件・旧段階39件に限る。現在の回帰データは旧Python本体・oracleを復活させずに使用する。

通信型の変更前に、現行fixture 5,928イベントの対象値29,257個を型別に確認した。旧変換に依存する値は0件。Fabricの送信実装もJSON number／booleanとoffset付き日時を使用している。

## 現行仕様と照合して保持したもの

共通BERT 23分類、通常会話の一回生成、本人編集・未採用案・CAS・配送後の履歴確定、意味説明への相槌、相談のreading/source参照、自動発句の音数制約、詩知識資料、川柳lessonと保存済み記憶は維持する。任意の観測項目の欠損、辞書や音声機器の利用可否、現行の生成失敗を扱う処理も旧版への復帰経路とは区別する。

提供されたGrokレビューの音数schema・OS AI案内・旧learn残骸・BERT分類数の指摘を現物と照合して反映した。レビュー中のChrome MCPを保持する記述は、既に撤去済みの本体Web調査とは整合しないため復元していない。

AGENTS・現行仕様・接続仕様・起動案内・共同編集文書を更新した。過去の検証結果は書き換えず、廃止済みの手順は該当節でも履歴と明記する。

ローカルの `.env`／`.env.shared` も廃止キーを整理し、既存のChat Completions接続設定で起動前検査を確認した。設定ファイルや認証値はGitへ含めない。

## 検証

以下は統合後のmain作業ツリーでの結果。以前から存在した本棚の別作業差分を含むため、件数は今回のコミット単独の結果とは区別する。

| 確認 | 結果 |
|---|---|
| Rust `test --all-targets --locked` | 853 passed / 1 ignored / 0 failed |
| Python補助・起動・配布 | 203 passed / 11 skipped / 22 subtests passed |
| 本棚UIのNodeテスト | 83 passed |
| Fabric Gradle test/build（offline） | 286 tests、成功 |
| 身体Gradle test/build（offline） | 166 tests、成功 |
| 模擬HTTP | 通常会話8、対話18、環境反応22、身体依頼6、workshop focus10。環境runtimeと共同編集6スクリプトも成功 |
| 通信型変更後の模擬HTTP | 対話18・身体依頼6を再確認 |
| release build・起動設定 | 成功。server／voiceの `launch_dialogue.py --check` を確認 |
| 配布wheel | 新規構築したwheelのPython 16ファイルに旧learn／OS AIモジュールなし |

実モデル生成・Minecraft・実音声の再検証と、常駐サーバーの再起動は行っていない。既存の本棚作業とレビュー原稿は今回のコミットへ混ぜない。
