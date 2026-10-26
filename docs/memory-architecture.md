# 記憶アーキテクチャ

**更新:** 2026-10-26
**状態:** Rust本体における短期対話履歴・句の保存機能は実装済み。対話・共有済み出来事のあらすじを「旅の記録」に保存する。評価用エピソードログとは区別する。参照APIおよび評価ログの詳細は [Rustの記憶API](rust-memory-api-and-episodes.md) を参照。

現行の主要な実装先は、[短期履歴](../dogido-rust/src/dialogue/history.rs)、[発句保存](../dogido-rust/src/haiku_record.rs)、[句の検索・採用履歴](../dogido-rust/src/haiku_memory.rs)、[記憶API](../dogido-rust/src/memory_api.rs)。

## 1. 基本方針

ドギドの記憶モデルは、大きく `短期記憶` と `長期記憶` に分離して管理する。

- **短期記憶**: プレイヤーとの直近のやり取りを参照するための会話文脈（ターン履歴）。
- **長期記憶**: 保存された川柳、プレイヤー自作句、添削（revision）履歴、講評（critique）、ソフト制約（soft lesson）。冒険のあらすじ・日付・句へのリンクを残す「旅の記録」は本棚で閲覧する。
- **判断との分離**: 戦闘判定、Mob反応、暗さ判定、川柳生成の内部判定は既存の状態機械・コードが所有し、記憶を状態機械の判定正本としては使用しない。
- **履歴への反映基準**: 音声の実再生完了または表示確認を受けた返答・固定警告を短期履歴へ保持する。川柳本文・相談・記憶操作は共有の出来事として扱い、川柳の正本は既存の発句記録へ保存する。
- **保存・読み取りの主体**: すべての保存・読取ロジックは決定論的なコード（Rust）が担当し、汎用エージェント基盤（Hermes 等）には委譲しない。
- **閉じたアーキテクチャ**: 将来的なLLMワークフロー拡張時も、特定の汎用エージェント基盤を前提とせず、型定義された閉じた route と厳密なコード検証で構成する（JSONL を正本とする保存形式は不変）。

### 1.1 単一プレイヤー前提（現状）

設定の`memory_dir`は**論理ユーザーが1人**の前提に近い。句はセッション単位で保存するが、検索・lesson・profileの参照を利用者別に分離していない。  
家庭内の複数人・呼び名の違い・将来のプロファイル切替は未分離。  
課題と段階案は [multi-user-tenancy.md](multi-user-tenancy.md)。

### 1.2 何を覚えるか（キャラクター）

通常の会話文脈は接続内で使い、保存用の「旅の記録」を次の接続へ自動注入しない。

旅の記録はあらすじと、終了時に10往復以下だった未要約のやりとりを保持する。観測の全量ログではない。

実装済みのcritique・soft lessonと、検討中のチェックポイント記憶は別に扱う。後者の案は[haiku-workshop-checkpoint-plan.md](haiku-workshop-checkpoint-plan.md)（Issue #37）。

`<memory_dir>/eval/episodes.jsonl` は名前に episode を含むが、キャラクターが覚える長期記憶ではない。誤反応分析と将来の支援アクション監査に使う評価ログであり、`MemoryStore` から読まず、会話や川柳生成へ注入しない。仕様は [支援アクションの操縦席 §A](assist-action-architecture.md#a-エピソード-jsonl実装済み) を正とする。

## 2. 保存形式

正本は JSON / JSONL とする。

保存ルートは設定の`memory_dir`。下の`<memory_dir>`は説明用の表記であり、固定のフォルダ名ではない。

```text
<memory_dir>/
  sessions/<session_id>/
    short_term/current_session.jsonl # 完成した発句の記録。通常会話の永続ログではない
    long_term/
      haiku_entries.jsonl
      haiku_revisions.jsonl
      haiku_workshop_turns.jsonl     # 相談の評価記録。会話・発句へ読み戻さない
  long_term/
    haiku_critiques.jsonl
    haiku_lessons.jsonl
    catalog_corrections.jsonl
    haiku_entries.jsonl             # 既存の直下記録も参照する
    haiku_revisions.jsonl
    journeys/<journey_id>.json     # 全あらすじ・短い未要約対話・日付・句リンク
    player_profile.json            # 既存ファイルの参照のみ
  bookshelf/state.json            # 既存の個人メモ。同じ保存先に旅の記録メモも保持
  short_term/rolling_summary.json   # 既存ファイルの参照のみ
  eval/episodes.jsonl               # 評価記録。会話・川柳記憶へ読み戻さない
```

通常の発句・revisionはセッション配下へ保存する。テキスト相談室で採用したrevisionは、選択した句の元の保存先へ追記する。句の一覧・想起は直下とセッション配下を読み、既存記録を自動移動しない。

### JSONL を使うもの

- 時系列で追記するログ
- 川柳エントリ
- 添削履歴（`haiku_revisions.jsonl`。Hermes 前提の自動整理はしない）
- 評価用の決定記録（`eval/episodes.jsonl`。記憶として読み戻さない）

### JSON を使うもの

- 既存のプレイヤープロフィール・達成度（参照APIのみ）
- 旅の記録（本体が生成・保存、本棚が閲覧。メモは本棚から明示保存）
- 既存のrolling_summary（参照APIのみ。起動時の会話復元は行わない）

YAML は正本にしない。手書きには便利だが、ログ追記・改行・引用符・日本語テキストの扱いで壊れやすい。

Markdown は正本にしない。人間が読む日記・共有文・UI エクスポートとして後から生成する。

## 3. 短期記憶

### 通常会話の文脈（実装済み）

通常会話はセッション内のメモリ上に持ち、未圧縮の対話は約20ターンを目安に保持する。20ターン揃った次の入力で、古い10ターンを約40文字の冒険のあらすじへ置き換える。あらすじ自体は再要約せず、会話には直近5件だけを渡す。それ以前のあらすじも冒険の記録用に保持する。直近の対話本文は80文字で切らない。プレイヤー入力と、音声の実再生完了またはテキスト表示が確定したドギドの返答・環境反応を扱う。取消・失敗を発話済みのassistant行にしない。

モデルが選んだ沈黙は`role=event, reaction=silent`として別の行に残す。実際に話した返答や、川柳へ渡す会話の一往復には数えない。本人の訂正は元のplayer行を残して注記する。詳細は[会話修復](conversation-repair.md)。

川柳の準備・相談・戦闘中断中は圧縮せず、モデルへ渡す範囲だけを直近20ターンへ制限する。原文は保持し、WS終了後の次の通常入力で直近10往復より古い分をまとめる。

戦闘前の会話は別に一時退避し、戦闘後のプレイヤー入力を既定3ターン受けた後に解放する。毎tickの生イベント全文や評価ログは履歴へ入れない。接続終了時の未要約分はサーバー終了処理へ引き継ぐ。正常終了時に残りが11往復以上なら一度だけあらすじにし、10往復以下なら原文のまま、日付・その冒険で届けた句へのリンクとともに「旅の記録」へ保存する。残りの要約に失敗した場合は、その原文を保存せず未収録と記録する。次の接続でファイルから会話を復元しない。

### 発句の短期記録（実装済み）

`sessions/<session_id>/short_term/current_session.jsonl`には、完成した発句とその表示・読み・行情報・意図説明・情景を記録する。通常の会話全文を追記するファイルではない。発句の保存成功は、TTSやスピーカーでの再生成功を意味しない。

`interpretation`は生成時の意図説明を保持し、再要約で元の意味を置き換えない。

### 旅の記録（2026-10-26）

1ターンは本人入力と確定応答の一往復。確定した沈黙、配送確認済みの川柳相談・記憶操作も文脈の一ターンとして数えるが、川柳の会話材料には昇格しない。単独の警告・出来事・未回答行は補助枠40行で保持し、完了した対話を押し出さない。

あらすじ生成は既存の会話モデル設定（通常8080）を使い、ツールやOS操作を渡さない。通常入力では未要約本文の圧縮を最大1回。要約同士の統合は行わない。通信失敗・空応答・出力打切り・取消では元の記録を残すため、一時的に20ターンを超える場合がある。次の入力で一度だけ再度扱う。会話本体の返答生成は従来の一回のまま。

確定したあらすじは接続中も保存する。接続ごとに一件の記録とし、正常終了時に未要約分を加える。完了ターンが10以下なら原文を話者・出来事の種別付きで収録し、11以上なら一度要約する。未回答入力は完了ターンに数えず、短い記録の本文には残す。強制終了・電源断では最後の保存済みあらすじまでとなる。「旅の記録」の記録メモは本人が追記する欄で、生成内容とは別に既存の本棚保存APIから保存する。詳細と検証状況は[旅の記録](2026-10-26-journey-records.md)。

### 再起動後の会話復元（未実装）

初期案には「24時間または300〜500件のraw記録」「30〜60分のactive buffer」「200〜300字の復元要約」「20〜50件や無言30分ごとの圧縮」があった。これらは現行Rustの保持量・自動処理ではない。

`rolling_summary.json`のGETは既存JSONを読むだけで、このファイルの自動更新・起動時の会話復元は行わない。新しいあらすじと旅の記録はこの旧参照APIを使わない。

## 4. 長期記憶

長期記憶は句と、その採用・講評・改善の記録を持つ。対話と共有済み出来事のあらすじは旅の記録に保存する。構造化したゲーム進行の節目は第7節の将来案に分ける。

### 入れるもの

- 完成時に自動保存するドギド川柳（記憶が有効な場合）
- 冒険のあらすじ・日付・その冒険で届けた句へのリンク
- プレイヤー自身が作った川柳
- 川柳の添削履歴
- 句の意図説明、critique、soft lesson

### 入れないもの

- 通常雑談の全期間の全文ログ（正常終了時の10往復以下の未要約分は旅の記録へ収録）
- 通常警告の全文
- mob 反応の全文
- 保存されていない短期文脈

記憶が有効なら、完成したドギド川柳を発句の短期記録と長期entriesへ保存する。プレイヤーの保存操作を待つ方式ではない。AIの修正案は採用前にrevisionへ保存せず、本人の採用意思と元句への適用をコードで検証してから追記する。

## 5. 川柳長期記憶

川柳長期記憶は、上記の保存先の`long_term/haiku_entries.jsonl`に保存する。以下のJSONは主要項目の説明例。ドギド句の例では表示・読み・三行の正本を省略している。

### 保存するフィールド

`surface_text`・`reading_text`・`lines`はドギド生成句の項目。プレイヤー句は入力本文を`text`へ保存し、未入力の読みや行出典を補作しない。

- `id`: 内部識別子。句の選択・添削履歴の紐付けに使う
- `created_at`: ドギド句は生成完了時刻、プレイヤー句は保存入力の基にした観測の`observed_at`。ファイルへの追記時刻とは限らない
- `author`: `dogido` または `player`
- `kind`: `agent_haiku` または `player_haiku`
- `text` / `surface_text`: 漢字・カタカナを含みうる表示三行（`text` は互換フィールド）
- `reading_text`: TTS・音数・CASに使う確定ひらがな三行
- `lines`: 各行の安定ID・位置・表示・読み・出典・provenanceを束ねた3件。表示と読みを別の句として保存しない
- `interpretation`: 川柳の読解・意図説明。プレイヤー川柳の現行保存では`null`
- `world`: 生成または保存時の Minecraft 文脈
- `trigger`: 元イベントに紐づく最小情報

`saved_at` は持たない。
保存時刻用の別フィールドはなく、上記の`created_at`を記録する。

`irony_kind` のような分類は持たない。
分類軸は増え続けるため、必要な意味は `interpretation` の文章に残す。

### ドギド川柳の例

```json
{
  "id": "hk_20260611_172701_4919",
  "created_at": "2026-06-11T17:27:01+09:00",
  "author": "dogido",
  "kind": "agent_haiku",
  "text": "ゆきのこもれ\nやみにはかぶる\nそらのとびら",
  "preface": "ここで一句。",
  "interpretation": "雪のタイガの冷たい夜、手元のエンダーポータルフレームが、遠くの地底で探すはずのダイヤとは異なる、別の次元への扉を暗示している。",
  "world": {
    "biome": "snowy_taiga",
    "structure": null,
    "time_phase": "night",
    "dimension": "minecraft:overworld"
  },
  "trigger": {
    "event_sequence": 4919,
    "route": "haiku"
  }
}
```

### プレイヤー川柳の例

```json
{
  "id": "hk_20260611_174205_player",
  "created_at": "2026-06-11T17:42:05+09:00",
  "author": "player",
  "kind": "player_haiku",
  "text": "ダイヤより\n土の階段\nありがたい",
  "preface": null,
  "interpretation": null,
  "world": {
    "biome": "dripstone_caves",
    "structure": null,
    "time_phase": "night",
    "dimension": "minecraft:overworld"
  },
  "trigger": {
    "event_sequence": 5031,
    "route": null
  }
}
```

### バイオームとストラクチャー

`world.biome` と `world.structure` は Minecraft ID を保存する。

日本語表示名は catalog から引く。
検索 UI では、ID と日本語表示名の両方を検索対象にする。

長期記憶内に `biome_label` / `structure_label` は原則持たない。
ただし、`interpretation` の文章内に日本語表記が含まれる場合はそのまま残す。

## 6. 添削履歴

添削履歴は `long_term/haiku_revisions.jsonl` に保存する。

```json
{
  "id": "rev_20260611_175100_001",
  "created_at": "2026-06-11T17:51:00+09:00",
  "haiku_id": "hk_20260611_172701_4919",
  "source": "generated_confirmed",
  "base_text": "ゆきのよる\nやみにはかぶる\nそらのとびら",
  "parent_revision_id": null,
  "comment": "やみにはかぶる、の意味が少しわかりにくい",
  "edit_contract": "line_compare_and_swap_v1",
  "edits": [
    {"line_index": 1, "expected_text": "やみにはかぶる", "replacement_text": "やみをかぶせて", "atom_ids": ["observation:weather:rain"]}
  ],
  "line_sources": [
    {"line_index": 0, "atom_ids": ["catalog:block:...:note:0"]},
    {"line_index": 1, "atom_ids": ["observation:weather:rain"]},
    {"line_index": 2, "atom_ids": ["catalog:structure:...:note:1"]}
  ],
  "revised_text": "ゆきのよる\nやみをかぶせて\nそらのとびら"
}
```

`source` は `player_feedback`（既定）／`formal`／`conversational`／
`generated_confirmed`／`player_line_confirmed` の5値。AIの修正案をプレイヤーが明示採用した
`generated_confirmed` では、3行すべての検証済み出典を `line_sources` に残す。さらに
`edit_contract` と、元行完全一致条件を持つ検証済み `edits` を残す。保存時にも
`edits` を元句へ適用すると `revised_text` になること、対象外行が変わらないことをコードで再確認する。
固定行の出典が欠ける旧データは材料重複を検証できないため、自動修正案を出さない。

プレイヤー明示語による局所編集は `player_line_compare_and_swap_v1` を使う。editの
`provenance` は `player_explicit` とし、source atom IDを捏造しない。連続編集では
`base_text` を直前の採用句、`parent_revision_id` を直前revisionへ向ける。初回発句の
`original_text` は変えず、各段階のCAS基準を履歴として残す。

ドギド発句とrevisionは、三行それぞれを `line_id / line_index / position / canonical_name / surface_text / reading_text / source_atom_ids / source_atoms / provenance` の一オブジェクトで保存する。revisionには初回の `original_text`（表示）と `original_reading_text`（読み）、各段階の `base_surface_text` / `base_text`、採用後の `revised_surface_text` / `revised_text` を残す。プレイヤー局所編集は対象行の `surface_text` と `reading_text` を同時に差し替え、他の二行をそのまま引き継ぐ。

添削エージェントは、`haiku_entries.jsonl` と `haiku_revisions.jsonl` を読む。
通常の短期ログ全文は読ませない。

## 7. ゲーム進行の長期記憶（初期案・自動更新は未実装）

現行Rustは`long_term/player_profile.json`を参照APIから読む。未作成なら以下の4項目を含む初期値を返すが、GETでは保存しない。ゲームイベントから進行を自動更新する処理は未実装。

対象はゲーム進行に関わるものだけに絞る。

```json
{
  "player_name": "main_player",
  "progress": {
    "story/mine_diamond": {
      "label": "ダイヤモンド！",
      "unlocked": false,
      "first_unlocked_at": null
    },
    "story/enter_the_end": {
      "label": "おしまい？",
      "unlocked": false,
      "first_unlocked_at": null
    },
    "nether/root": {
      "label": "ネザー",
      "unlocked": false,
      "first_unlocked_at": null
    },
    "end/elytra": {
      "label": "空はどこまでも高く",
      "unlocked": false,
      "first_unlocked_at": null
    }
  }
}
```

初期案の記録対象:

- `story/mine_diamond`: ダイヤモンド！
- `story/enter_the_end`: おしまい？
- `nether/root`: ネザー
- `end/elytra`: 空はどこまでも高く

初期案では、達成時の`first_unlocked_at`をタイムスタンプ、未達成を`null`とする。現行の参照APIは過去のJSONの追加属性も保持して返す。

## 8. UIの現在と拡張案

保存句の共同編集は[テキスト相談室](../dogido-rust/workshop-text.md)、閲覧・個人メモ・写真貼り付けと30日復元付き削除は[独立した本棚](2026-10-13-bookshelf-integration.md)にある。本棚はMinecraft停止中も同じ正本を読む。本体の `GET /api/v1/memory/haiku`・`profile`・`summary` と `/catalog` は維持し、UI間の導線は追加しない。本棚の読み訂正は共通の `catalog_corrections.jsonl` に保存し、音声へ反映する。

以下は初期の実装順の記録。達成度保存などの未実装項目と、現在のAPI・テキスト相談室を分けて読む。

1. 長期記憶ファイルを固定する
2. 川柳保存・プレイヤー川柳保存・達成度保存を実装する
3. 一覧取得 API を作る
4. ローカル閲覧 UI を作る
5. コピーボタンを付ける
6. 共有文生成を付ける
7. SNS 共有ボタンを付ける

`/memory`というローカル管理画面は初期案であり、現行Rust本体にその画面のルートはない。

SNS 共有は初期実装では直接投稿しない。
まずは共有文を作ってコピーできるようにする。

## 9. 初期MVPの記録

当初の候補は以下だった。未実装を含む履歴であり、現在の実装済み一覧や次の作業指示ではない。

- 短期記憶 JSONL への発話イベント保存
- rolling summary の読み書き
- ドギド川柳の保存
- プレイヤー川柳の保存
- 川柳意図説明の保存
- 4 種の進行達成度保存
- 長期記憶一覧 API

現行の保存・API・UIの範囲は第2〜8節を参照する。

状態機械の危険判定や発話優先度は、この記憶システムに依存させない。
記憶は会話・保存・振り返りのために使う。
