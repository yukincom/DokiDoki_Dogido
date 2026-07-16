# 川柳・Senryu ロードマップ

**更新:** 2026-07-14  
**ブランチ文脈:** `Senryu-RAG` 以降の対話・カタログ・記憶まわり

関連:

- [川柳フィードバック（実装メモ）](haiku-feedback-plan.md)
- [Senryu-RAG 実装プラン](senryu-rag-plan.md)（カタログ直引き優先・ベクトルは任意）
- [川柳アーキテクチャ](haiku-architecture.md)
- [記憶アーキテクチャ](memory-architecture.md)
- [対話設計](dialogue-design.md)

---

## 1. いま入っているもの（だいたい完了）

「下手な句を出して、直して、残して、あとから呼び出す」の**骨格は一通り入った**。

### 発句の質（カタログ使い切り）

| 項目 | 内容 |
|---|---|
| カタログ観察 | biome / structure / nearby block の `note` を HaikuContext へ |
| 詩語 | 主役平和 mob の `poetic_lines`（role 中心）。`haiku_tags` と二重にしない |
| 読み | エントリ `reading` ＋ `catalog_corrections.jsonl` オーバーレイ（例: 草地→くさち） |
| 発句フロー | irony → scene → haiku。状態機械優先は維持 |

### 記憶・フィードバック

| 項目 | 内容 |
|---|---|
| 発句の長期保存 | **基本すべて** `haiku_entries.jsonl` へ自動保存（プレイ中は句が珍しいため） |
| 元句＋直し | `直し:` で revision にペア保存（プロンプトには常時載せない） |
| 読み訂正 | プレイヤー指摘 → オーバーレイ → 次回のラベル／制約に反映 |
| 明示的想起のみ | 「句思い出して」等のときだけ memory 検索。発句プロンプト常駐はしない |
| 日付（壁時計） | 今日／昨日／今週／**今月**／**ここひと月**／N月／N月M日。`created_at` を使用。**ゲーム時刻は使わない** |
| 場所 | カタログ全ラベル・読み ＋ biome **group**（寒いところ＝cold+snowy、乾燥帯、温帯、洞窟、ネザー…） |

### 対話（前段・main 側）

- peace / battle / tension、会話履歴、inventory オンデマンド、tactics、player-input 再キュー など

### 正本データの置き場

```text
.dogido_memory/
  short_term/          … セッションログ（発話・haiku_emitted 等）
  long_term/
    haiku_entries.jsonl
    haiku_revisions.jsonl
    catalog_corrections.jsonl
    player_profile.json
```

API 例: `GET /api/v1/memory/haiku`（entries 一覧。UI の土台になりうる）

---

## 2. 入れていない・薄いもの

| 項目 | 状態 | メモ |
|---|---|---|
| 音の正体・非MC生物名 | 一部対応 / 計画 | [sound-identity-plan.md](sound-identity-plan.md)。直近音バッファは実装済み |
| player_chat 観測ギャップ（旗・地下） | 地下コンテキスト一部済 / 旗は計画 | [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md)、[pillager-banner-chat-plan.md](pillager-banner-chat-plan.md) |
| Simple Vector RAG（Chroma 等） | 未実装 | [senryu-rag-plan.md](senryu-rag-plan.md) 第2波。直引きと被らせない |
| 対話ワークショップ | 計画 | [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md)。自然文の講評→critique/lesson→次回制約 |
| biome / block note の中身 | データ不足 | 仕組みはある。コンテンツ作業 |
| ぼんやり場所の言い回し網羅 | 部分的 | プレイでフレーズを足す |
| `haiku.py` mixin 分割 | 任意 | ~850 行。context 構築の切り出し候補 |
| M5Stack / LINE・Discord | 後回し | 対話・川柳が固まってから |

---

## 3. 将来計画

### 3.1 保存した川柳を見る UI（候補・優先度高）

長期 JSONL に句が貯まる前提なので、**見る・探す・見返す UI** があると体験が閉じる。

**やりたいこと（案）**

- 一覧: 日付・バイオーム・元句／直し句・interpretation
- フィルタ: 今月／ここひと月、biome group、作者（dogido / player）
- 詳細: 元と直しの並び、読み訂正との関連は後で
- 操作（任意）: お気に入り、削除、Markdown / 画像エクスポート（日記用）
- データ源: `.dogido_memory/long_term/*` と既存 `GET /api/v1/memory/haiku`（revisions 用 endpoint を足す想定）

**置き場所の候補**

| 案 | 向き |
|---|---|
| ローカル Web（dogido-server に静的 UI + API） | 実装が近い。PC プレイと同居しやすい |
| 別 CLI / TUI | 軽い。一覧・grep 向き |
| 将来 M5Stack / 外部アプリ | 出力デバイス方針が固まってから |

**やらないこと（最初）**

- クラウド同期必須
- ソーシャル投稿
- 句を毎回 LLM プロンプトに自動注入する UI（記憶方針と矛盾）

**実装の切り方（案）**

1. API: entries + revisions の一覧／フィルタ（since/until/biome/group）  
2. 最小 HTML/React 等でテーブル表示  
3. お気に入り・エクスポートは後続  

### 3.2 Vector RAG（任意）

- 直引きで足りない横断語・教育短文だけ
- 見えている ID の poetic 再取得はしない（二重）
- 発句はレアなので、**先に UI と note コンテンツの方が体感価値が高い**可能性あり

### 3.3 対話での添削ワークショップ

- 直近句 + interpretation を chat に明示注入
- 「直して」→ 候補 → プレイヤー確定で revision 保存
- 戦闘中は起動しない（既存優先順位）

### 3.4 カタログコンテンツ

- biome / structure / block の `note` 充実
- 主要語の `reading` 先回り投入（誤読が分かったものから）
- オーバーレイ訂正の data/ 本編への取り込み手順

### 3.5 コード健全性

- `mixins/haiku.py` から context 組み立てを分離
- 想起・読み・保存のテストをプレイシナリオで増やす

### 3.6 デバイス・外部（方針どおり後回し）

- M5Stack Push Avatar
- LINE / Discord メッセージ

---

## 4. 優先度の目安（将来）

```text
1. 実プレイで読み訂正・直し・想起を回して穴潰し
2. 保存川柳 UI（一覧・フィルタ）     ← 記憶が貯まるほど効く
3. note / reading のコンテンツ増強
4. 添削ワークショップ（対話）
5. Vector RAG（必要なら）
6. haiku mixin 分割・M5Stack 等
```

---

## 5. 設計上の固定方針（忘れない用）

1. **履歴句はプロンプト常駐させない**。明示 recall か UI で見る  
2. **読み・語の訂正はカタログ側**（オーバーレイ → のち本編）  
3. **日付は壁時計 `created_at`**。ゲーム内昼夜は使わない  
4. **場所はカタログ group / label が正本**。手書き辞書は補助に留める  
5. **状態機械の panic / cue は触らない**  
6. 汎用エージェント基盤（Hermes 等）は使わない  

---

## 6. 関連テスト

```bash
pytest tests/test_haiku_feedback.py tests/test_haiku.py tests/test_memory.py -q
```
