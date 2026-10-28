# Rustの川柳相談prompt

相談のsystemは共通 `base`、`workshop_identity`、`modes.workshop` とJSON専用指示を組み合わせる。
発話の120字上限は `companion_prompts.json` の `modes.workshop` に集約する。
平時の35字指定は川柳相談へ入れない。

Rustの `workshop_projection::details_for` が相談材料を作り、`workshop_prompt` がsystem/userメッセージを組み立てる。Python helperは任意のUniDic token補助だけを担当する。

現在句・未採用案・相談中の案、認識原文と会話用解釈、会話段階、BERTの分類結果、直前の実行結果を渡す。行指定・具体変更・終了をモデルに再判定させない。具体変更は既存native `stage_player_edit` で旧語と置換語を解釈させ、原文の正規表現から直接編集しない。対象行と元句はコードが補完する。

`evidence`・`confidence` は任意の記録であり、原文からの逐語抽出や閾値を要求しない。相談案、採否、終了に付随する採否、称賛feedbackにも原文一致を要求せず、その修復のための再試行を追加しない。WS内のlesson解除は会話LLMが `clear_lessons` を選び、既存memory runtimeが実行する。BERTが終了に分類し、未採用案がなければ直接閉じる。未採用案がある場合はLLMが採用・破棄・会話を選ぶ。採否が未確定なら尋ね、askや会話の応答ではopenとpendingを保持する。LLMが採用または破棄を選んだときだけ、その操作の成功後に閉じる。

モデル出力は `workshop_response` でdecodeし、actionが必要とする項目だけを整える。意味・語形・読み未確定・音数・hard制約・表現や材料の重複を再審査しない。新案は生成された表記を保持する。材料投影、対象・元句・版・対象外不変、取消と保存整合性は既存Rust処理が担当する。読みは本人の質問に必要な場合だけ既存toolで参照し、編集の前提にはしない。

応答サイズ、実行回数、期限・取消・子回収の上限は既存の所有者で扱う。不成立を未実行の成功へ変換しない。自動発句・assist・戦闘のプロンプトと処理は今回の対象外。

2026-10-28の受信・適用への変更は、自動テスト・ビルド・起動前チェックを確認済み。実モデル・実音声・Minecraftでの再確認は未実施。移植時のPython由来fixture比較や旧原文検査の通過を、今回の確認済みとして扱わない。[現行の受信・適用](rust-workshop-validation.md)と[変更記録](2026-10-28-workshop-interpreted-edits.md)を参照する。
