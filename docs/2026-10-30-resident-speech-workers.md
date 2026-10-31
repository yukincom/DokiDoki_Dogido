# 音声認識と読み辞書の常駐

2026-10-30。発話ごとのモデル初期化、返答ごとの辞書初期化を省く。

## 所有期間

- Whisperは`voice-input`の起動時に一度ロードする。接続待ち・`voice off`でもモデルを保持し、録音許可と寿命を分ける。
- 発話の取消はその推論だけに渡し、応答を最後まで回収する。モデルを次の発話へ使い回し、音声入力プロセスの終了で停止・回収する。
- 実行中のモデルが異常終了した場合、または既存の60秒期限を超えた場合は回収し、次の発話で再ロードする。
- UniDicは`Dialogue`の専用workerで共有する。返答取消は呼出側へ直ちに返し、辞書workerが旧応答を読み切る。新しい返答へ旧応答を渡さない。本体shutdownでworkerを回収する。
- 読み訂正は返答ごとに従来どおり読み直す。自動発句の生成単位のtoken helperは今回の変更対象外。

## 維持する振る舞い

発話区切り、RMS、Silero VAD、Whisperのbeam search・best-of・日本語指定・no-speech閾値、空認識時の既存再試行、本文処理、受付世代、配送の条件は変更しない。新しい音声・意味・語句の検査や拒否条件を追加しない。Whisperの以前の文字文脈は次の音声へ持ち越さない。

常駐補助は既存whisper.cppの同じ共有ライブラリを使う。stdin/stdoutだけで要求と取消を受け渡し、追加HTTPサービスは作らない。`native/whisper_worker/main.cpp`を`dogido-rust/scripts/build_whisper_worker.py`でコンパイルする。出力先は`dogido-rust/target/release/dogido-whisper-worker`。家庭用セットにも既存の依存ライブラリとともに同梱する。

## 確認範囲

- Rust音声処理の単体試験と、`tests/python/check_voice_runtime.py`の実プロセス・模擬通信試験で、起動時常駐、複数発話、取消、休止・再開、分割された応答の途中取消、終了時の子回収を確認する。
- native helperは`test_whisper_worker_native.py`でWhisper APIを模擬し、一度のロード、入力ごとのprompt・閾値、取消後の再利用、EOFでの解放、非ゼロ結果の部分本文を確認する。
- 読み辞書は返答間の同一PID、取消後の応答分離、shutdownでの回収を模擬辞書で確認する。
- 固定の同梱音声を使う実Whisper試験と、Minecraft・マイク・再生を合わせた実運用確認は区別する。

実装時の確認では、音声の模擬通信38件、音声のRust単体15件、常駐nativeの模擬4件、辞書の単体5件を通過。実Whisper（既存Kotoba q5_0・Metal）も、固定サンプルの連続認識、取消後の同一PIDでの再利用、shutdown終了を確認した。3秒の同梱サンプルでは旧CLI起動込み575ms、常駐後261〜275ms、起動時ロード584msだった。初回Metal準備が冷えている回はロードに約5.9秒かかった。認識文には繰返し間の差もあり、この試験は精度や実際の会話全体の速度を保証するものではない。実Minecraft・マイク・再生は未確認。

## 分類器とLLM

BERTは同日の追加変更で、入力の取消とモデルの寿命を分離した。新しい別入力、明示停止、受付モードや入力世代の変更等で分類が不要になっても、開始済みの通信は同じfutureで完了させ、結果だけを破棄する。新入力は受け付け、両モデル共通のMutexで合計1推論を守って次へ進む。再ロード中の取消もモデルを保持し、その旧入力の推論は始めない。本体shutdown・既存timeout・通信異常では従来のworker回収を維持する。新しい入力・意味・品質の検査や拒否条件は追加していない。

モデル差し替え手順は `dogido-rust/scripts/request_classifier_worker.py` 冒頭と `src/dialogue/request_runtime.rs` のSettingsコメントに記載した。同じラベル契約のModernBERTなら、新しい採用記録・checkpoint・tokenizerを別ルートへ配置し、対応する `DOGIDO_REQUEST_CLASSIFIER_*_MODEL_ROOT` を変更して本体を再起動する。Rustの再ビルドは不要。

BERT追加変更の検証は模擬workerによる共通受付18件、通信・回収6件、Python worker17件（10 subtests）を通過した。共通受付の既存4件は変更前・変更後とも同じ箇所で失敗したため、今回の範囲で修正していない。対象は `playing_reply_queues_one_classification_then_dispatches_each_operation_once`、`completed_senryu_queue_waits_for_same_verse_without_another_inference`、`queued_normal_input_is_cancelled_when_workshop_opens`、`same_text_during_combat_does_not_deduplicate_into_old_senryu_input`。実BERTでの取消後再利用・速度、およびMinecraft実操作は未確認。

Whisper/BERT常駐化の段階ではLLM設定・プロンプトを変更していない。共有生成サーバーの直近31要求では入力処理の中央値4.607秒、生成の中央値0.367秒だったが、ドギドの各操作とは未照合。プロンプトキャッシュとMTPは既に有効。固定の指示を前方、可変情報を後方へ寄せて前方一致の再利用を増やす案を検討する。実モデル比較前には変更後プロンプト全文を提示する。

同日の続きで、文面・情報を保持したまま固定指示を前へ移す変更を実装した。実モデルは未実行で、[変更後の全文とオフライン確認](2026-10-30-prompt-prefix.md)をレビューする段階。
