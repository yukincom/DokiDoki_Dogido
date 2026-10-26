# 同じ入力の再解析・再構築・再読込を減らす計画

2026-10-27作成。状態: **計画確定・実装前**。開始点は書類側 `main` の `3ef8ba4`。

## 目的と判断基準

[Minecraft観測の取得共有](2026-10-26-minecraft-observation-dedup-plan.md)に続き、同じ処理内で変わっていないデータの重複処理を整理する。対象は以下の6件。一件ずつ実装・検証・docs更新・コミットを完了してから次へ進む。

「安全確認」という名前や非同期境界の存在だけで再処理を残さない。対象が変化する経路、検査が防ぐ具体的な取り違え、既に取得した値を共有できる区間を確認する。命令の対象・期限・接続世代、保存時の版照合は、それぞれ変化し得る状態を実行直前に検査する。同じ不変入力の解析・形式検査と、現在状態の照合を分ける。汎用キャッシュや代替処理経路は追加しない。

## コミット単位

### 1. FabricのHTTP応答を一度解析する

- 対象: `DogidoEventClient` と既存のhotbar・body request・body inspect・body view・screenshot・horse recallのprotocol。
- 変更: 受理した応答を一つのJSON objectへ解析し、各protocolへ渡す。共通ACK集合も一度だけ抽出する。単体の文字列入力APIが必要な箇所は同じ解析処理への入口にする。
- 境界: command固有の型・期限・session等の検証と、client threadでの実行直前確認を維持する。不正JSONや一部の不正commandが他の有効commandの配送を妨げない。
- 確認: protocol・接続世代・ACK・結果再送の既存Gradle試験。複数commandを含む応答、不正JSON、不正な個別commandの回帰を確認。
- docs: adapter READMEの通信責務と本書の結果。
- 予定コミット: `Fabricの応答JSONと受領確認を一度だけ解析する`

### 2. 環境判断内で身体観測の投影を共有する

- 対象: `dogido-rust/src/dialogue/environment_runtime.rs` の `process_environment`、`focus`、`run_light_plan`。
- 変更: 同じdata lock内で同じsessionの `environment_latest` を投影した結果を、危険判断・focus・通常環境判断へ渡す。focusの他の呼出箇所も調べ、既存入口へ統合する。
- 境界: ロック外やモデル待機前の結果を再利用しない。モデル待機後には現在のsession・観測を一度取得する。player側の危険観測と身体側の観測を混ぜない。
- 確認: environment runtimeのRust試験、身体投影・危険・照明・workshop抑止、モデル待機中の状態更新と取消を模擬HTTPで確認。
- docs: 既存の観測共有計画の再構築条件を精密化し、本書へ結果を記録。
- 予定コミット: `環境判断の同一処理で身体観測を共有する`

### 3. 環境発話へ渡す履歴を一度構築する

- 対象: `queue_environment` と `refresh_dialogue_context`、生成開始直前の呼出箇所。
- 変更: 一つの更新処理の中で `model_history()` を一度だけ構築して全actionへ渡す。attach直後の重複付与・上書きを整理する。
- 境界: queue待ち中に増えた確定済み履歴・沈黙・現在観測は生成開始前に反映する。別turnや別sessionの履歴を保持するキャッシュは作らない。
- 確認: 複数actionでの履歴一致、待機中の履歴追加、silent履歴、雨上がり・画像付きactionの材料保持をRust試験で確認。
- docs: 環境反応の既存仕様と本書へ共有単位・結果を記録。
- 予定コミット: `環境発話の履歴材料を更新ごとに一度構築する`

### 4. 想起の条件変更で記憶を読み直さない

- 対象: `dogido-rust/src/haiku_memory.rs` の `search_poems` と `recall_reply`。
- 変更: 記憶の候補読込と検索・整列・重複除去・件数制限を分ける。条件に該当しない場合は同じ読込候補へ無条件検索を適用する。
- 境界: 最初の検索前に全候補を削ったり、別requestまで候補を保存したりしない。削除済み句の除外、最新順、本文重複、revision、案内文は現行どおり。
- 確認: 条件一致・不一致・空記憶・削除済み句・revisionをRust試験と既存memory runtimeの模擬HTTPで確認。
- docs: 記憶・想起の既存仕様と本書へ読込単位・結果を記録。
- 予定コミット: `句の想起で条件を外すときに読込結果を再利用する`

### 5. 本棚の削除句確認で全記録を二度読まない

- 対象: `bookshelf/library_view.rs` と `bookshelf/store.rs` の句の読込。
- 変更: 一覧表示に必要な生存句と削除IDを一つの読込から得る。既存のtrash表示入口も同じ読込処理を使い、不要な削除句本文の投影を避ける。
- 境界: ローカルとSSD snapshotの対応、元の保存先を含むID、未同期編集・削除・復元、SSD不在時の表示を維持する。GETで修復・保存・同期しない。
- 確認: 本棚のSSD表示・未同期編集・削除復元・期限・複数保存元・破損snapshotの既存Rust試験。
- docs: 本棚・アーカイブの既存仕様と本書へ結果を記録。
- 予定コミット: `本棚の句一覧と削除IDを一度の読込で集める`

### 6. 辞書編集の不変入力検証を一度にする

- 対象: `dialogue/reading_runtime.rs`、`reading_correction.rs`、必要なら `catalog_editor` の入力型。
- 変更: 編集入力と静的カタログの形式検証の所有者を一か所に定め、APIと保存処理の二重検証をなくす。直接保存を呼ぶ経路にも未検証入力の抜け道を作らない。
- 境界: ファイルロック後の最新記録読込と `expected_id` 照合は残す。別プロセスによる保存版変更をここで検出する。無効な入力で記録を作らず、正常編集・削除・競合時のAPI応答を維持する。
- 確認: 読み編集のRust試験と `check_reading_runtime.py`、不正入力、直接保存、競合・削除の確認。
- docs: 読み辞書編集の既存仕様と本書へ検査担当・結果を記録。
- 予定コミット: `辞書編集の入力検証を保存入口に集約する`

## Git・検証・完了条件

- この計画書だけを先にコミットする。以降は各段階の全差分とstaged差分を確認し、対象コード・必要なテスト・docsだけを明示してstageする。
- 開始時の未追跡 `docs/2026-10-23-Grok4.5-CodeReview-compat-deletion-targets.md` は別作業。変更・stageしない。
- 作業場所は書類側 `DokiDoki-Dogido` のmain。push・tag・配布・常駐service再起動は対象外。
- 関連試験後、全段階の統合としてFabric `test build`、Rust `test --all-targets --locked` とrelease build、変更対象の模擬HTTPを実行する。失敗は原因と影響を記録する。
- 自動検証と実Minecraft・実音声・速度測定を区別する。未計測の高速化率は記載しない。

## 進捗と検証結果

| 段階 | 状態 | 検証・補足 |
|---|---|---|
| 計画 | 作成済み・コミット前 | 実装変更なし |
| 1 応答解析 | 未着手 | |
| 2 身体投影 | 未着手 | |
| 3 履歴材料 | 未着手 | |
| 4 想起 | 未着手 | |
| 5 本棚 | 未着手 | |
| 6 辞書編集 | 未着手 | |
