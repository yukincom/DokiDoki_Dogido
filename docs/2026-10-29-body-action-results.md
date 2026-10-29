# 身体行動の実行結果の引継ぎ（パート7）

2026-10-29。編集完了。テスト・ビルド・実モデル確認は未実施。

既存の身体行動の選択履歴（最大8件）へ `execution` を追加した。
命令IDと身体世代で同じ選択を照合し、結果待ち・照合済み終端・コードによる取消・期限切れをその一件へ反映する。
`stay` は命令を出さないため `not_requested` とする。adapterの受付だけでは `pending` を終えない。
成功を認める条件、命令の配送、到着による再訪抑制、世代変更時の履歴破棄は既存処理を使う。

モデルへは命令ID・身体世代を外して、行動・対象・選択理由・発行時刻・実行結果を渡す。
環境判断では `body_options.recent_choices` と `body_options.current_body`、通常返答では共通観測の `body_action_history` と `current_body` を使う。
環境判断に身体候補を添える際は共通観測側の同じ二項目を除き、重ねて渡さない。
`current_body` のmode・activity・reasonは受理済みの新鮮な観測で、履歴から推測しない。

本人からの明示操作は既存 `body::requests::State` が直近一件だけ表示用に残し、共通観測の `body_operation` へ渡す。
受付は `queued`、正しい命令・入力世代等の照合を通った結果は `succeeded / failed / rejected / expired`、結果待ちの取消は `cancelled`。
所有者・世界・身体が一致しない記録は次の身体へ渡さない。呼び寄せでwarpした成功は、既存の成功照合を通った結果のepochへ結び直す。
これは表示用の記録で、命令の再実行や新たな到着判定には使わない。呼び寄せの台詞生成は変更しない。

説明用のモデル材料例（実行採取ではない）：

```json
{"body_options":{"current_body":{"mode":"follow","activity":"holding","reason":"within_follow_distance"},"recent_choices":[{"action":"inspect","label":"ウシ","issued_at":"2026-10-29T08:00:00Z","execution":{"status":"failed","detail_code":"path_unavailable","executed_at":"2026-10-29T08:00:01Z","source":"adapter"}}]}}
```

取消・コードの期限切れは `executed_at: null, source: service`。取消を物理停止と説明しないよう、既存の身体観測プロンプトに各状態の意味を添えた。

変更ファイル：

- `dogido-rust/src/body/autonomy/state.rs`：既存8件の選択履歴へ結果を反映、現在活動と表示用履歴を投影。
- `dogido-rust/src/body/requests.rs`：既存の明示操作受付と終端から直近一件を投影。
- `dogido-rust/src/dialogue/body_runtime.rs`：通常返答・環境判断の共通材料へ接続。
- `dogido-rust/src/dialogue/body_interest_runtime.rs`：当回の身体候補と現在状態・履歴を併記。
- `dogido_server/llm/companion_prompts.json`：結果待ち・成功・失敗・取消と現在状態の読み分け。

テスト部屋の確認対象は既存パート7に加え、期限切れ、明示注視による自律注視の取消、別身体・世界へ古い明示操作が漏れないこと、通常返答の自律履歴、環境入力に同じ履歴を重ねないこと。
