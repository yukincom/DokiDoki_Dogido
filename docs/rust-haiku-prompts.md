# 川柳の Rust プロンプト組立

`haiku_irony`、`haiku_scene`、`haiku_draft`、`haiku_line_grounding`、`haiku_line_regeneration`、`haiku_workshop_revision` のメッセージ組立は `haiku_prompt` が所有する。`StructuredRequest` を読み取り、モデル、route、温度、token 上限、fallback を変更しない。`python_worker::Helper::prepare` は Python への送受信を行わず、この純粋関数を呼ぶ。

固定文の正本は `dogido-rust/src/haiku_prompt/templates.json`。literal／slot の文章部品へ、材料、制約、出典、共有文脈、失敗理由を Rust で組み込む。検査要求は合否先行・一時整数番号・不合格理由の形式を使い、revision の同案拒否案内もこのテンプレートで管理する。

`text_format::spaced_json` は会話・川柳・workshop・国語に共通する空白付きJSON整形。単体値の `None/True/False` 表記は `text_format::value_text` が所有し、呼出側が配列・辞書の表記形式を指定する。JSON整形と単体値の文字列化を混同しない。

348件の保存済みfixtureは、6種のroleと本文全体について、4生成方式、構造物・可視性・詩的解釈、空材料、Unicodeと切詰め、修正差分、失敗理由、共有文脈、契約再試行を照合する。文章仕様を変更するときだけ同じ変更内でfixtureも更新し、`./dogido-rust/cargo.sh test --offline --locked haiku_prompt --lib` を実行する。今回の整形共通化では本文を変更していない。

検査の既定512 tokens、欠けた判定だけの再照合、最大再生成回数、同案拒否、固定行・CASはRust生成器が所有する。材料準備、normalize／correct、signatureもRustが行い、必要な漢字の辞書tokenだけPython補助から受け取る。通信の上限・失敗後の停止・取消・子の回収は `python_worker` が管理する。

workshopの生入力はJSON境界で受ける。`workshop_editing` は行差分・編集案・検査結果を型付きで返し、runtimeは行をJSONへ戻して読み直さず `Pending::stage` へ渡す。元句一致・対象外不変・音数・出典の検証と保存権限は従来どおりRustが持つ。保存済み2,666ケースの出力契約は維持する。

実Minecraft、実モデル、マイク、音声再生の検証は、自動テストとは別に扱う。
