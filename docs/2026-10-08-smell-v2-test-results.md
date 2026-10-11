# 2026-10-08 スメルバトルv2 検証結果

> この文書は**方位計算の撤去前**に実施した試験の記録。同日、この出力の確認後にユーザー方針で匂いの方位計算・送受信・返答を撤去した。以下の入力・出力・件数は当時のまま保持する。撤去後のテスト・ビルド・実モデル試験はコード確認待ちで未実施。現行仕様は[スメルバトル仕様](smell-policy.md)を参照。

ユーザーのコード確認後に実行。匂い・雨上がりの自動回帰テストは成功し、実モデル8条件も各1回ずつ完了した。数値の受け渡しは確認できたが、生成文の品質は全条件で合格とはいえない。

## 実モデル8条件

Javaの実 `SmellPolicy` で入力を作り、試験用JSONからRustの `game-events`、通常の環境反応を経て常駐モデルへ渡した。既存 `default_model` を使用。待受プロセスの起動引数では `isetnefret/Huihui-gemma-4-26B-A4B-it-qat-q4_0-unquantized-abliterated-mlx-4Bit`。max_tokens=72、temperature=0.65。モデル・プロンプトを変えず、各1回、再生成なし。

ゾンビ1・3・8は個体数や距離そのものではなく補正後のスメル値。温度補正0で距離8・6・1をJava規則へ与え、それぞれ1・3・8になったことを確認した。外部源の方向は東に揃えた。位置・距離・個体IDはモデル入力から除いている。

| 条件 | 実際の出力 | 生成時間 |
|---|---|---:|
| ゾンビ1 | 東の方から、なんかgetっとるような、嫌な匂いが漂ってきとるで。 | 1.677秒 |
| ゾンビ3 | 東の方から、ちょっと嫌な匂いが漂ってきとる気がするわ。 | 0.952秒 |
| ゾンビ8 | 東の方から、めっちゃ不快な匂いが漂ってきとるで。やばいな！ | 1.069秒 |
| パン3＋ゾンビ1 | お、パンのええ匂いがするな！東の方からゾンビも来とる気がするで。 | 1.296秒 |
| スズラン・シャクヤク・バラ各1 | 東の方から、お花がいっぱい咲いてる良い匂いがしてきたで！ | 1.058秒 |
| 手持ちパン3 | お、ええ匂いしてきたやん！パンの匂いがふわっと漂ってきたで。 | 0.987秒 |
| 無臭の石 | お、足元に石が転がっとるやんか。平原やけど石もあるんやな。 | 1.185秒 |
| 前回パン、今回未観測 | （silent・発話なし） | 0.739秒 |

7件はspeak、1件はsilent。全8件の応答形式は受理され、生成エラーやフォールバックはなかった。モデル生成の所要時間は合計8.963秒で、起動・イベント投入・模擬再生の時間は含まない。

### 内容の評価

- **ゾンビ1**: 数値1は到達したが、本文に英字getが混入。仄かな匂いの自然な表現としては未達。
- **ゾンビ3**: 不快な匂いと東の気配を表現。数・正確な距離・姿の断言はなし。
- **ゾンビ8**: 強度3より強い「めっちゃ不快」という表現。数・距離・姿の断言はなし。
- **パン3＋ゾンビ1**: パン3とゾンビ1は両方到達。パン優位には触れたが「ゾンビも来とる」は静的な匂いから動きを補った懸念。
- **スズラン・シャクヤク・バラ各1**: 3種類を保持して到達したが、匂いだけの入力から「いっぱい咲いてる」と量・景色を補った。
- **手持ちパン3**: パンの匂いを表現。手持ちに外部方向や数を付け足していない。
- **無臭の石**: 匂いは補わなかった。一方で、石の種類一覧から「足元」「転がっとる」を補作した。
- **前回パン、今回未観測**: 前回のパンの発話はcompleted履歴にあり、今回はsmell=unknown。silentを選び、前回の匂いを現在へ持ち越さなかった。

現在の匂いがある6条件では、送信した全成分と `effective_strength` が実モデル要求に一致した。石では `none`、前回だけ匂った場合は `unknown`。以前の発話から現在の匂いを補完していない。各条件1回なので自然さ・再現率・長期品質の保証にはしない。

[入力・実モデル要求・生の応答・評価のJSON](2026-10-08-smell-v2-results.json)に全8件を保存した。失敗文も修正せず記録している。

## 自動検証・ビルド

| 対象 | 最終結果 |
|---|---|
| Fabric `./gradlew --offline test build` | 90件成功。jarビルド成功 |
| Rust単体テスト | 487件成功 |
| Rust結合テスト（全16対象） | 120件成功、6件失敗。内訳は次節 |
| 環境反応HTTP `check_environment_reactions.py` | 22項目成功 |
| 環境runtime `check_environment_runtime.py` | 28項目成功 |
| 共有対話 `check_shared_dialogue.py` | 5項目成功 |
| 発話履歴 `check_spoken_history.py` | 5項目成功 |
| workshop所有権 `check_workshop_focus.py` | 8項目成功 |
| Rust debug / release build | 両方成功 |
| Clippy `--all-targets -- -D warnings` | 変更前から存在する5警告で不合格 |

匂いの混合・マスキング・コンポスター3・方角・旧入力拒否・手持ちの雨上がり受理、雨上がりの一度だけの注意、完了／沈黙／失敗／取消、古い窓ID、通常臭への即時復帰、Web・学習・workshopの所有権と警告優先を自動検査した。雨上がりの実モデル生成は今回の8条件に追加していない。

最初のRustコンパイルで、新しい `window_id: u64` に未対応のwire変換を指定していた不備を検出した。通常の正整数JSON受信へ修正し、負数・文字列・bool・小数を拒否する検査を追加した。今回追加した入れ子のifはClippyに合わせて整理した。共有文言・匂いがあるときだけ付く説明・旧匂い形式の期待値と、既存カタログの「香水瓶→ポーション瓶」の期待値も更新した。モデル出力を見てプロンプト・採否条件を調整していない。

### 結合テストの残り6件

- `chat_topics_projection_matches_fixture_including_none_and_observation_boundaries`
- `chat_topics_weak_terms_intent_and_policy_lines_match_fixture`
  - 通常会話policyの関西弁へ変更済みの文面と、旧文面の期待値が一致しない。
- `chat_world_catalogue_all_labels_and_raw_biome_metadata_match_canonical`
  - バイオームの `note`（例: 「影が多く、視線が遮られる。」）の有無が期待値と違う。
- `canonical_contexts_and_all_detail_projections`
- `canonical_persistent_preparation_traces`
  - 上記の森林note・source atom等が期待値と違う。
- `canonical_raw_catalog_entries_are_not_fixture_backed`
  - ポータルの `notes` の有無が期待値と違う。

これらは検査時の不一致として残した。今回の匂い回帰テスト成功と、リポジトリ全検査の成功を同じものとして扱わない。

### Clippyの残り5警告

- `dialogue/combat_runtime.rs::apply_combat_decision`: too_many_arguments
- `dialogue/story.rs::share_story` / `append_story`: too_many_arguments（2件）
- `environment/ambient.rs::still_applicable` の照明本数guard: collapsible_if
- `workshop_projection/mod.rs` の `unspoken_combat` 分岐: collapsible_if

該当3ファイルはHEADと同一、ambient内の該当ブロックもHEADから不変と確認した。今回の変更にある警告は修正済み。

## 確認範囲

TTS・playerは模擬。前回パンのケースでは、前回発話を模擬生成し、実際の本体台帳でcompletedにした後、今回だけ実モデルを呼んだ。実Minecraftの走査と送信、実マイク、VOICEVOX・スピーカーの品質、長時間の自然さは未確認。既存モデルサーバーの再起動・設定変更、Minecraftの再起動、利用中modsへのjar置換はしていない。
