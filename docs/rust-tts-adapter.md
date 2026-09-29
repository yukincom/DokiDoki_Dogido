# Rust の自由文読みと最小 UniDic 補助

反応音声（`reaction_runtime`）と記憶の想起応答（`memory_runtime::recall_poems`）は、自由文の読みのためだけに `workshop_helper.py` を起動しない。Rust の `tts_reading::prepare` が空・off・現行範囲の漢字なしと判断した場合は補助プロセスを起動せず、従来の順序付き例外表だけを適用する。

UniDic が必要な場合だけ `tts_unidic_adapter.py` を一回の読み処理の間起動する。Python の担当は既存 fugashi の初期化・一度の warmup・token feature の取得に限る。Rust が優先読み、語種（和・混）、除外 POS、kana／pron の選択とかな変換、最後の例外表を担当する。表示原文は変更せず、派生した `spoken_text` のみを音声へ渡す。カタログの読み overlay は送らず、自由文へ適用しない。

SDK 初期化失敗と解析途中の例外は、部分 token を捨て、原文全体への例外表処理に戻す。成功した空 token 列は空の成功結果として保つ。不正 IPC、異なる request ID、取消、期限切れ、子プロセス異常終了は成功 fallback に変換しない。取消と所有者終了を起動前・完了後にも確認し、所有する子を終了・回収してから戻る。

反応の全体95秒、読みの残り時間と35秒の小さい方、IPC一件15秒の上限を維持する。想起の読みは旧二往復分の30秒以内とし、新 adapter 一往復には15秒の IPC 上限がかかる。正常終了の待機と強制回収は既存の有界 `Helper::finish` を再利用する。常駐化は導入せず、通常会話や workshop の共有 helper の寿命は変えない。

正本比較は前後処理5,376ケース、token選択7,730ケース。辞書 mock は初期化・warmup・初期化失敗の記憶・途中失敗・空結果・完全な IPC を確認する。Rust は辞書不要時のゼロ起動と、成功・辞書失敗・不正応答・取消・期限・異常終了における実子回収を検証する。reaction／recall の結合テストでは原文、保存済みの記憶、生成レポートが読み処理によって変わらないことも確認する。

通常 chat／workshop の他処理とそこで使う既存の読み op、句の確定読み・normalize／correct・signature はこの変更の対象外。実 Minecraft、VOICEVOX、実音声、新 adapter と実 UniDic の結合試験は未実施。既存 Python TTS テストの導入済み UniDic 検証は成功したが、新 adapter の実機検証とは区別する。

例外表の並びは正本どおりに保持している。例えば off の「今朝は」「今朝の」は先行する「朝は」「朝の」の置換を受ける。移植と同時の読み方変更は行っていない。
