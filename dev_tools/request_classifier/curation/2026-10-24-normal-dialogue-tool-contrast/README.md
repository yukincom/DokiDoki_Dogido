# 通常対話の道具・雑談対照例（2026-10-24）

[既存546件](../../releases/2026-10-24-normal-dialogue-546/README.md)を基に、道具の雑談と剣への持ち替え依頼を区別する合成候補32件を作成。`not_target` 24件、`equip_sword` 8件。

- [全32件](materials-review.md)。ユーザーの実機原文は変更せず、新しい文はすべて `source=synthetic` と記録。
- 根拠はtrainに入れた実機の誤分類。道具の感想・使い方の質問・作業報告を対象外、今の手持ちを剣へ替える明示依頼を持ち替えとする。
- 現行16分類と空contextを維持。既存546件および候補内で正規化本文・IDの重複は0件。
- [文字列の近さ](coverage-review.json)は照合補助であり、意味の自動検査ではない。元のdev/testは変更していない。
- 32件は確認用の候補で、正本546件には未統合。採用時は8つの話題groupをすべてtrainへ追加する想定。学習・推論・本体変更は未実施。

[候補JSONL](candidates.jsonl)・[合成元と提案ラベル](evidence.jsonl)・[件数とhash](manifest.json)
