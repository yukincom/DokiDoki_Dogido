# Rustの川柳相談prompt

相談のsystemは共通 `base`、`workshop_identity`、`modes.workshop` とJSON専用指示を組み合わせる。
発話の120字上限は `companion_prompts.json` の `modes.workshop` に集約する。
平時の35字指定は川柳相談へ入れない。

Rustの `workshop_projection::details_for` が相談材料を作り、`workshop_prompt` がsystem/userメッセージと契約違反時の再試行文を組み立てる。Python helperは任意のUniDic token補助だけを担当する。

現在句・未採用案・相談中の案、認識原文と会話用解釈の差、会話段階、実検査結果、検査後の返答制限を含む11条件は従来と同じ。JSONは既存のPython互換serializerを使い、許可リストの順序と重複も維持する。固定編集がある場合は、従来どおり契約再試行の追加文を付けない。

材料投影、意味・evidence検証、音数・hard制約の実検査、編集・採用のCASはRustのコードが担当する。辞書が必要な読み処理だけ既存の任意helperへ渡す。会話モデル、420 token、温度0.25、契約再試行を含む各段階最大2回の生成、初手・実検査・修正検証の段階上限は変更しない。既存helperの15秒交換上限とturn全体95秒の取消・回収も維持する。Rustで作るpromptにも従来の応答サイズ上限を適用する。不正な投影を成功fallbackへ変換しない。

検証は、Python由来の投影例と純粋例の全文比較、原文に基づく固定編集・終了検証、サイズ上限、任意helperの再利用・不正応答・取消・期限・実子回収を含む。長文のgoldenは共通文字列を参照して保存し、比較時に全文へ復元する。実モデル・実音声・Minecraftでの確認は別途必要。
