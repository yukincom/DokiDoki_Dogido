# 前置きと川柳材料の分離試験

2026-10-29のユーザー指定に従う独立試験。本体の自動発句・workshop・通常起動には未接続。

1. 元材料を**3つ以上**選び、その材料を取り上げた前置きと選択IDを同じ応答で生成する。
2. 選んだ元材料の中から**別々の3材料**を選び、上五・中七・下五へ一つずつ割り当てる。この要求へ前置き本文は渡さない。
3. **各行の元材料**と、別欄の**調理方針としての前置き**から一句を生成する。

前置き・前置きの節・詩的解釈の派生データは材料候補へ入れない。元材料の名称と説明は `source_ref` でまとめる。コードは提示候補への所属、前置きの3材料以上、後段の3つの別材料、行番号の対応を照合する。異なるIDでも意味が同じ場合があるため、この構造検査だけで内容を合格にしない。

各ケースの見込み・合格条件・確認範囲は `cases.json` に保存する。モデルに渡すのは材料と生成指示だけで、旧句・期待する句・評価コメントは渡さない。前置きが選択材料を取り上げているか、各行に割当材料の内容があるか、同じ対象・性質の言い直しになっていないかを、全結果に対して別途レビューする。

5ケース×最大3段階＝最大15要求。各段一度だけ。前段で構造条件に失敗したケースは失敗を保持して打ち切り、条件を満たす他ケースだけ次段へ進める。自動再試行・意味採点LLM・音数による再生成なし。音数はかな出力の診断値で、本体の読み補正・実行経路一式の確認ではない。

先の `preface_separation_v1` は「3材料を選ぶ必要はない」という誤った試験指示を含んでいた。2材料の結果を、今回の要求の検証結果として使わない。元の生応答・送信プロンプトは `.dogido_tools/haiku-preface-separation/2026-10-29/` に残す。今回の修正版は `2026-10-29-three-materials/` に分けて保存する。

```sh
python3 dev_tools/haiku_preface_separation/probe.py freeze --repo . --out .dogido_tools/haiku-preface-separation/2026-10-29-three-materials
python3 dev_tools/haiku_preface_separation/probe.py prepare --out .dogido_tools/haiku-preface-separation/2026-10-29-three-materials --stage preface
```

`prepare`が保存する全文を提示してから、同じ引数で`prepare`を`run`へ変えて実行する。preface → select → poemの順。既存結果があるstageは再実行しない。

```sh
python3 -m unittest discover -s dev_tools/haiku_preface_separation -p 'test_*.py' -v
```

入力は過去の保存句に残った一次材料で、元発句時点の全観測の再現ではない。共有人格は `companion_prompts.json` から固定する。プロンプト全文・スクリプト・全生応答・選択と行割当・各ケースの所見を保存する。会話を含む入力・結果はGit対象外の試験ディレクトリに置く。Minecraft・音声・本体保存の確認は含まない。
