# 川柳ワークショップ：OSSの設計を取り入れる比較試験

## 現在の採用範囲（2026-09-28）

ここで比較する複数案は、開発側の**改善手法の候補**。プレイヤーに複数の句案を出させる設計とは別に扱う。
本体の対象は「プレイヤーの一案を、現在句の指定箇所だけへ差し替える」こと。
複数案の保管・ID選択・順位参照は導入しない。

コードを確認して取り入れたのは、一案の対象・差し替え表現・検査状態の分離と、訂正で指定された項目だけ更新する方法。
TypeAgentの候補ID返却、Framesの複数frame管理、Trustcallの汎用JSON Patch、OpenDFの汎用対話グラフは導入しない。
以下の比較表と実験は採用判断の記録であり、本体の機能一覧ではない。
[切り出した範囲と実モデル検証](evaluations/workshop-player-edit-20260928.md)。

更新: 2026-09-27。基準コミット: `cf689fa`（会話中の差し替え案の保持）。

目的は、相談中の案・編集対象・採否を取り違えず、発話を増やさずに共同編集を続けること。
比較は一つずつ行い、合格した段階ごとにGitへ保存する。

## 現在あるもの

- `43abc99`: 部分一致した対象箇所だけを差し替える経路。
- `cf689fa`: 会話中の差し替え案を、実際の未採用編集とは別に保持する経路。
- 正本の版、対象行、置換前の文字列の一致、音数、hard制約、採否・保存、戦闘中断は既存コードが管理する。
- 現在の会話中の案は一件で、`Pending`の検査に通るものだけ。短期履歴や句の版に依存して失効する。

既存の部分置換・CAS・戦闘pauseを再実装する計画ではない。以下は追加効果を測る順番。

## 試す順番

| 順 | 参考にする実装・研究 | 取り入れる案と実装箇所 | 試す会話・採用条件 | 状態 |
|---|---|---|---|---|
| 共通 | Microsoft evolving-intent | 意図の変更を含む会話ごとに、期待する対象・行為・正本の状態を定義する。LLMの自己採点だけに頼らない。既存のworkshop試験を拡張する | 案A→案B→最初の案、否定、伝聞、曖昧な指示、脱線、終了、古い句への参照。各実装で同じ系列を比較する | 第一試験16例に加え、独立した候補保持の連続試験を実施。本体の状態遷移は未検証 |
| 1 | TypeAgentのentity ID参照とclarify分類 | 候補に安定IDを付け、会話stepから対象IDと行為を返す。参照不明・対象の省略・行為不明を区別して聞き返す。まず独立試験、その後`workshop_helper.py`と`dialogue/workshop_runtime.rs`へ限定接続 | 「最初の案」「下に使う方」「それ」の参照。存在しないIDだけでなく、存在する別案の誤選択も失敗に数える。既存stepに統合し、平常時のモデル呼び出し回数を増やさない | IDだけの第一試験を実測済み。意味理解の改善は確認できず、本体採用は保留 |
| 2 | Framesの複数frame保持 | `workshop_candidate.rs`の一件を、複数の相談案へ拡張する。ID、対象行、対象箇所、差し替え案、発話者、出所turn、基準となる句の版、検査結果を別々に保持。相談案→未採用編集→正本を分ける | 字余りの案も相談対象として残す。後から「最初の方」を選べる。句の版が変われば適用可否を再検査する。プレイヤーの案は返答音声の中断で消さず、ドギドの案は再生済みだけを共有済みとする | 独立試験で完走0/4→2/4。本体未導入。参照不明・出力契約の失敗を残す |
| 3 | Trustcallの既存データに対する差分生成 | 全三行を書き直さず、対象行・置換前の文字列・置換後の文字列を抽出する。既存`workshop_edit.rs`、`dialogue/workshop_edits.rs`と検査結果を再利用。失敗時は対象の決定と案の内容を分けて保持し、必要箇所だけ言い直す | 「さくら、を、もみじに」のような部分編集で他の文字を保持する。古い版、重複一致、別の行、生成した語へのすり替えを拒否。すでに既存経路が満たす部分は追加しない | 独立試験で優位は確認できず。本体未導入。編集指示への訂正の取り違えが課題 |
| 4 | OpenDFのside task / revise | 「今何を相談しているか」を小さな状態として保持し、意味質問などの寄り道の後に戻す。`workshop_followup.rs`と既存の戦闘中断状態を接続する。汎用グラフ実行器は入れない | 「この語の意味は？」→説明→「なら最初の案で」。終わったworkshopを敵出現後に再開しない。既存のpause/resumeで通るケースは変更しない | 独立試験で正解3/6→4/6。本体未導入。意味への相槌と採用の区別が残る |

順位は導入規模と対象問題への近さによる仮説。結果が悪ければ採用せず、次の案へ進む。
BERT/Jevを挟むのは、参照・相談・適用の区別と評価例が固まってから検討する。
まず同じLLMで状態表現の効果を分離し、新モデルの常駐や追加判定は増やさない。

## 第一試験：ID参照と文字列参照

実行器: `dogido-rust/scripts/compare_workshop_references.py`。
採点器の確認: `dogido-rust/scripts/test_workshop_references.py`。
実応答の記録: [workshop-reference-20260927.jsonl](evaluations/workshop-reference-20260927.jsonl)。

同じ手作りの候補一覧と再生完了済みの会話を両方式へ渡した。
文字列方式は行IDと差し替え案の原文、ID方式は候補IDを返す。
会話からの候補抽出・候補の自動蓄積・本体workshopとの比較は行っていない。
意味解析の独立試験であり、現行本体の精度を表す値ではない。

- 16例×2方式、順番を固定乱数で混ぜ、各一回、追加再試行なし。
- 指定モデルと全応答の`model`は `mlx-community/Qwen3.6-35B-A3B-4bit-DWQ`。
- `temperature=0`、`max_tokens=160`、`enable_thinking=false`。全32応答は`finish_reason=stop`。
- 既存8080へ接続。サーバー起動・停止、Minecraft、音声、正本の変更は行っていない。
- 正解は行為と参照先の完全一致。JSON外形・原文evidence・参照先もコード検査する。
- 「誤った差し替え指示」は、不適切な`stage`出力を数える。原文evidenceが不正で検査に落ちた一件も含む。実編集はゼロ。

| 方式 | 正解 | 誤った差し替え指示 | 出力契約不正 | 応答時間の中央値 | 出力トークン合計 |
|---|---:|---:|---:|---:|---:|
| 文字列＋行ID | 11/16 | 4 | 1 | 1,030ms | 887 |
| 候補ID | 9/16 | 5 | 0 | 791ms | 508 |

ID方式では今回の出力は短かったが、精度の改善は確認できなかった。
存在しない案、曖昧な「それ」、同文言の別行候補、伝聞で誤った`stage`が出た。
さらにID方式では「最後に私が出した案」を最初の候補へ結びつけた。
比較と説明の取り違えも厳密採点では不正解としているが、変更意思の誤認とは分けて扱う。

結論: IDは参照先を短く表す手段として残し、理解力が上がると仮定しない。
次はTypeAgentのclarify分類も参考に、今回発話の行為と参照根拠を分け、未解決なら聞き返す設計を同じstep内で比較する。
IDが実在するだけでは採用しない。既存の原文・否定・条件・引用・伝聞検査も維持する。

16例の一回測定なので速度差の一般化やp95の主張はしない。キャッシュ・実行順・出力長にも影響される。
次の比較では別の句・候補順の入れ替え・未使用の言い回しを加え、今回の16例への当てはまりと区別する。

## 各段階の検証とGit

1. 変更前のコミットと試験条件を固定する。
2. 関連するRust/Python検査と、同じ入力の実モデル比較を行う。
3. 意図しない変更・採用・保存が評価例で出ないこと、他行・他の文字列が保持されることを確認する。
4. 通常ケースの成功率、聞き返し率、呼び出し回数、入力/出力トークン、応答時間を比較する。最終的に実Minecraft・音声でも確認する。
5. 当該段階の差分だけをレビューしてローカルコミット。意味理解が改善しなければ結果だけ保存し、本体へ接続しない。

当初の成果物は計画・独立試験・採点器・応答記録。2026-09-28に一案の対象保持・部分置換のみRust経路へ組み込み、模擬環境と実モデルで確認。Minecraft・マイクでの実機確認は未実施。

第二試験では複数案保持・差分修正・相談復帰について計52回の実モデル生成を行った。
[第二試験の結果と限界](evaluations/workshop-designs-20260927.md)を参照。当時は複数案保持→本題の問いの保持→編集指示への訂正を試験候補としたが、この本体導入順は撤回。一案の部分置換と訂正へ対象を絞った。

## 参照したOSSと範囲

星数は2026-09-27確認。今回の候補はすべて10超。ただし星数を品質の保証には使わない。
Tether MDは採用対象外。本試験は設計を参考にした独立実装で、以下のコードや学習データをコピーしていない。

| 参照先 | 星 | 読んだ実装・利用上の限界 |
|---|---:|---|
| [TypeAgent](https://github.com/microsoft/TypeAgent) | 743 | [`chatHistoryPrompt.ts`](https://github.com/microsoft/TypeAgent/blob/75a78d13efed5bc40754edd57c8c4d7ac9174a78/ts/packages/dispatcher/dispatcher/src/context/chatHistoryPrompt.ts)のentity参照と、同ディレクトリ以下`dispatcher/schema/clarifyActionSchema.ts`の不明点分類。MIT。英語中心のサンプルで、日本語の精度を保証しない |
| [Frames](https://github.com/Maluuba/frames) | 76 | [`frames/utils.py`](https://github.com/Maluuba/frames/blob/20c00247d0975181fae381756503c391a802ea1c/frames/utils.py)と評価試験、[Frames論文](https://arxiv.org/abs/1704.00057)。公開repoはデータ取扱い・評価用で、複数案追跡モデルの完成実装ではない。archived。コードのLICENSE.txtはMIT。データを導入する場合は別途条件確認 |
| [Trustcall](https://github.com/hinthornw/trustcall) | 1,092 | [`_base.py`](https://github.com/hinthornw/trustcall/blob/8c7312b591542cf9349ce00d2811e890ea6eef91/trustcall/_base.py)の`_ExtractUpdates`/`PatchDoc`。MIT。差分生成を参考にするが、原文一致や編集意思の検証はドギド側に必要 |
| [OpenDF](https://github.com/telepathylabsai/OpenDF) | 24 | [`framework_functions.py`](https://github.com/telepathylabsai/OpenDF/blob/f156665f776a82e984517b615dee438181b69c7d/opendf/graph/nodes/framework_functions.py)の`side_task`/`revise`。MIT。候補が複数なら先頭を返す参照経路は取り入れない。自然な日本語会話を完成させるパッケージではない |
| [evolving-intent](https://github.com/microsoft/evolving-intent) | 29 | [`user_intent.py`](https://github.com/microsoft/evolving-intent/blob/993d6be9597ac03854b46362ccd647eb1bfd267a/situated_simulation/user_intent.py)の意図変更表現。MIT。評価系列の考え方を使い、リアルタイム経路には追加しない |

再実行例（既存のローカル推論サーバーと出力先を指定）:

```bash
python3 -m unittest discover -s dogido-rust/scripts -p test_workshop_references.py -v
python3 dogido-rust/scripts/compare_workshop_references.py \
  --base-url http://127.0.0.1:8080/v1 \
  --model mlx-community/Qwen3.6-35B-A3B-4bit-DWQ \
  --output logs/workshop-reference-new.jsonl
```

出力先の親ディレクトリは事前に作る。既存の記録は上書きしない。モデル応答の`model`も記録する。
