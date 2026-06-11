# 記憶アーキテクチャ

この文書は、ドギドの短期記憶・長期記憶・川柳保存・ゲーム進行記録の仕様です。

## 1. 基本方針

記憶は `短期記憶` と `長期記憶` に分ける。

- 短期記憶は、プレイヤーとの会話で直近参照するための発話文脈
- 長期記憶は、保存川柳・プレイヤー川柳・添削履歴・ゲーム進行の記録
- コンバット判定、mob 反応、暗さ判定、川柳生成の内部判定は既存の状態機械が担当する
- 記憶は状態機械の判定正本にはしない
- ただし、実際に発話された警告・反応・川柳と、その発話意図は会話参照のため短期記憶に残す

## 2. 保存形式

正本は JSON / JSONL とする。

```text
.dogido_memory/
  short_term/
    current_session.jsonl
    rolling_summary.json
  long_term/
    haiku_entries.jsonl
    haiku_revisions.jsonl
    player_profile.json
```

### JSONL を使うもの

- 時系列で追記するログ
- 川柳エントリ
- 添削履歴

### JSON を使うもの

- 現在のプレイヤープロフィール
- 達成度の現在状態
- 起動時復元用の要約

YAML は正本にしない。手書きには便利だが、ログ追記・改行・引用符・日本語テキストの扱いで壊れやすい。

Markdown は正本にしない。人間が読む日記・共有文・UI エクスポートとして後から生成する。

## 3. 短期記憶

短期記憶は `発話された出来事の会話文脈` を持つ。

### 入れるもの

- プレイヤー入力
- ドギドの通常雑談
- 「モンスターがおるで」系の実際に発話した警告
- 「暗いで」系の実際に発話した注意
- 通常の mob 反応
- 川柳の発句
- 川柳の完成句
- 川柳生成時に LLM が出した意図説明文
- プレイヤーが保存・添削・反応した事実

### 入れないもの

- 毎 tick / 毎 snapshot の生イベント全文
- 発話されなかった内部判定
- 状態機械の派生シグナル全文
- 同種警告の過剰な連続ログ

ただし、プレイヤーがあとから話題に出す可能性がある発話は短期記憶に残す。

### 短期記憶イベント例

```json
{
  "type": "haiku_emitted",
  "time": "2026-06-11T17:27:01+09:00",
  "sequence": 4919,
  "text": "ゆきのこもれ\nやみにはかぶる\nそらのとびら",
  "interpretation": "雪のタイガの冷たい夜、手元のエンダーポータルフレームが、遠くの地底で探すはずのダイヤとは異なる、別の次元への扉を暗示している。",
  "biome": "snowy_taiga",
  "structure": null
}
```

`interpretation` は再要約せず、LLM が返した説明文をそのまま保存する。
要約で意味がずれると、後からプレイヤーに聞かれたときに混乱するため。

`reason` は原則持たない。
「静かな時間が続いたので川柳を詠んだ」のような全句共通の理由はノイズになる。
機械処理で必要になった場合だけ、`trigger_type` のような短い識別子を追加する。

### 保持量

- raw 短期イベント: 直近 24 時間、または直近 300-500 件
- active 会話バッファ: 直近 30-60 分、または重要イベント 80 件程度
- 起動時復元要約: 200-300 字
- 内部の詳細要約: 600-1000 字まで

### リフレッシュ条件

- 起動時: 前回の `rolling_summary.json` を 200-300 字で読み込む
- プレイヤー入力が来た時: 短期イベントに追加
- ドギドが発話した時: 発話内容と必要な文脈を追加
- 川柳が完成した時: 句と意図説明を追加
- 20-50 件たまった時: rolling summary を更新
- 30 分以上無言が続いた時: セッション区切りとして圧縮
- 同種警告が連続した時: 要約側で圧縮する

例: 「ゾンビ警告を 5 回」ではなく、「洞窟でゾンビ警告が続いた」とまとめる。

## 4. 長期記憶

長期記憶は `残したいもの` と `ゲーム進行の節目` だけを持つ。

### 入れるもの

- プレイヤーが保存したドギド川柳
- プレイヤー自身が作った川柳
- 川柳の添削履歴
- 川柳の改善エージェントが使う読解情報
- ゲーム進行に関わる達成度

### 入れないもの

- 通常雑談の全文
- 通常警告の全文
- mob 反応の全文
- 保存されていない短期文脈

自動生成されたドギド川柳は、完成時に短期記憶へ入れる。
長期記憶へは、プレイヤーの保存操作、または将来の明示設定によって昇格させる。

## 5. 川柳長期記憶

川柳長期記憶は `long_term/haiku_entries.jsonl` に保存する。

### 共通フィールド

- `id`: 内部識別子。UI の編集・削除・添削履歴の紐付けに使う
- `created_at`: 長期記憶エントリを作成した時刻
- `author`: `dogido` または `player`
- `kind`: `agent_haiku` または `player_haiku`
- `text`: 3 行の川柳本文
- `interpretation`: 川柳の読解・意図説明。プレイヤー川柳では未入力なら `null`
- `world`: 生成または保存時の Minecraft 文脈
- `trigger`: 元イベントに紐づく最小情報

`saved_at` は持たない。
長期記憶に入った時点で保存済み扱いになるため、`created_at` だけでよい。

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
  "source": "player_feedback",
  "comment": "やみにはかぶる、の意味が少しわかりにくい",
  "revised_text": "ゆきのよる\nやみをかぶせて\nそらのとびら"
}
```

添削エージェントは、`haiku_entries.jsonl` と `haiku_revisions.jsonl` を読む。
通常の短期ログ全文は読ませない。

## 7. ゲーム進行の長期記憶

ゲーム進行は `long_term/player_profile.json` に保存する。

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

記録対象:

- `story/mine_diamond`: ダイヤモンド！
- `story/enter_the_end`: おしまい？
- `nether/root`: ネザー
- `end/elytra`: 空はどこまでも高く

`first_unlocked_at` は必ずタイムスタンプを持つ。
未達成なら `null`。

## 8. UI 方針

長期記憶には UI が必要。

ただし、最初から作り込まない。
実装順は以下とする。

1. 長期記憶ファイルを固定する
2. 川柳保存・プレイヤー川柳保存・達成度保存を実装する
3. 一覧取得 API を作る
4. ローカル閲覧 UI を作る
5. コピーボタンを付ける
6. 共有文生成を付ける
7. SNS 共有ボタンを付ける

最初の UI は `/memory` のローカル管理画面でよい。
公開サイトではなく、`dogido-server` がローカルで配る画面として始める。

SNS 共有は初期実装では直接投稿しない。
まずは共有文を作ってコピーできるようにする。

## 9. 実装単位

最初の MVP は以下に絞る。

- 短期記憶 JSONL への発話イベント保存
- rolling summary の読み書き
- ドギド川柳の保存
- プレイヤー川柳の保存
- 川柳意図説明の保存
- 4 種の進行達成度保存
- 長期記憶一覧 API

UI は API の後に薄く作る。

状態機械の危険判定や発話優先度は、この記憶システムに依存させない。
記憶は会話・保存・振り返りのために使う。
