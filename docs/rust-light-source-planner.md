# 照明コメントの Rust 判定境界

照明器具が増えた際の `light_source_comment_plan` は、Rust の `light_plan` が既存の会話モデルへ直接送る。プロンプト、JSON 契約、追加の契約説明、採否状態を Python 正本と照合する。実行時に Python helper は起動しない。

- モデルは会話設定の値を使い、温度 `0.0`、上限 `160` tokens、thinking 無効を維持する。
- JSON を受け取れない場合と初回生成失敗は無言へ戻す。JSON 契約に一致しない場合だけ同じ入力で一度再試行し、再び不成立なら棄権する。完結した正しい JSON は `finish_reason=length` でも受け入れる。
- 全体の待機上限は従来どおり 95 秒。取消・所有者終了・時間切れで生成待機を破棄し、後続の再試行を送らない。生成終了理由とトークン数は既存形式の `llm_reports` に保持する。
- 半スタック、現在の暗さ、5 分の重複抑止、現在観測との再照合、発話 confidence `0.78` 以上の条件は既存の `Ambient` が所有する。低 confidence は契約の再試行理由にしない。台詞生成へ正確な所持本数を渡さない境界も維持する。

検証は Python 正本由来の 384 文脈のプロンプト、516 契約ケースと再試行文、17 生成シナリオで行う。既存 Rust のイベント消費側の採否とも比較し、取消・期限・最大 2 回の呼出をモデルなしで確認する。実 Minecraft、実モデル、マイク、音声再生はこの検証には含まない。

正本変更時は `python dogido-rust/scripts/generate_light_plan_fixtures.py` で生成物を更新し、`./dogido-rust/cargo.sh test --locked light_plan --lib` と既存の `environment::ambient::tests` を実行する。Python の照明 helper は比較用の正本として残るが、Rust の `op=light_plan` はその前で分岐する。
