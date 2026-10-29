# 川柳相談の具体変更と行表示（パート8）

2026-10-29。編集完了。テスト・ビルド・実モデル確認は未実施。

今回の対象は相談中の材料表示と、分類済みの具体変更を既存編集へ渡す指示。
並行作業中の前置き・材料選択・自動発句・再生成・新案生成・保存形式は変更しない。

共有文脈の `current_verse` と `pending_verse` に「上の句・中の句・下の句」を本文と組で表示する。
保存済みの行本文・ID・版はそのまま保ち、読み・音数に変換しない。
現在の `conversation_candidate` は、その採用actionを今回公開しない具体変更でも参考材料へ渡す。
具体変更の操作は従来どおりnative `stage_player_edit` と `ask` の範囲を保つ。

モデル向け指示は次の三つへ揃えた。

- 本人が置換語を指定したら、その語を既存toolへ渡す。前の相談案と違う語も今回の指定を保つ。
- 同じ対象行の相談案を選んだら、保持された案の語を使う。
- 指定語も選ばれた案もなく置換語が分からなければ、`ask` で尋ねる。変更分類だけから新しい語を補作しない。

音声認識や表記差の解釈はLLMに任せる。コードによる原文再抽出・語句一致・品質検査は追加しない。
対象行・元句・版・入力取消・対象外不変・保存整合性の既存検査を使う。

説明用の材料例（実行採取ではない）：

```json
{"workshop_context":{"current_verse":"上の句: あさのそら\n中の句: ひかりがゆれて\n下の句: はながさく","conversation_candidate":{"proposal":{"line_index":0,"target_fragment":"あさのそら","replacement_text":"さくらいろ"},"validation_codes":[]}}}
```

`conversation_candidate` は既存の保持案をそのまま渡す。上記は入力形式を示す例で、生成や保存を実行した記録ではない。

変更ファイル：

- `dogido-rust/src/workshop_projection/mod.rs`：行名付き表示、保持案の読み取り投影。
- `dogido-rust/src/dialogue/workshop_runtime.rs`：具体変更時の指示。
- `dogido-rust/src/workshop/tools.rs`：既存native toolの説明。
- `dogido-rust/src/workshop_prompt/templates.json`：具体変更時の共通説明。

テスト部屋では既存パート8の指定語・保持案・置換語なしの三ケース、対象・版・取消・保存の対照を確認する。
表記・材料・モデル品質の新しい足切りは設けていない。
