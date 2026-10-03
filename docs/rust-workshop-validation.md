# Rustの川柳相談検査

相談promptに使ったRustの `workshop_projection` の投影を、その試行のモデル出力検査にも使う。`workshop_validation` は現行の厳格JSON契約、発話の連続した根拠、confidence、明示意思、行指定・句断片の一致、検査済み事項だけの発話を検査し、従来と同じ `contract_errors / step / reason` を返す。検査のためのPython往復はない。Python helperへ検査要求を送る経路は持たない。

認識原文と会話用解釈を混同せず、編集・採否・終了の根拠は原文で確認する。否定・疑問・条件・引用・伝聞の検査は既存 `workshop_input_guard`、発話の掃除と外形検査は既存 `reaction_leaf::sanitize` を再利用する。findingsとline reference/proposalの確定は、カタカナからひらがなへの文字変換と指定記号の除去で完結し、辞書SDKを呼ばない。

自然な相談は一つのagent stepが次手を選び、不成立時は正本・未採用案を維持してコード固定返答へ戻る。材料投影・固定編集の取り出し、音数・hard制約の実検査、pendingとCAS・保存・戦闘中断はRustコードが担当する。任意のUniDic token・読み補助だけPython helperへ渡す。followupの外形・段階・採否もRust側で再検査する。

モデル設定・契約再試行を含む各段階最大2回の生成・段階上限は変更せず、既存のturn全体95秒とhelperの取消・回収を維持する。検査要求にも従来の1,000,000 byte上限を適用する。型不正・不正な投影・上限超過を成功したfallbackへ変換しない。

Python由来の比較例で検査結果を照合する。型・未知フィールド・エラー順序と件数上限、confidence境界、未採用案、原文と解釈の差、行の競合と一意性、未実行の修正・保存や未完了の検査の断言、切り詰めとUnicodeを含む。検査にはIPCを使わず、任意helperの再利用と取消・期限・回収は別に確認する。実モデル・実音声・Minecraftでの確認は別途必要。
