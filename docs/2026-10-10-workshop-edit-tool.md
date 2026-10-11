# 2026-10-10 川柳の変更依頼をnative toolへ接続

状態: native tool接続、旧JSON操作の撤去、送信案内の矛盾修正を実装。2026-10-11に関連単体・模擬通信が通過。実E4Bは検査tool往復に加え、案内修正後の編集・音数不合格3ケースを既存設定と公式推奨samplingの双方で確認。2026-10-11に稼働中本体へ反映済み。実Minecraftの接続・会話・字幕配送・音声再生完了を確認した。自動発句は2回とも音数不合格で、実機の編集tool試験には未到達。

小さい会話モデルが変更依頼を取り出し、Rustが行・元句・読み・音数・保存を扱う。E2B独立試験で追加した三行引数の補助を、既存Chat Completionsの `stage_player_edit` toolへ接続した。HTTPエンドポイントは追加していない。

## 処理

1. 現在の相談段階で編集が許されている場合、既存の `inspect_verse` と同じ `tools` に `stage_player_edit` を載せる。モデルは行ID、元の行、対象語、置換語、今回の原文根拠、確信度を返す。「冒頭の五音」等の呼び名を今回明示した場合は、任意の `line_reference_evidence` を既存の行概念検証へ通す。
2. `workshop/tools.rs` が関数名・一件だけの呼出し・引数の型を確認し、既存の `normalize_player_edit_tool` へ渡す。元句三行が返った場合は全行を照合する。置換結果も三行なら、変化が指定された一行だけであることを確認して取り出す。
3. 既存validatorで原文中の変更意思・置換語・確信度・否定／疑問／引用を検査する。照合済みの行番号を保持して `Engine::run` へ渡し、辞書の読み・厳密な5/7/5・他行不変・hard制約を検査する。
4. 検査成功時は既存 `Pending::stage` と `dialogue/workshop_edits` が現在句・版を再照合し、明示依頼の変更を一度保存して三行を表示する。成功の返事を作る追加推論は行わず、コードが確定した三行を返す。
5. 音数などの編集検査に落ちた場合は、同じtool call IDへ実際の失敗理由を返し、既存の `after_validation` で一度だけ相談を続ける。追加toolは `tool_choice=none`。元句・原文の照合で落ちた呼出しは、既存の一回再試行を会話だけに限定する。

一度に複数toolが返っても部分実行しない。保存・採用・終了の関数を汎用実行器へ渡さず、既存の状態機械と保存時のCASを維持する。本文JSONの `inspect` と `stage_player_edit` は、候補保持・検査・編集へ入る前に `native_tool_required` で棄却する。旧JSONをfunction callへ変換したり、API失敗時に旧形式で実行したりしない。形式訂正は同じtool APIへ一度だけ依頼し、再び旧形式なら句を変えず終了する。通常の会話、保持済み相談案の選択、案の採否、生成依頼、終了は未移行の現行操作であり、今回の二つのtoolの代替として扱わない。

## 確認項目

- native tool境界の単体確認: 三行照合後の行番号の保持、失敗結果のcall ID一致、複数呼出し・未知関数・過去の原文根拠の棄却。
- 本体の模擬通信: 一行の変更・三行置換から一行抽出・音数不合格・対象外の元句の食い違い・過去の根拠・複数呼出し。保存件数、他行不変、モデル呼出し回数も確認する。
- `inspect_verse` は三行に加えて既存の `inspect_text / inspect_line_index` も受け取り、語・候補の辞書検査を維持する。
- 本文JSONで編集・検査を二度返す模擬ケースでは、検査結果なし・候補保持なし・保存なしを確認する。
- 成功した一手の `transport=native_tool` と `tool_call.name/id`、旧形式拒否の `transport=content_json` と `native_tool_required` を記録する。
- promptから旧JSON操作例と許可actionを除き、参照fixtureと模擬LLMの送信形式を現行APIへ揃えた。領域処理の内部actionと検証器は共有し、旧モデル応答の受付には使わない。

ユーザーのテスト実行指示後、2026-10-11に関連試験を実施した。以下は再確認用コマンド。

```sh
./dogido-rust/cargo.sh test --locked workshop::tools
./dogido-rust/cargo.sh test --locked workshop_editing::tool_call
./dogido-rust/cargo.sh build --locked
dogido-llm/bin/python dogido-rust/scripts/check_workshop_edit_tool.py
dogido-llm/bin/python dogido-rust/scripts/check_workshop_inspection_tool.py
```

E4Bでの編集確認は、同じ模擬観測・初期句のまま、相談だけを稼働中の8080へ送れる。

```sh
dogido-llm/bin/python dogido-rust/scripts/check_workshop_edit_tool.py \
  --base-url http://127.0.0.1:8080/v1 \
  --output .dogido_tools/experiments/2026-10-10-gemma-e4b/edit-tool-result.json
```

これは編集toolとRustの接続試験であり、自動発句・実Minecraft・実マイク・実音声・16GB機の性能試験ではない。[E4Bの実機発句結果](2026-10-10-gemma-e4b-evaluation.md)で確認した生成失敗は、この接続だけでは解決済みとしない。この独立接続試験では稼働中の本体・モデルを更新や再起動していない。後続の反映・実機確認は末尾を参照。

## 稼働中の確認

2026-10-10のMinecraftでの相談時、5055の本体は編集tool接続前のreleaseだった。コードへの実装と稼働中プロセスへの反映を分けて扱う。この確認時点ではモデルも本体も停止・再起動していない。2026-10-11の反映結果は末尾を参照。

## 2026-10-11の検証結果

以下は送信案内を見直す前の初回結果。修正後の結果は末尾に分けて記録する。

| 確認 | 結果 |
|---|---|
| Rustのworkshop関連単体 | 83件通過。プロンプト参照fixture、原文根拠、CAS、音数、旧JSON拒否を含む |
| native編集toolの模擬通信 | 8件通過。正常編集、三行引数、音数不正、元句不一致、過去の根拠、複数呼出し、旧JSON二種の拒否 |
| 既存runtime / edits / conversation | 18 / 14 / 11件通過 |
| native検査toolの模擬通信 | 通過。2回のモデル通信で実測結果を返し、句を変更しない |
| 実Gemma 4 E4Bの検査tool | 通過。`inspect_verse` → Rustの読み・5/7/5・出典記録 → モデル返答、2回の推論 |
| 実Gemma 4 E4Bの編集tool | 不成立。`くろいをしろいに変えて` に別用途の `propose_revision` 本文JSONを2回返し、schema検査で棄却。native呼出し・変更・保存なし |

試験で、API移行時に自然な行呼称の根拠が落ちて既存の相談対象から移れなくなる回帰を修正した。任意の `line_reference_evidence` を現在原文・既存行概念検証へ渡し、明示した呼称のない行IDだけでは対象を変えない。旧JSONの編集受付は戻していない。会話テストの模擬応答と期待値も現行APIへ合わせた。

実モデル試験の初回は模擬ゲーム観測の更新不足により `stale_observation` で取り消された。試験側を0.25秒ごとの観測送信へ修正し、通常の鮮度判定を保ったまま再試験した。取消をモデルの不合格として数えない。編集試験のリクエスト、生応答、所要時間、Rustログは `.dogido_tools/experiments/2026-10-10-gemma-e4b/edit-tool-native-only-observed-20261011.json` に保存した。初回取消の記録も別ファイルのまま保持している。

### Google公式手順との照合

- [公式モデルカード](https://huggingface.co/google/gemma-4-E4B-it#best-practices) は `temperature=1.0 / top_p=0.95 / top_k=64` を推奨。今回の本体接続試験は既存の `temperature=0.25`、`top_p/top_k` 未指定で、稼働中MLXの既定は `1.0 / 0`。公式推奨条件での評価ではない。
- [公式function calling手順](https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4) はschemaを `apply_chat_template(..., tools=...)` へ渡す。稼働中モデルの配布templateとMLX実装を読み、実リクエストの2関数がGemma専用のtool declarationへ変換されることをオフラインで確認した。
- 公式例はassistantの `tool_calls` と `tool_responses` に結果オブジェクトを置く。使用中templateはOpenAI形式の `role=tool` にも対応し、同じmodel turnへ結合する。ただし現在のJSON文字列は文字列値として包まれ、公式例の構造化オブジェクトとは表示形が異なる。今回の編集失敗は結果を返す前の初手であり、この違いだけでは説明できない。Google例の引数オブジェクトをMLXのOpenAI互換APIへそのまま渡すと現行のJSON正規化と合わないため、外形だけ置換しない。
- 公式はThinkingによる関数呼出し精度向上を案内しているが、非Thinking例もある。今回の本体試験はThinking off・最大420 token。Thinking有効での比較は未実施。通常ターンの思考を次のユーザー発話へ持ち越さず、同一tool turn内では保持するのが公式の扱いで、使用中templateの `preserve_thinking=false` も現tool turn内の保持を満たす。

現MLXは `tool_choice=none` を処理していないため、追加呼出しの禁止はRust側の既存ガードが保証する。

初回時点では接続とモデル選択の成否を分けた。長い相談prompt内の案内、公式sampling、Thinkingを切り分ける前に、E4Bの能力限界とは判定しなかった。案内とsamplingの後続確認は以下を参照。Thinkingの比較は未実施。

### 2026-10-11 送信プロンプトの案内修正

実E4Bへ送信した生リクエストを再点検すると、編集toolの案内に加えて、`propose_revision` を広く「修正依頼」と説明する本文JSONの案内が残っていた。初手と再試行はいずれもこのactionを選んでいた。また、提案生成の `speech` を空にする指示と、後付けの会話プロトコルで操作actionにも返事を入れる指示が矛盾していた。接続の成功だけでは案内の整合性を保証できていなかった。

既存の案内を次のように修正した。本体の変更はプロンプト・toolの引数説明・参照fixtureで、新しい分類器・操作経路・旧JSONの救済処理は追加していない。試験scriptに実モデルのsamplingだけを指定する任意フラグを追加した。

- 本人が指定した新しい語への変更依頼は `stage_player_edit` のfunction call。
- 置換語を指定せず、新しい表現案の生成を頼む場合だけ `propose_revision` の本文JSON。
- 感想の相談、保持済み案の選択、採否、終了は既存の用途を維持する。
- function callは本文の返事を付けず、`propose_revision` の `speech` は空にする。後付けプロトコルも同じ条件に揃える。
- JSON側の一覧を「本文JSONで使えるaction」と明示し、形式再試行でも不足キーの補充より先に依頼と処理の対応を選び直す。

最初の案内修正後はnative toolを選んだが、原文にない一行全体を置換語にして棄却された。引数説明を明確にすると旧語・新語を正しく抽出した一方、原文evidenceへ元の句を入れて棄却された。これらを成功として数えず、両方の生リクエスト・応答を保存した。

最後に、`target_fragment` は旧語、`replacement_text` は本人が指定した新語、`evidence` は変更意思を含む今回発話からの引用、と引数ごとの入力元を明記した。行の組み立てはコードが行う。拒否後に編集権限が外れた試行と、tool実行済みの続きでは、利用できない操作を促さず会話へ戻す案内に揃えた。原文照合・音数・CASの検査は緩めていない。

### 案内修正後の再試験

関連単体83件を再実行して通過。編集tool模擬8件、検査模擬1件、会話模擬11件も通過した。本文JSONで編集・検査を返し続ける模擬ケースは引き続き実行・候補保持・保存を行わない。

実E4Bには同じ初期句・原文を使い、既存設定（temperature 0.25、top_p 1.0、top_k 0）と公式推奨sampling（1.0 / 0.95 / 64）で各ケースを一度ずつ送った。双方ともThinking off、最大420 token。

| ケース | 既存設定 | 公式推奨sampling |
|---|---|---|
| `くろいをしろいに変えて` | native編集1回、原文根拠・他行不変を確認しrevision 1件保存 | 同左 |
| `中七をしろいおのへとに変えて` | native編集1回、中七だけ変更してrevision 1件保存 | 同左 |
| `くろいをあおに変えて` | native編集の音数検査で棄却、tool結果から会話継続、revision 0件 | 同左 |

6ケースすべて期待どおり。部分変更の引数は `target_fragment=くろい / replacement_text=しろい / evidence=くろいをしろいに変えて` だった。各条件3ケースの一回ずつの確認であり、長時間の成功率・samplingの優劣を示す測定ではない。案内修正前後で既存設定を保った試験も通ったため、samplingを変えたことだけで成功した結果ではない。

記録は `.dogido_tools/experiments/2026-10-10-gemma-e4b/` 内の `edit-tool-prompt-evidence-existing-20261011.json`、`edit-tool-prompt-evidence-official-20261011.json`、`workshop-prompt-guidance-summary-20261011.json`。途中の不合格は `edit-tool-prompt-guidance-20261011.json` と `edit-tool-prompt-guidance-v2-20261011.json` に保持する。

この再試験では独立本体と実E4Bの推論を確認した。実Minecraft・実マイク・実TTS、16GB端末の性能は対象外で、稼働中サービスの再起動・モデル設定変更は行っていない。後続の反映・実機確認は以下に分ける。

公式推奨samplingで同じ一件を確認するコマンド:

```sh
dogido-llm/bin/python dogido-rust/scripts/check_workshop_edit_tool.py \
  --base-url http://127.0.0.1:8080/v1 --official-sampling \
  --output .dogido_tools/experiments/2026-10-10-gemma-e4b/new-result.json
```

## 2026-10-11 稼働中本体への反映と実Minecraft再試験

ユーザーの反映・実機試験指示を受け、検証済みソースでreleaseをビルドし、公式launcherの起動前確認を通して5055本体を更新した。実行ファイルのSHA-256は `a03798ab0d9cb1f0d3c1710d3b0b61b1056f799cd67d48f10073ba5245bccf72`。共有8080のE4B、VOICEVOX、マイクは再起動・設定変更していない。

実際のFabric観測で発句を待つため、本体の発句間隔だけ一時的に30秒とした。模擬観測・完成句の注入や検査条件の変更は行っていない。

| 実機確認 | 結果 |
|---|---|
| Minecraft再接続 | 新しい本体へ自動再接続し、新鮮なゲーム観測を継続受信 |
| 自動発句1回目 | 中七の音数超過・候補重複で再生成6回を使い切り、不成立 |
| 自動発句2回目 | ユーザーの再試行指示後も同様に不成立。どちらも句の保存・workshop開始なし |
| 通常設定への復帰 | 更新済み本体を通常の `interval_ms=600000`、`quiet_time_ms=30000` で起動。実引数と応答元PIDを照合 |
| 復帰後の会話 | `source=text` の既存入力APIへ挨拶を送信。実E4Bの返答、VOICEVOXの再生完了、Fabricの表示・配送ACK、Minecraftの `[CHAT]` 行を確認 |

発句が成立していないため、実Minecraft上の編集tool入力・変更・revision保存の確認には進めなかった。これは編集toolの成功／不成功を示す結果ではない。独立した初期句での6ケース成功と、実機の自動発句不成立を分けて扱う。実マイク入力、スピーカーの聴感、ゲーム画面の目視、16GB端末の性能は未確認。

記録は `.dogido_tools/experiments/2026-10-10-gemma-e4b/live-minecraft-edit-20261011/`。ビルドとプロセスの照合、2回の生成・再生ログ、Minecraftの該当チャット行、通常設定復帰後の会話と接続snapshotを保持する。
