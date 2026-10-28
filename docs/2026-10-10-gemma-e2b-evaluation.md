# 2026-10-10 Gemma 4 E2B 初回評価

**2026-10-28の後継方針:** 本文の川柳WSに関する原文evidence・confidence・語形・音数・材料条件による拒否、原文からの直接編集、旧 `workshop_validation` の記述は当時の記録。現行はBERT・LLMの解釈を `workshop_response` で受け取り、対象・現在句・版・取消・保存整合性を扱う。[モデル出力の受信と適用](rust-workshop-validation.md)を正とし、旧検査を復元しない。実ログ・試験結果は当時の条件のまま保持し、今回の変更の確認済みへ読み替えない。自動発句・assist・戦闘の契約はこの更新の対象外。

## 判断

`lmstudio-community/gemma-4-E2B-it-MLX-4bit` は起動・通常会話の返答を確認した。現行のまま全機能のメインモデルへ採用する判断は保留する。独立したnative tool calling試験では操作の選択が改善したが、編集引数は不正であり、この測定時点では編集toolを本体へ組み込んでいなかった。後続作業で確認用toolを接続し、さらに[編集toolの本体接続](2026-10-10-workshop-edit-tool.md)を実装した（後続の2026-10-11に関連単体・模擬通信を確認。実E4Bの検査toolは成功。編集toolは初回不成立だったが、案内修正後の部分変更・一行変更・音数不合格は既存設定と公式推奨samplingの双方で確認）。検証範囲と経緯は末尾に記録する。追加学習の前に、入力形式を揃えた比較を行う価値がある。

初回評価では本体ソース、設定、モデル起動方法は変更していない。ユーザーが8080で起動したモデルへ `model=default_model` で接続し、試験終了後も同じPIDの稼働を確認した。試験用ドギドと子プロセスは既存fixtureで終了した。後からユーザーが起動した通常5055の本体には介入していない。後続の引数補助は末尾に区別して記録する。

## 測定条件

- 配布revision: `31512dab743c73974a99795270031a6f85072d94`。モデル名は起動引数と起動ログで確認。`/v1/models` の一覧だけを実モデルの証明にしない。
- 既存MLX runtime: `mlx-lm 0.31.3` / MLX `0.31.2`。起動時thinking off。既存のモデル対応修正は変更していない。
- Rust release SHA-256: `92ba1be183c0d0279bf8e87e4acdc37d0e9a4007fb94d2665425c59ec399478a`。
- 通常会話: 現行prompt・temperature 0.65・max_tokens 72。テキスト入力、表示ACK、TTSなし。
- 既存workshop: 現行prompt・temperature 0.25・max_tokens 420。初期句と観測、TTSと再生は模擬。実モデルの返答を加工せずRustへ渡す。
- 自動川柳: scene/draft/grounding/regenerationのモデル呼出しはすべて実モデル。観測と音声は模擬。structured 192、grounding 512、最大再生成6回。試験専用記憶。
- 実Minecraft・マイク・TTS・展示会端末の性能は、この測定の対象外。共有8080を使用しており専用機の性能試験ではない。

## 現行入力の結果

| 対象 | 結果 |
|---|---|
| 通常会話6発言 | 6/6で表示ACKまで完了。表示完了まで596〜691ms、中央値618.5ms。挨拶・散歩への返答は自然。緊張の意味の訂正と感謝には直前の励ましを繰り返した。配送完了を会話品質6/6とは数えない。 |
| 既存の局所編集3シナリオ・計7発言 | シナリオ成功0/3。直接の置換、相談案への同意、置換語の訂正でいずれも `respond` を選び、句は変化しなかった。初期句保存1件を編集成功と数えない。 |
| 意味説明・音数確認 | 説明/検査へ進まず、どこを変えたいかという返事になった。 |
| 引用された「終了」 | openを維持。ただし入力への適切な応答ではなく、一般的な変更箇所の聞き返しだった。 |
| 「今日は句の相談をここでおしまいにしよう」 | LLM呼出し前に既存の句の想起へ入り、openを維持。これはモデルの終了選択能力の失敗件数へ含めない。 |
| editorを含む既存修正試験 | pending案が作られず停止。editor/checker/採用の実モデル経路には到達していない。 |
| 前回の失敗語句「光かかして」→「光かざして」 | `respond` で返答し、正本は古いまま。revision 0件。 |
| 自動川柳1場面 | 15回のモデル呼出し、約16.1秒。6回の再生成後も完成せず保存なし。出力例は「さくらのはなや」「ふくろうのほこり」「あさのひにそと」。音数不一致の修正が収束しない。sceneの補助出力1回は192tokenで打ち切り。 |

既存検証スクリプトの終了コードだけで合格判定しない。`check_workshop_edit_live.py` はシナリオ不成立でも正常終了するため、各 `passed` を読む。

## メッセージの並びの診断

3入力（部分編集・意味説明・新語への変更）を、temperature 0に揃えて、元の形・連続userの結合・現在文脈を含むuserメッセージを最後へ移動、の3条件で比較した。各条件は同じテキストを保持し、本体には反映していない。

意味説明は文脈を最後へ移すと `explain` になった。部分編集は別actionへ進んだが、行番号と置換語が不正。新語変更は説明へ逸れた。並びの影響は見られるが、これだけでは編集を成立させられなかった。

## Native tool callingの独立試験

[Google公式のfunction calling形式](https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4)に合わせ、Chat Completionsの `tools` に関数名・説明・引数schemaを渡した。配布chat templateと既存MLXのGemma4 tool parserを使用した。関数の実行器は接続せず、返された関数名・引数を記録するだけの試験。

公式推奨のtemperature 1.0 / top_p 0.95 / top_k 64、thinking off、max_tokens 420。本体の長いpromptより短い入力であり、samplingも異なるため、「toolsだけで性能が上がった」という因果比較ではない。

| 入力 | 関数の選択 | 引数の検査 |
|---|---|---|
| 公式最小例のLondon気温 | `get_current_temperature` | location=Londonで一致 |
| くろいをしろいに変えて | `stage_player_edit` | 行2・新語・対象語・今回根拠は一致。ただしexpected_textへ三行全体を入れたため不正 |
| 光かざしてへ変更 | `stage_player_edit` | 行2・新語は一致。ただしexpected_textが三行全体、evidenceが過去の相談案の説明であり不正 |
| 音数を確認して | `inspect_verse` | checks=[meter]で一致 |
| 「終了」と言ったが相談は続けたい | 呼出しなし | 終了を誤って選ばない |
| ここで句の相談を終わりにしよう | `close_workshop` | 今回の終了発話をevidenceにした |

関数の選択は6/6で期待と一致したが、編集2件の完全な引数検査は0/2。0.285〜1.167秒で応答。これは独立した最小試験であり、workshop成功率や本体の安全な採用・保存成功率ではない。

## 次の比較

同じ情報・schema・sampling・評価ケースを用い、短い独自JSON形式とnative tools形式を比較する。小さいモデル向けに、現在句・今回入力・過去候補を区別して渡す。返答は既存の原文根拠・行対象・CAS・音数・保存検査へ戻す。モデルへ直接の保存権限や世界操作を与えない。残る失敗をデータ化してからLoRAの要否を判断する。

## 記録と再現

生request/response、結果、実行用スクリプト、参照ソースのハッシュは、ローカルの `.dogido_tools/experiments/2026-10-10-gemma-e2b/` に保存。既存スクリプト3本は `DOGIDO_RUST_BINARY` にreleaseを指定し、`--base-url http://127.0.0.1:8080/v1` で実行した。モデル指定があるスクリプトでは `--model default_model` を使用した。

追加の `measure.py` / `prompt_order.py` / `native_tools.py` は測定用。既存出力の上書きを避けるため保存は排他的作成。本体へ新しい経路は追加していない。プロンプト並びとnative toolsの実験は製品変更ではなく、採用前の調査記録として保持する。

## 後続: 三行で返った編集引数の補助（初回実装時の記録）

`workshop_editing/tool_call.rs` に、native tool試験の編集引数を既存Rust検証へ渡す補助を追加した。追加当初は通常workshopのモデル呼出しへ接続していなかった。後続の[編集tool接続](2026-10-10-workshop-edit-tool.md)でこの補助を本体から使う実装を追加し、2026-10-11に独立本体と実E4Bで確認した。この独立試験時点では稼働中本体への反映は未実施。後続の2026-10-11に反映し、実Minecraftの接続・会話・音声・字幕配送を確認したが、自動発句2回は音数不合格で編集tool試験には未到達。詳細は[反映・実機再試験](2026-10-10-workshop-edit-tool.md#2026-10-11-稼働中本体への反映と実minecraft再試験)を参照。

- `expected_text` が一行なら指定行、三行なら全行を、コード側が保持する現在の編集対象と照合する。未採用案があればその三行を使う。改行区切りと、実測の `line_1=…, line_2=…, line_3=…。` を受ける。行順・IDの重複・他行の食い違いを無視しない。
- `replacement_text` も三行なら、実際に変化した行が指定された一行だけかを確かめ、その行を取り出す。今回の原文にない置換語や、二行以上を変えた案は通さない。部分語の置換は既存editorが完成行へ戻す。
- 原文の根拠・信頼度・疑問／否定／引用／伝聞は既存 `workshop_validation`、行対象・読み・5/7/5・hard制約は既存 `Engine::run`、変更行の抽出と採用時の元句一致は既存 `Pending` を使う。保存処理を別実装しない。
- 実測の「くろい→しろい」は三行の照合後、中七だけの差分にできる設計。「光かざして」の過去文evidenceは書き換えず、原文不一致として止める。

回帰テストに三行／一行、元句の食い違い、行IDの矛盾、根拠不正、未採用案、音数不正、複数行変更、保存時の元句変更を用意した。機能変更のため、ユーザーレビュー後にこの補助のテストと保存済み応答の再検証を実施する。後続の確認tool接続時に本体ビルドは通過したが、この編集補助のテスト通過件数は未確認。

再検証は既存応答を読み、モデル呼出し・本体起動・記憶保存なしで行える。

```sh
./dogido-rust/cargo.sh test --locked workshop_editing::tool_call
./dogido-rust/cargo.sh build --locked --example check_workshop_tool_edit
dogido-llm/bin/python dogido-rust/scripts/replay_workshop_tool_edits.py \
  --record .dogido_tools/experiments/2026-10-10-gemma-e2b/native-tools.json \
  --validator dogido-rust/target/debug/examples/check_workshop_tool_edit \
  --python dogido-llm/bin/python \
  --helper dogido-rust/scripts/tts_tokens_worker.py \
  --output .dogido_tools/experiments/2026-10-10-gemma-e2b/native-tools-normalized.json
```

## 後続: 読み・音数・出典の確認tool（最小接続済み）

`inspect_verse` 一つに、`checks: ["reading", "meter", "source"]` で必要な確認項目を指定する。現在相談している三行（未採用案があればその三行）を既存の `workshop::inspect` で検査する。読み・音数・保存済み出典の検査を重複実装せず、句の変更・採用・保存のtoolは増やさない。

既存Chat Completionsの `tools` へ定義を載せ、一件の `tool_calls` を既存inspectへ渡し、実測結果を同じ呼出しIDの `role=tool` で返す。確認後は追加のtool呼出しを止めて既存の返答段階へ進む。他の相談や候補語の `inspect_text` は既存JSON経路を維持する。通常のJSON指定には確認toolの例外を添え、toolを扱う入力では今回のプレイヤー発話を末尾へ明示する。

検証は接続中心に限定した。

- 本体debugビルドと既存 `llm::tests` 5件が通過。
- 新しい接続試験1ケースは模擬モデルで通過。tool呼出し→既存検査→同じIDで結果返却→返答の2回のモデル呼出し、読み・5/7/5・出典記録、正本と保存済み句が変わらないことを確認。
- 稼働中のGemma 4 E2Bでも同じ入力を試したが、最終形では `tool_calls` ではなく従来JSONの `inspect` を選択し、native toolの往復は不成立。検査後の返答も変更箇所の質問へ逸れた。指示の例外と入力の順番だけでは解決していない。追加のprompt再設計・モデル比較・網羅試験へは広げていない。
- 全体テスト、実Minecraft・マイク・TTSは未実施。稼働中の5055本体と8080モデルは再起動・切替していない。

```sh
./dogido-rust/cargo.sh build --offline --locked --bin dogido-rust
./dogido-rust/cargo.sh test --offline --locked --lib llm::tests
dogido-llm/bin/python dogido-rust/scripts/check_workshop_inspection_tool.py
# 任意の実モデル確認。起動・停止はせず、native toolが選ばれなければ失敗として報告する。
dogido-llm/bin/python dogido-rust/scripts/check_workshop_inspection_tool.py \
  --base-url http://127.0.0.1:8080/v1
```

## 後続: Minecraft実機の配送確認（確認toolはポーズ待ちで未実施）

確認toolを含むreleaseビルドが通過し、本体を更新して実Minecraftの再接続を確認した。入力は既存の `player-input` APIへ `source=text` で送り、観測・モデル・VOICEVOX・音声再生・Fabricの字幕受領確認は実環境を使った。

- 「こんにちは、ドギド。」の一件で、Gemmaの生成、音声再生プロセスの完了、Fabricの表示ACK、Minecraftクライアントの実 `[CHAT]` 記録を確認。生成時間は715ms。ただし返答は「お、これ石やな。なんか堅そうやで。」であり、挨拶への応答品質は合格としない。
- 読み・音数・出典の確認対象となる句は、ゲームのポーズで発句時計が0のまま止まり、10分間の待機中に生成されなかった。確認toolの入力は送っておらず、この実機試験ではnative toolの成否を判定していない。
- 試験中だけ発句間隔を30秒にしたが、終了後に標準設定で起動し直し、起動引数の `interval_ms=600000` とMinecraftの再接続を確認した。設定ファイルは変更していない。更新後の本体を起動したままにし、8080のGemmaは同じPIDで継続稼働している。
- マイク入力、画面の直接目視、スピーカーから実際に聞こえた音の聴取は未確認。字幕の根拠は実クライアントの表示記録とACK、音声の根拠は実再生プロセスの開始・終了記録。

実機試験の入力・返答、ポーズ待機終了時のsnapshot、実行ファイルと関係ソースのハッシュ、Minecraftの該当チャット行、通常設定へ復帰した状態は `.dogido_tools/experiments/2026-10-10-gemma-e2b/live-minecraft-inspection/` に記録した。

## 後続: ポーズ解除後の実Minecraft試験（発句失敗で確認toolへ未到達）

ポーズ解除を実snapshotで確認後、試験中だけ発句間隔を30秒にして一件の自動発句を実行した。実ゲームの観測と同じGemma 4 E2Bを使った。

- 21:08に発句が始まったが、6回の再生成後に `accepted=false` / `failure_reason=max_regeneration_rounds` で終了した。LLM呼出しは11回、発句処理全体は約28.8秒。検査を通った三行は得られなかった。
- 「まとまらんかった。。。」の実Minecraft `[CHAT]` 行、Fabricの表示ACK、音声再生プロセスの開始・完了を確認した。確認対象の句がないため、確認入力は送っておらず、実Minecraftでの `inspect_verse` は未検証。
- 試験後は標準設定で本体を起動し直し、起動引数の `interval_ms=600000` とMinecraftの再接続を確認した。設定ファイルと8080のGemmaは変更していない。
- この結果は発句失敗の記録であり、確認toolが実ゲームで失敗したという判定ではない。モデル調整や生成検査の緩和は行っていない。

該当snapshot・サーバーログ抜粋・Minecraftのチャット行・復帰後の状態は、上記実機記録ディレクトリの `attempt-2/` に保存した。

## 2026-10-11の後続確認

編集補助を含むRustのworkshop関連単体83件、native編集模擬8件、検査模擬、既存相談・編集・会話43件が通過した。稼働中E4Bでは検査toolの往復に成功し、編集toolは別用途のJSON操作を選んで不成立だった。旧JSONの `inspect` / `stage_player_edit` 受付は撤去済みで、候補語の検査も `inspect_verse` に統合している。上記のE2B試験当時の経路・結果とは区別する。公式推奨条件との差と測定範囲は[編集toolの検証記録](2026-10-10-workshop-edit-tool.md#2026-10-11の検証結果)を参照。

同日の案内修正後、実E4Bで指定語の部分変更と中七全体の変更はnative tool一回で保存まで完了し、音数不合格は保存せずtool結果から会話を続けた。既存samplingと公式推奨samplingで各3ケースを確認した。このE4Bの後続結果を、初回E2Bの能力や自動発句の成功として扱わない。詳細と生応答の保存先は[案内修正後の再試験](2026-10-10-workshop-edit-tool.md#案内修正後の再試験)を参照。
