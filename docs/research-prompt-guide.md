# ドギドの関西弁・一人称を変えて比較する

一人称を「オレ」から「僕」に変えるなら、まず[共通プロンプト](../dogido_server/llm/companion_prompts.json)の `base` にある「一人称は『オレ』。」を書き換えます。標準語にするなら、同じ `base` の方言指定、語尾の例、避ける口調も見直します。相棒としての距離感や動物への接し方は同じ段落にあります。話し方だけを比べる実験では、この部分は両条件で揃えます。

## 発話別の書き換え先

| 比べたい発話 | 書き換える場所 | どの言葉に効くか |
| --- | --- | --- |
| モデルが話すときの共通の口調 | [共通プロンプト](../dogido_server/llm/companion_prompts.json) の `base` | 会話、環境への反応、自動発句、ゲーム内の川柳相談に渡す一人称・語尾・口調の基本指示。各場面では下記の指示も一緒に渡ります。 |
| プレイヤーへの返事／周囲を見て自分から話すとき | 同ファイルの `dialogue.reply`（返事）、`dialogue.ambient`（自発発話）、`modes.normal`・`modes.tension`（平時・緊張時） | 相槌や話しかけ方を場面ごとに変えられます。身体観測が入るときは `body_observation` にも「ドギドはオレ」という視点の説明があります。一人称を揃えるときは、誰から見た位置かという意味と `{player}` を保って書き換えます。 |
| 見えたかどうかを尋ねたとき／身体を動かすときのひとこと | [会話の入力文](../dogido-rust/src/chat_prompt/data.json) の `policies.hypothesis`、[共通プロンプト](../dogido_server/llm/companion_prompts.json) の `environment_body_examples` | 前者には推測を扱うときの「オレにも見えた」という禁止例、後者には「そばへ戻るで。」などの発話例があります。例文を変える場合も、観測と推測の区別や行動IDは保ちます。 |
| 雷や戦闘終了などへの反応 | [出来事別の状況文](../dogido_server/llm/reaction_situations.json) の該当する `event`（例：`thunder_reaction`、`aftermath`） | 共通の口調に、その出来事の説明が加わります。ここで方言を直す対象はモデルに読ませる状況文です。実際に口にする言葉はモデルが生成します。 |
| 自動で川柳を詠む前のひとこと | [発句テンプレート](../dogido-rust/src/haiku_prompt/templates.json) の `haiku_irony`（user メッセージ内で `description` を指示する文） | 「自然な関西弁の一文」「〜が浮かんできたわ」と明記されています。前置きを標準語にする場合は、共通の `base` とここを揃えます。 |
| 自動発句の三行 | 同じ発句テンプレートの `haiku_draft`（初稿）と `haiku_line_regeneration`（行の再生成） | 句そのものの言葉遣いを指定する場所です。前置きの `description` と分けて、三行の表現を調整できます。 |
| ゲーム内で句を説明する・一緒に推敲する | [共通プロンプト](../dogido_server/llm/companion_prompts.json) の `workshop_identity`・`modes.workshop` と、[相談テンプレート](../dogido-rust/src/workshop_prompt/templates.json) の `main`・`extras` | 共通の `base` に共同編集者としての話し方を加えます。句の意味を聞かれたときの言い回しや例文は `main`、個別の相談場面で加わる指示は `extras` にあります。 |
| 乗用動物の呼び寄せ／照明器具が増えたときのコメント | [呼び寄せの指示](../dogido-rust/src/assist/horse_recall/prompts.json) の `selection_followup`・`selection_unavailable`、[照明の指示](../dogido-rust/src/light_plan/prompts.json) の `instruction`・`contract` | 例文を含む「候補が見つからないときの返事」や、備えが増えたときの短い発話を個別に調整できます。 |

**固定のセリフと録音音声。** モブの位置を知らせる「オレから見て…」は[モブ情報の定型文](../dogido-rust/src/combat/queries/mob_information.rs)、複数の敵を数えたときの「…おるで。」は[敵の警告文](../dogido-rust/src/threats/groups.rs)にあります。これらはコードが直接組み立てます。「がおるで」のような[録音済みの音声片](../cue_voice/common/phrases/)を使う警告では、表示文と再生音声を別々に確認してください。

## 変更を試す

- **まず入力を読む。** [組み立て済みのプロンプト27例](2026-10-30-prompt-prefix.md#変更後の全文)で、通常会話・環境反応などにどの文が一緒に入るかを確認できます。比較用の場面から作った入力例なので、Minecraftのセットアップ前でも読めます。
- **川柳の相談だけで試す。** 保存済みの句とローカルの会話モデル・実行環境があれば、[川柳のテキスト相談室](../dogido-rust/workshop-text.md#プロンプトの調整)をMinecraftなしで使えます。「プロンプト調整」→「キャラクターへの指示（system）」で一人称を書き換え、「保存して次の発言から反映」を押します。変更はこの相談室の全会話が対象です。同じ句で「この句で最初から話す」を選び、変更前と同じ質問を送ると比べられます。「直近に実際に送ったプロンプト」でモデルが受け取った文も確認できます。
- **ゲーム本体で試す。** 表のJSONはRust本体のビルド時に取り込まれます。編集後はリポジトリのルートで `./dogido-rust/cargo.sh build --release --locked` を実行し、[通常の手順](../README.md#1-本体の準備と起動)で新しい本体を起動します。文面を変えるときは、JSONのキー、`__DOGIDO_SHARED_BASE__` などの差し込み記号、モデルに返させる項目名（`action`・`speech` など）を残してください。
