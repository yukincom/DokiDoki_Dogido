# LLMプロンプトの固定部分を前方へ配置

2026-10-30。実モデル試験前のレビュー段階。生成LLM・モデルAPI・稼働サービスは呼び出していない。

## 変更

通常会話と環境反応のsystemを、人格 → 観測方針 → 選択済みの出力契約 → 対話目的 → 身体観測の説明 → mode → 匂いの説明の順へ配置した。身体説明には既存の呼び名をそのまま入れる。環境での候補ID入り行動例は、その後ろに置く。

共有の文言資産と通常会話のuserテンプレートはbyte単位で不変。system/userの役割、指示の適用条件、現在観測、会話履歴、今回の入力、生成回数、画像の後付け経路、既存の採否処理を維持する。新しい検査・拒否条件は追加していない。完成した要求を保存して使い回さず、現在観測と履歴は毎回組み立てる。

環境body出力の契約は組立時に選ぶ。旧処理の「choice契約はsystem末尾にある」という前提を外し、契約文を削ったり二重に入れたりしない。

## 変更後の全文

以下は実builderで出力した全文。観測・履歴・名前は比較用の合成入力で、実プレイ記録ではない。省略記号や伏せた本文はない。

- [通常会話・身体観測あり・匂いなし](reviews/2026-10-30-prompt-prefix/chat-normal-smell0-body1.txt)
- [緊張時の会話・身体観測と匂いあり](reviews/2026-10-30-prompt-prefix/chat-tension-smell1-body1.txt)
- [環境反応・身体観測と匂いと行動候補あり](reviews/2026-10-30-prompt-prefix/environment-smell1-actions1-body1.txt)
- [全27例の全文](reviews/2026-10-30-prompt-prefix/all-prompts.txt)
- [入力detailsと組立後messagesのJSON](reviews/2026-10-30-prompt-prefix/requests.json)

27例は通常会話builderの4モード×匂い有無×身体観測有無の16例、環境反応8例、既存の反応leaf3例。`chat-workshop-*`は通常会話builderのcharacter_mode分岐を確認する例であり、川柳WSのtoolプロンプトを変更した意味ではない。

## オフライン確認

- 関連30試験成功。共通builderの48組で指示の行別出現数とbyte数を保持し、環境の8組では選択契約・候補例の位置・user JSONの一致を確認した。
- 既存fixtureの変更はsystem本文の順序だけ（chatのpool 2項目、reaction 279項目）。入力、user本文、sanitizer期待値は不変。
- 16例を変更前の本体バイナリの`build-chat-prompt`と比較し、user本文の完全一致とsystemの各行・出現数・byte数の一致を確認した。これはプロンプト構築だけで、モデル呼出しはない。
- 既存の`chat_prompt::tests::body_guidance_uses_existing_player_name_only_when_body_is_present`は変更前・変更後とも、古い英語の`player は…`という期待文言で失敗する。共有文言は現在`プレイヤーは…`。この既存期待値の修正は今回の範囲に含めていない。

前方一致の比較はUnicode文字数であり、token数・KVキャッシュ命中率・実測時間ではない。

| 合成入力間の変化 | 変更前 | 変更後 |
| --- | ---: | ---: |
| 身体観測あり、通常→緊張 | 283 | 2,370 |
| 身体観測あり、匂いなし→あり | 863 | 2,417 |
| 身体観測なし、通常→緊張 | 283 | 1,243 |

速度・生成品質・実Minecraftとの接続は、全文レビュー後の実モデル試験で確認する。

全文の再出力は`DOGIDO_PROMPT_REVIEW_DIR`を出力先にして、`cargo test --lib companion_prompt::tests::export_full_review_prompts -- --ignored --exact`。この明示実行だけが指定先へファイルを書き出す。
