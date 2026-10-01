# Rust の通常会話入力投影

通常会話の `PlayerInputContext` は Rust の `input_context` が作る。原文の句本文・改行と、正規化済みの分類面を分離し、slash command は分類前に返す。剣の明示依頼、知識質問、純粋な入力規則、句の想起条件を既存の優先順位で組み合わせる。読み訂正の `wrong_reading` がある場合は `explicit=true` を維持する。

Python の `input_helper.prepared_context` は現在入力との対応と閉じた型を検証し、dataclass・tuple・datetime に変換するだけで、入力parserを呼び直さない。保存用の読み訂正parser、原文根拠、現在句の CAS、操作・保存の採否は変更しない。

`Context::general_conversation()` は従来の宛先確認と同じ所有権判定を共有する。`address_route` はこの値を Rust 内で返し、Python helper やモデルを起動しない。知識検索・国語対話・Webの後段処理は変更しない。

入力用の想起条件は group IDs とローカル時刻の型を保持する。保存検索用 `RecallQuery` の既存の UTC・展開済みバイオームの契約は維持する。比較は Python 正本由来の純粋分類 1,586 例と context 全23項目・宛先判定 1,596 例を使う。実機・モデル・音声の試験ではない。
