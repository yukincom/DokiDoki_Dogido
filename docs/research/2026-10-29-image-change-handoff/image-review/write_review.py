from pathlib import Path
import json,collections,hashlib
from datetime import datetime,timezone
root=Path('/private/tmp/dogido-tests-final-20261029/image-review/prepared-v2')
m=json.loads((root/'manifest.json').read_text())
rows=[json.loads(x) for x in (root/'results.jsonl').read_text().splitlines()]
conditions=m['spec']['conditions'];labels=['画像のみ','変化率付き','変化率＋領域付き']
assert len(rows)==117 and len({(r['case_id'],r['condition'],r['seed']) for r in rows})==117
assert all('error' not in r for r in rows)
observations=[
 ('完全に同じ金鉱石の画像','全条件3/3で金への発話。差分0という情報だけでは沈黙しない。履歴がない初回の感想も含むため、9件を誤反応とは採点しない。'),
 ('海岸。太陽・雲などの弱い差','領域条件だけ0/3。ほかは景色への感想が各1件。物体の新規出現を主張していない。'),
 ('水中の微小な差','変化率条件だけ2/3で景色への感想。数値を添えることで発話が増える例。'),
 ('水中の泡や描画の小さな差','全条件3/3で景色への感想。過去発話の反復はこの独立試験では判定できない。'),
 ('水中。プレイヤーの向きと色・光の差','数値条件は明るさ・海の色の差に言及。画像のみでは既にあった珊瑚を「見えてきた」と述べる例がある。'),
 ('砂浜の左下にカメが現れる','全条件3/3でカメを認識。画像だけでも成功。2%以下を除外する仮定ではこの画像対を落とす。'),
 ('金鉱石の壁。右上の見え方に差','全条件3/3で金への発話。「金の発光」のように画像だけでは性質を確定できない表現もある。採掘や新発見の実イベントは画像だけから確定しない。'),
 ('洞窟のプレイヤーが画面中央から上端へ移る','全条件で急な暗さの表現。平均輝度の差は小さい。数値・領域条件のseed44は、上端に残るプレイヤーを「消えた」と表現している。'),
 ('海岸の夜明け。空・地面が明るくなる','変化率条件だけ全て沈黙。領域を追加すると全て景色への発話に戻り、情報を足すほど静かになる関係ではない。'),
 ('同姿勢で右の空白が地形表示に置き換わる','画像上は描画更新に見える。木の実際の成長・増加は確認できないが、数値条件は全て「増えた」、領域条件にも「生えてきたんちゃう」との推測がある。'),
 ('水中洞窟の床が明るくなる','逆向きの「暗くなった」は画像のみ1/3、数値2/3、領域1/3。領域条件は2回沈黙するが、比較内容の正しさは改善していない。'),
 ('大きな視点変更。下向きからプレイヤーが見える向きへ','0%でも見え方は変わる。各セルの差が24以下なので変化セルは0だが、平均差は8.026。暗さへの反応が中心で、視点変更の理解は安定しない。'),
 ('水中から夜の砂浜。移動と旋回を含む','全条件3/3で発話。数値・領域条件は地上へ出たことに言及。全条件が同じカメラ移動量を受け取っている。'),
]
review={'reviewed_at':datetime.now(timezone.utc).isoformat(),'status':'reviewed_diagnostic_not_production_acceptance','source_results_sha256':hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest(),'response_count':len(rows),'excluded_preliminary_responses':30,'method':'担当アシスタントが前後の実画像と全117本文を照合。追加の採点モデル呼出しなし。発話率を誤反応率へ換算しない。','conditions':{},'cases':[]}
lines=['実画像の差分情報を渡す比較試験 — 2026-10-29','',
'**結果：今回の13組では、変化率だけを添えても発話数は変わらず、領域まで添えると3応答減りました。内容の誤読は残っており、差分情報の追加による一貫した改善は確認できません。**','',
'稼働中のGemma 4 26B A4B QAT UD Q4_K_XL＋F16 projectorを使用。実際の連続撮影13組×3条件×3 seed（42/43/44）、計117応答。画像のみ条件も前後2枚・撮影時刻・カメラ移動量を受け取り、差分情報以外は同じです。保存済み192px画像は加工していません。','',
'| 条件 | 発話 | 沈黙 | 発話率 |','|---|---:|---:|---:|']
for c,label in zip(conditions,labels):
 rr=[r for r in rows if r['condition']==c];s=sum(r['parsed']['action']=='speak' for r in rr)
 review['conditions'][c]={'responses':len(rr),'speak':s,'silent':len(rr)-s,'speak_percent':100*s/len(rr),'output_formats':dict(collections.Counter(r['output_format']for r in rr))}
 lines.append(f'| {label} | {s}/39 | {39-s}/39 | {100*s/39:.1f}% |')
lines+=['','同じ13組の繰返しなので、39個の独立した場面ではありません。過去発話なし・各条件3回の診断です。発話をそのまま過敏反応とは数えず、会話履歴による連続発話の抑止や実運用の発話率は未評価です。','',
'「変化率」は32×18の576セルに縮小したRGB平均について、前後の平均絶対差が24/255を超えたセルの割合です。1セルは約0.174%。変化の重要度や、原画像の厳密な変化画素率ではありません。領域は4近傍の連結セルの外接矩形を大きい順に最大8個。矩形内の全画素が変わった意味ではありません。','',
'| 実画像対 | 変化率 | 平均差/255 | 画像のみ | 率付き | 率＋領域 | 画像で確認したこと |','|---|---:|---:|---:|---:|---:|---|']
for case,(description,note) in zip(m['cases'],observations):
 counts=[sum(r['parsed']['action']=='speak' for r in rows if r['case_id']==case['id'] and r['condition']==c)for c in conditions]
 review['cases'].append({'case_id':case['id'],'observation':description,'assessment':note,'speak_counts_by_condition':dict(zip(conditions,counts))})
 f=case['frames'];view=f"[{case['id']} 前]({f[0]['model_image']})・[後]({f[1]['model_image']})"
 lines.append(f"| {view} | {case['metrics']['changed_area_percent']:.3f}% | {case['metrics']['mean_rgb_difference_0_255']:.3f} | {counts[0]}/3 | {counts[1]}/3 | {counts[2]}/3 | {description} |")
lines+=['','各数は発話回数/3回。以下は過敏さ・見落としを考えるうえでの具体例です。','',
'- **0%・完全同一画像**：全条件3/3で金鉱石について発話。「お、金が見つかったで！」など。数値を知らせても沈黙にはならない。ただし履歴がなく、初めて見た景色への感想も許す指示なので、全件を誤りとはしていない。',
'- **0.174%・水中の微小差**：画像のみ0/3、変化率付き2/3、領域付き0/3。「海の中って、めっちゃ綺麗やなぁ！」など。数値が発話を増やす場合もある。',
'- **1.910%・カメ出現**：全9応答がカメを認識。画像のみでも「あ、見て！カメがおるやん！」。平均差2.035も低く、面積2%以下の棄却では意味のある変化を落とす。',
'- **15.451%・夜明け**：画像のみ3/3、変化率付き0/3、領域付き3/3。率だけでは静かになったが、領域を足すと景色への発話へ戻った。',
'- **20.833%・地形の描画更新らしい変化**：変化率付きでは3回とも木が「増えた」と表現。領域付きにも「新しい木が生えてきたんちゃう？」。画像で見える範囲の増加と、世界内の成長を混同する余地が残る。',
'- **36.458%・床が明るくなる**：「さっきより暗くなった」の逆向き比較が、画像のみ1/3・変化率付き2/3・領域付き1/3。原画像の平均輝度は30.044→46.420（RGBへ0.2126/0.7152/0.0722の重み）。明るさ差の向きは今回の絶対差情報に含めていない。','',
'**この結果からの判断**：コードで差分量・領域を残すことには診断上の価値がありますが、それを渡すだけで判断が改善するとは言えません。面積の低さだけで画像を捨てる閾値は採用せず、物体・出来事の意味は画像から判断させるのが今回の結果に沿います。「比較不能」と「変化なし」を分ける必要もあります。明るさの増減など符号付きの情報や、発話済み履歴を加えた場合の効果は今回未測定です。','',
'形式は117件とも正常終了し、内容を解釈できました。生JSONは54件、コード枠付きJSONは63件です。コード枠は外形違反として別記し、沈黙や通信失敗には数えていません。runの終了コード2は内容確認待ちを示し、未レビューのまま合格を出さない設計です。この文書は取得後の内容確認です。','',
'最初の30応答は、領域全体を中心位置のラベルで表してしまう補助情報の不備があり、最終比較から全て除外しました。修正後は座標だけにし、同じ13組・承認済み追加指示のまま117応答を再取得しました。初回の記録も別フォルダに残しています。','',
'[全117応答の原文一覧](REPORT.md)・[生の要求と応答](results.jsonl)・[固定した画像と測定値](manifest.json)・[要求プレビュー](request-previews.json)・[内容確認記録](review.json)','',
'[テストコード](../../combined/dogido-rust/tests/acceptance/part5_model.py)・[実行手順](../../combined/dogido-rust/tests/acceptance/part5_model.md)']
(root/'review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
(root/'RESULTS_REVIEW.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(review['conditions'],ensure_ascii=False,indent=2))
print('content review saved; complete 117-response diagnostic; no production acceptance claim')
