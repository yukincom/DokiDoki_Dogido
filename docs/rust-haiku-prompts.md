# 川柳の Rust プロンプト組立

`haiku_irony`、`haiku_scene`、`haiku_draft`、`haiku_line_grounding`、`haiku_line_regeneration`、`haiku_workshop_revision` のメッセージ組立は `haiku_prompt` が行う。既存の `StructuredRequest` を読み取り、モデル、route、温度、token 上限、fallback を変更しない。`haiku_bridge::Helper::prepare` は Python への送受信を行わず、この純粋関数を呼ぶ。

固定文は Python 正本から literal／slot のテンプレートとして生成し、材料、制約、出典、共有文脈、失敗理由を Rust で組み立てる。テンプレートは文章の部品だけで、Python コードや任意の式を実行しない。検査用は移行版 `scripts/haiku_grounding_prompt.py` の合否先行・一時整数番号・不合格理由だけを返す形式を正とする。revision の同案拒否案内も維持する。

348 件の golden 比較は、6 種の role と本文全体について、4 生成方式、構造物・可視性・詩的解釈、空材料、Unicode と切詰め、修正差分、失敗理由、共有文脈、契約再試行の各分岐を照合する。正本更新時は `python dogido-rust/scripts/generate_haiku_prompt_fixtures.py` で更新し、`./dogido-rust/cargo.sh test --locked haiku --lib` を実行する。

検査の既定 512 tokens、欠けた判定だけの再照合、最大再生成回数、同案の拒否、固定行・CAS の検査は既存の Rust 生成器が所有する。今回それらのループは変更しない。辞書による normalize／correct と signature、`HaikuPreparation` の材料準備は引き続き Python 補助を使う。補助の失敗後停止と frame 上限は維持し、prompt が後続の辞書応答を消費しないことを模擬補助で確認する。

実 Minecraft、実モデル、マイク、音声再生による検証はこの移植には含まない。
