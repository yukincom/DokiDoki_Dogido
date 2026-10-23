# GPTプレイヤーの句相談実験

同じプレイヤー指示・同じ原句で、luna / sol / astraと推論low / medium / highを比較する。
Minecraftは操作しない。既存テキスト相談室のクライアントを使い、通常の句記憶と独立したコピーへ保存する。
プレイヤー指示は確認待ち。実モデルによる採取は未開始。

条件・観察項目・指示全文は[実験メモ](../../docs/2026-10-20-gpt-player-experiment.md)。

```bash
# オフラインで条件表と句コピーを作る。既存出力は上書きしない。
python dev_tools/gpt_player/experiment.py prepare \
  --source-memory .dogido_memory/rust-migration \
  --output .dogido_tools/experiments/2026-10-20-gpt-player

# プレイヤー指示を確認した後、条件表のrun_idを一つ選んで実験用窓口を起動。
python dev_tools/gpt_player/experiment.py launch --prompt-reviewed \
  --run-dir .dogido_tools/experiments/2026-10-20-gpt-player/runs/p01-luna-medium-r01
```

`launch`は既存の`launch_workshop_text.py`を専用ポート5067・その条件だけの記憶コピーで起動する。
実行には通常のPython環境と現在の`workshop_text` releaseビルドが必要。既存ポートを停止・横取りしない。
GPTは親エージェントが条件ごとに新しい会話で起動し、表にあるmodelとreasoning effortを指定する。
一つの会話を最後まで進めてから次へ移り、モデル間で会話を引き継がない。

別ターミナルから同じ`--run-dir`を指定して記録する。

```bash
python dev_tools/gpt_player/experiment.py open --run-dir RUN_DIR --player-agent-id AGENT_ID
python dev_tools/gpt_player/experiment.py say --run-dir RUN_DIR --text-file PLAYER_UTTERANCE.txt
python dev_tools/gpt_player/experiment.py snapshot --run-dir RUN_DIR
python dev_tools/gpt_player/experiment.py finish --run-dir RUN_DIR --reason max_turns
```

GPTへ渡すのは共通`player_prompt.txt`と各条件の`player_input.json`だけ。親は返答を添削せず、そのまま発言ファイルへ保存する。
`open`の`--player-model-reported`には実行基盤が返すモデル識別子を記録し、分からなければ既定の`unreported`を維持する。
送信結果が不明なときは自動再送せず、snapshotと記録を確認する。`finish`の理由は自然終了、最大会話数、失敗、中止を分ける。
窓口はその実験を起動したターミナルでCtrl+Cにより終了する。通常のドギドやモデルサーバーを停止しない。

`events.jsonl`は発言・返答・時間・結果、`summary.json`は原句と確定句・未採用案を保持する。
ドギドの内部プロンプトは分析用ファイルへ保存し、GPTプレイヤーには渡さない。

```bash
python -m unittest discover -s dev_tools/gpt_player -p 'test_*.py'
```
