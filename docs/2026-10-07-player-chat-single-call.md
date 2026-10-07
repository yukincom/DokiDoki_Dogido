# 2026-10-07 通常対話の一回生成と検証結果

通常 `player_chat` の対象選択と本文生成を一回に統合した。前段planner、返答の意味・観測整合検査、不合格候補の言い直し、定型fallbackは本体経路から撤去した。観測・履歴を入力へまとめる既存の責務と、川柳・操作・戦闘の経路は維持する。実装仕様は [通常会話の一回生成](rust-chat-grounding-boundary.md)。

## 呼出し回数と結果

実HTTPに接続する模擬モデルで、受付から履歴・音声までを確認した。

| 場面 | モデル呼出し | 確認結果 |
|---|---:|---|
| 通常の返答・対象への問い | 1 | 対象選択専用の呼出しなし |
| 短い相槌・英語・観測と不整合な本文 | 1 | 内容を理由に棄却・再生成しない |
| 同じ文を繰り返す壊れた出力 | 1 | 音声0件、壊れた本文は履歴へ入れない |
| `{"action":"speak"}` | 1 | 本文を空として扱い、音声なし |
| `{"action":"speak","speech":"そや`、打切り | 1 | JSON末尾を補完し、受信した「そや」を使う |
| `{"action":"speak","speech":123}` | 1 | 本文を文字列「123」へ補正 |
| 正本知識の明示照会 | 0 | 既存の知識経路で回答 |
| 生成中の取消 | 開始済み1 | 再呼出しなし、返答・沈黙の履歴なし |

不成立の出力では「難しい話だったので、ドギドは沈黙を選んだ」を短期の出来事として記録する。次のプロンプトにはこの記録が届き、破損本文は届かない。出来事はassistantの発話・再生済みの会話一往復・川柳材料に数えない。モデルが明示的に選んだ沈黙は既存の記録を使う。取消・古いepoch・再生失敗・全体期限超過の失効処理は維持する。

川柳や国語対話から雑談へ戻る場合は、前段処理とは別に雑談の一回がある。この検証結果を川柳全体の呼出し回数1回という意味にはしない。

## 自動テスト

- Rust単体テスト: 477件成功。
- Rust結合テスト: 125件成功。下記の既存不一致1件を明示的に除外して残りを完走。
- 模擬通信試験: 下表10本・80項目成功。知識rendererの31契約も成功。
- releaseビルド: `--offline --locked` で成功。
- 今回の変更対象の `git diff --check`、Python検証スクリプトの構文検査: 成功。

| スクリプト | 成功項目 |
|---|---:|
| `check_chat_native.py` | 8 |
| `check_shared_dialogue.py` | 5 |
| `check_dialogue.py` | 11 |
| `check_pet_runtime.py` | 7 |
| `check_shared_story.py` | 3 |
| `check_history_retention.py` | 5 |
| `check_address_runtime.py` | 8 |
| `check_workshop_runtime.py` | 16 |
| `check_knowledge.py` | HTTP 7、renderer 31 |
| `check_vocalization_runtime.py` | 10 |

初回に失敗した新しい入力テストは既存160字投影の期待値へ修正した。通信試験の旧2回生成・固定回答の期待を新仕様へ更新し、既存の戦闘出来事の短期ログ、読み補正後の音声表記、worker終了通知との競合も検証側へ反映した。上表は修正後の最終結果。

### 残っている既存の不一致

全対象の実行では `haiku_preparation::canonical_raw_catalog_entries_are_not_fixture_backed` が失敗した。`items/nether_portal` の現行資料に `notes` があり、保存済みfixtureにはない。テスト・fixture・カタログreader・元資料が作業開始時の `5ec0705` と同一であることを確認した。今回この川柳用fixtureや資料は変更していない。

```sh
./dogido-rust/cargo.sh test --offline --all-targets
# 上記は既存のポータルfixture不一致1件で終了。
./dogido-rust/cargo.sh test --offline --test '*' -- --skip canonical_raw_catalog_entries_are_not_fixture_backed --quiet
./dogido-rust/cargo.sh build --release --offline --locked
python3 dogido-rust/scripts/check_chat_native.py --binary dogido-rust/target/debug/dogido-rust
```

## 確認範囲

HTTPのモデル・TTS・再生playerは模擬。実Gemmaでの返答品質、実Minecraft、マイク、スピーカーは未確認。実サービスの再起動やリモートへのpushは行っていない。起動中のプロセスへ新しい実行ファイルが反映されたという確認でもない。
