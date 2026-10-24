# ModernBERT 23分類をドギド本体へ接続

2026-10-23。ユーザー指定により、890件で学習した元のseed42を採用する。層合成版は使わない。

## 採用モデル

- 本体の保管先: `models/dogido-request-v7-seed42`（2026-10-24移設）
- 採用記録: 同フォルダの `adopted-model.json`。研究資料は[知りたがり屋](https://github.com/yukincom/AI-Curious-Community/tree/main/modernbert-request-classifier-study)に保存。
- 重み: `models/dogido-request-v7-seed42/model.safetensors`。研究時の元checkpointは `artifacts-v7-pilot890/weights/trial-1/best/`。
- `model.safetensors` SHA-256: `8645b0c742c9f71df13e940e252619648df04b1243644c4a705b5d20a3256bbd`
- 入力: 現在発話だけ、履歴0件、最大128トークン、切り詰めなし
- 旧v1.9 seed43の採用記録は `adopted-model-v19.json` に保持する。

元の重み・学習データ・比較結果は変更しない。独立した保護コピーもモデル保管側へ残す。先行評価ではseed42のtestは72/78件だったが、この値をMinecraftでの成功率とは扱わない。

## 分類の接続先

| ID | ラベル | 既存処理への接続 |
|---|---|---|
| 0 | `not_target` | 通常会話、知識質問など従来の受付 |
| 1 | `enemy_information` | 現在見えているモブの情報。友好・敵対の両方 |
| 2 | `equip_sword` | 現在のhotbarを照合する剣支援 |
| 3 | `recall_mount` | Minecraftから取得した乗用動物候補の選択と呼び戻し |
| 4 | `dogido_recall` | 身体Modの既存の呼び出し処理 |
| 5–7 | `senryu_line_upper/middle/lower` | 相談する行だけ固定し、質問・感想・提案は既存の相談へ渡す |
| 8–10 | `senryu_change_upper/middle/lower` | 対象行を固定し、原文の置換語を既存編集へ渡す |
| 11 | `look_direction` | プレイヤーが現在向いている方角へ視線を向ける |
| 12 | `senryu_end` | 現在の川柳相談を終了。未採用案の採否は別に扱う |
| 13 | `look_relative_right` | ドギド自身の右 |
| 14 | `look_target` | プレイヤーの現在の照準先 |
| 15 | `look_player` | プレイヤーを見る |
| 16–22 | `look_relative_front/back/left/front_left/front_right/back_left/back_right` | ドギド自身の現在の向きを基準にした残り7方向 |

共通受付の結果は原文・入力元・入力世代に結び付け、内部再送や再生待ちにも引き継ぐ。既存Python workerを23ラベルへ更新し、Rustで要求ID・採用記録のhash・23確率・argmax・入力上限を照合する。別の分類器やconfidence閾値は増設しない。

### 川柳

行ラベルには意味質問や感想も含まれるため、行指定だけで修正を始めない。具体変更は原文から旧語・新語を取れる場合には既存のコード抽出を使い、それ以外は既存の限定抽出を一度使う。現在句・版・原文の語句・保存整合性は既存処理で照合する。

終了時に未採用案がなければ直接終了する。未採用案がある場合は、同じ発話での採用／破棄を勝手に補わず、既存の採否と終了のtransactionへ渡す。戦闘中断中も終了ラベルを使う。再開・継続は敵の現在観測と警戒解除条件でコードが決める。敵が残る間は原文・句・版・入力世代を保持し、解除後に同じ受付へ戻す。保持入力がなければ三行を再掲して通常相談へ戻る。

### OS AIと旧判定の撤去

Apple Foundation Models／Foundry Localの専用worker・設定・SDK依存・chat分類fallbackを撤去した。意図はBERT、対象と原文語句・現在状態・実行結果はコードが所有する。再開にAIの同意判定は不要なので、再開／辞退actionと確認段階、敵が残る間の暫定復帰も削除した。`not_target`を安全や再開意思の証拠には使わない。

接続箇所には「BERTへ変更済み。旧判定へ戻さない。」、再開箇所には「再開・継続は敵の現在観測と警戒解除条件でコードが決定できるため、AI分類を撤去。旧判定へ戻さない。」を記した。旧5分類のラベル・語句による操作入口・OS AIへのfallbackは実行経路に残さない。

### モブ・乗用動物

`enemy_information`の名前は学習時のIDを維持するが、友好モブも対象にする。回答の材料は現在のドギド視点と正本カタログの呼称で、視点を使えない場合にプレイヤー視点へ代用しない。特定の種類を尋ねられたとき、別の最寄りモブで埋め合わせない。

`recall_mount`は猫など実行対象外の動物への依頼も受け取る。候補選択へ呼び戻し可能な14種と現在候補を渡し、対象外なら呼べない旨を返す。対象外の依頼を勝手に馬へ置き換えない。所有者、装備、最新の個体、期限、入力世代、実行先の安全条件は従来どおりコードが照合する。

### 身体

相対方向はドギドの向きが基準で、プレイヤーの向きとは別である。視線や呼び出しは同じMinecraft接続・world世代・command IDと結果ACKに結び付ける。受付やqueueへの投入だけでは実行成功として記録しない。

## 今回追加した検査と既存検査の更新

| 区分 | 内容 |
|---|---|
| 新しい実行検査 | 身体の呼び出し・視線のHTTP命令を、capability・所有者・次元・身体ID/epoch・入力世代・command ID・5秒の受付期限へ結び付ける。結果は35秒まで待ち、実到着／向き適用のACKだけを成功とする。再配送は同じIDで重複実行しない。 |
| 既存の仕組みを再利用 | 身体Modの所有者確認・制御優先・位置や視線の適用、川柳の原文語句・現在句・版・保存CAS、剣のhotbar照合、乗用動物の候補と着地点の確認。 |
| 既存検査の更新 | BERT workerの5ラベル契約を23ラベルへ更新。採用hash、原文・入力元・世代、確率の個数・argmax、入力上限の確認は継続。旧ラベルの互換受付はしない。 |
| 新しい回帰テスト | 身体12モード、相対方向、命令／結果の対応・期限・重複・接続変更。戦闘保留の再受付が満杯でも保持すること、同じIDで再接続しても旧入力が混ざらないこと、FIFOの予約枠。 |
| 既存テストの更新 | OS AIの模擬分類を削除し、原文ごとにBERTラベルを明示するfixtureへ変更。終了・行指定・具体変更・モブ・剣・乗用動物をその分類後の処理として検査する。再開は敵消失と警戒解除の観測で検査する。 |

川柳の意味・自然さ・音数・品質を理由に拒否する検査は追加していない。BERTの行指定だけで修正意思を補わない。意図を語句や別のモデルで再審査する旧経路を削除した。模擬BERTのラベル表は試験の入力条件であり、本体のキーワード判定器ではない。

## 起動と確認範囲

既存の `DOGIDO_REQUEST_CLASSIFIER_MODEL_ROOT` と分類用Pythonの設定を使う。一般起動は従来の `python dogido-rust/scripts/launch_dialogue.py --settings-dir .`。採用記録の参照先を切り替え、新しいRust本体と対応する身体Mod・Fabricアダプターを揃えてから起動する。

### 自動検証・配備結果

書類フォルダのmainへ統合し、採用記録を元seed42へ切り替えた。

| 確認 | 結果 |
|---|---|
| Rust `test --locked --offline --all-targets --no-fail-fast` | 873 passed、0 failed、1 ignored（34 test targets） |
| Python `python3 -m pytest tests dogido-rust/scripts -q` | 173 passed、11 skipped、22 subtests passed |
| Fabric adapter `./gradlew --offline test build` | 286 tests、失敗0、ビルド成功 |
| 身体Mod `./gradlew --offline test build` | 166 tests、失敗0、ビルド成功 |
| Rust本体の模擬HTTP通信 | 川柳・戦闘・知識・乗用動物・身体命令など22スクリプトが通過（fixture修正後の再実行を含む） |
| 実seed42のCPU worker | 保存重みを読み込み、剣・ドギド呼び出し・上五の意味質問・川柳終了・右向きの5文を対応ラベルへ分類 |
| 一般起動 | releaseビルド、`launch_dialogue.py --settings-dir . --check`成功 |
| 保存モデル | 原本4ファイルと独立コピー4ファイルのhash一致、旧採用記録のbyte一致 |

模擬HTTPはBERTラベルを原文ごとに固定し、その後の配線・取消・保存・結果確認を検査する。実モデルの分類精度を測る試験ではない。旧OS AIの模擬応答・再開同意・暫定復帰の期待値は削除した。直接編集の記録名は本体の`modernbert_verbatim`へ揃え、原文どおりの編集・保存1回・LLM呼出し0回を確認した。今回の検証で見つかった末尾句読点の欠落も、表記を保持して読みと区別するよう修正した。

Minecraftが起動していないことを確認し、普段の`minecraft/mods`へ以下を配置した。旧2本は`.dogido_tools/checkpoints/2026-10-23-bert23-integration/mods-before/`へ退避した。

| 配布物 | SHA-256 |
|---|---|
| `cube-dogido-lab-0.7.11.jar` / Body API v11 | `3c3f1096dcb43be59ee05302ec66f2f3483fadcf63e8875b7d111b4896d93b1f` |
| `dogido-fabric-client-0.1.0.jar` / build `0.1.0+20261023bert23` | `b14f7b86f18605665ad93851d02b73bf4c89f70dd92f53119b966d7368b47acb` |

検証ログ・機械可読の結果は`.dogido_tools/checkpoints/2026-10-23-bert23-integration/validation.json`、導入先と退避先は同じフォルダの`installed.json`に保存した。Minecraft上の動作・音声・実モデルによる相談の通し確認は未実施。Minecraft・本体サーバー・マイクは起動していない。

旧5分類の経過は [2026-10-15の接続記録](2026-10-15-modernbert-integration-preparation.md) に保持する。
