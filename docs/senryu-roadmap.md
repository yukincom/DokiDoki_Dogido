# 川柳・Senryu ロードマップ

**更新:** 2026-10-04  
**状態:** 発句・カタログ直引き・JSONL記憶・共同編集はRust本体へ接続済み。実機での品質評価と、将来の拡張を分けて整理する。

関連: [完成度の方針](companion-maturity.md) · [川柳アーキテクチャ](haiku-architecture.md) · [共同編集](haiku-player-improvement-plan.md) · [記憶](memory-architecture.md) · [カタログ利用とRAG](senryu-rag-plan.md)

## 1. 実装済みの機能

| 機能 | 現在の範囲 | 詳細 |
|---|---|---|
| 材料と出典 | 現在観測とカタログの説明・詩語を取り出し、行の根拠として扱う | [カタログ利用](senryu-rag-plan.md) |
| 発句 | 見どころ→scene→三行生成。出典・重複・音数・制約を検査し、内容の再生成は最大6回 | [生成方式](haiku-architecture.md) |
| 保存 | 完成した発句を自動保存し、元句を保持してrevisionを追記する。保存成功と実再生成功は別に扱う | [記憶設計](memory-architecture.md) |
| 共同編集 | 意味の説明、行検査、修正提案、本人の局所編集、採否、終了、戦闘中断と再開 | [workshop](haiku-player-improvement-plan.md) |
| 好みの反映 | critiqueとsoft lessonを保存し、期限・発句回数・明示的な緩めで扱う | [workshop](haiku-player-improvement-plan.md) |
| 明示的な想起 | 本人の照会時だけ句を検索する。日付は壁時計、場所はカタログのラベル・読み・groupで扱う | [フィードバック](haiku-feedback-plan.md) |
| 読み辞書 | あんちょこ画面で登録・更新・取消し、保存済み訂正を次の読み・生成材料へ反映する。会話を辞書の保存命令にはしない | [API仕様](adapter-api.md#26-あんちょこと読み訂正) |
| 保存句の一覧と相談 | 専用テキスト相談室で句を選び、日時・情景・採用済み本文を見ながら同じworkshop処理を使う | [テキスト相談室](../dogido-rust/workshop-text.md) |

実装先は`dogido-rust/src/haiku/`、`haiku_record.rs`、`haiku_memory.rs`、`workshop_*`と各`dialogue` runtime。旧Pythonのmixin分割は現在の未着手項目ではない。

## 2. 保存先と読み戻し

保存ルートは設定の`memory_dir`を使う。以下の`<memory_dir>`はその設定値であり、固定のフォルダ名ではない。

```text
<memory_dir>/
  long_term/
    catalog_corrections.jsonl
    haiku_critiques.jsonl
    haiku_lessons.jsonl
    player_profile.json              … 存在する場合に参照
    haiku_entries.jsonl              … 既存の直下記録も読取り対象
    haiku_revisions.jsonl
  sessions/<session_id>/
    short_term/current_session.jsonl … 完成した発句の記録
    long_term/
      haiku_entries.jsonl
      haiku_revisions.jsonl
      haiku_workshop_turns.jsonl      … 相談の評価記録
```

通常の発句・revisionはセッション配下へ保存する。テキスト相談室からのrevisionは、選んだ句の元の保存先へ追記する。一覧と想起は直下とセッション配下の句を読み、移行を理由に既存記録を移動しない。

`GET /api/v1/memory/haiku`は採用済みrevisionを反映した一覧を返す。相談の評価記録は会話・発句へ読み戻さない。一般起動と専用起動では保存先を明示的に変えているため、[起動案内](../dogido-rust/README.md)で使用中の設定を確認する。

## 3. 次に確認すること

機能があることと、実プレイで自然に使えることは分ける。現在の優先順位は[実機チェック](../dogido-rust/manual-dialogue-check.md)に沿って次を確認すること。

1. 観測した材料が句と説明に届くか。地下・天候・乗車・音などの取り違えを記録する。
2. 本人の局所編集、AI案の採否、保存、戦闘中断と再開を一巡する。
3. 読み・音数・出典の説明が検査結果と一致するか、返答の自然さと待ち時間を確認する。
4. 保存後に一覧・想起・テキスト相談から同じ句と採用結果を参照できるか確認する。

過去のPython版や独立したモデル試験の結果を、変更後のRust本体の実機確認済みへ読み替えない。個々の結果には実行した版・入口・入力・状態変更・表示／音声の結果を残す。

## 4. 未実装・今後の候補

| 項目 | 現在との違い・条件 |
|---|---|
| マイクラ句集UIの拡張 | 基本の一覧と相談はある。画像との紐付け、期間・作者等のフィルター、お気に入り、削除、エクスポートは別の将来案 |
| あんちょこの記憶表示 | カタログ閲覧・検索・読み編集はある。critique／lessonや関連句を項目へ紐付ける表示は将来案 |
| カタログの説明・読みの充実 | 仕組みの追加より、実際に不足した項目の内容を整える |
| 観測ギャップの改善 | [観測メモ](bug-player-chat-observation-gaps.md)・[音の仕様](sound-identity-plan.md)の各項目を、現在の実測に照らして判断する |
| Vector RAG | 未実装・任意。直引きや内容追加で解決できない用途だけを対象にする |
| M5Stack・外部メッセージ・OS連携 | 対話と川柳が安定してから検討する |

画像付きの冒険記録やOS連携の具体案は[将来構想](future-assistance-and-senryu-app-plan.md)にまとめる。クラウド同期やソーシャル投稿を必須にはしない。

## 5. 継続する設計方針

1. 履歴句を発句プロンプトへ常駐させない。明示的な想起かUIで参照する。
2. 読み訂正はカタログのオーバーレイ、句の直しはrevisionとして分ける。
3. 日付の絞り込みには壁時計の`created_at`を使い、ゲーム内の昼夜を使わない。
4. 本人の好みはsoft lessonに留め、道具・読みのhard制約へ混ぜない。
5. 発句・戦闘優先・採否・保存はコードが所有し、汎用エージェント基盤へ移さない。

## 6. 検証の入口

材料・準備・記憶の自動検証は、リポジトリルートから実行する。

```sh
./dogido-rust/cargo.sh test --locked --test haiku_context --test haiku_preparation --test haiku_memory
```

全体の自動検証と模擬通信は[Rust本体の案内](../dogido-rust/README.md)、実モデル・Minecraft・音声を使う確認は[実機チェック](../dogido-rust/manual-dialogue-check.md)を参照する。
