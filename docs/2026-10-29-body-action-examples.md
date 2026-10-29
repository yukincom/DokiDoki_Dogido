# 身体行動のJSON例（パート6）

2026-10-29。編集完了。テスト・ビルド・実モデル確認はテスト部屋へ引き継ぐ。

環境判断の `body_action` は既存の受信形式を保ち、モデルへ渡す例を完成JSONへ変更した。
通常の例は共有プロンプト資産で持ち、今回の `available_actions` にあるものだけを組み立てる。
`inspect` の `target_id` は今回の最初の候補IDを例に使う。モデルの選択を固定する指定ではない。
`inspect_image` は既存の画像添付時の契約へ完成例を置き、`image_target` が `body_action` 内のオブジェクトであることを示す。

例（説明用。実モデルの応答ではない）：

```json
{"action":"speak","speech":"左の黄色い花を見てくるで。","body_action":{"action":"inspect_image","target_id":null,"image_target":{"description":"左の黄色い花","kind":"flower","color":"yellow","horizontal":"left","vertical":"middle"}}}
```

変更ファイル：

- `dogido_server/llm/companion_prompts.json`：完成例と画像対象の説明。
- `dogido-rust/src/companion_prompt.rs`：今回の行動候補だけを例へ投影。
- `dogido-rust/src/environment/reaction.rs`：既存の環境要求へ例を添付。

確認対象は、提示された完成例が応答受信と既存 `State::choose` を通ること、候補外の行動例が混ざらないこと、画像対象がオブジェクトで渡ること。
行動の選択検査・実行・撮影の処理は変更していない。
