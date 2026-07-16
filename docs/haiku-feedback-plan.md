# 川柳フィードバック計画（実装メモ）

2026-07-14 時点の実装方針と動かし方。

**全体の進捗・将来（保存 UI 含む）:** [senryu-roadmap.md](senryu-roadmap.md)

## 二つの仕組み

| 系統 | 置き場 | プロンプト |
|---|---|---|
| **エピソード記憶**（元句＋直し句） | `.dogido_memory/long_term/haiku_entries.jsonl` / `haiku_revisions.jsonl` | **常時載せない**。明示的に「句思い出して」などのときだけ recall |
| **読み・語の訂正** | カタログ `reading` ＋ `catalog_corrections.jsonl` オーバーレイ | 次回の発句材料（ラベル・制約）に反映 |

## プレイヤー入力（現状）

| 言い方 | 動作 |
|---|---|
| `草地はくさち` / `読み: 草地=くさち` / `そうちじゃなくてくさち` | 読み訂正をオーバーレイ保存。草地は「くさち」と返事 |
| `直し: 五行 / 七行 / 五行` | 直近発句を元句として長期保存し、直し句を revision にペア保存 |
| `今の句保存` | 従来どおり元句だけ entries へ |
| `草地の句思い出して` | 長期記憶を検索して読み上げ（発句プロンプトには入れない） |
| `今月の句` / `ここひと月の句` / `昨日の句` / `7月の句` | `created_at`（壁時計）で期間フィルタ。ゲーム時刻は使わない |
| `寒いところの句` / `乾燥帯` / `温帯` / `ネザーの句` など | カタログ biome **group**（snowy/cold/temperate/dry/…）を展開して検索 |
| `草地` / `雪のタイガ` など | カタログの japanese / reading から具体 biome を解決 |
| （発句そのもの） | **基本すべて長期 `haiku_entries` に自動保存**（プレイ中は句が珍しいため） |

## 草地の例

- `data/catalogs/entries/minecraft_biome.json` の `meadow` に `"reading": "くさち"`
- 川柳コンテキストのバイオーム表示は `草地（くさち）`
- 制約に `allowed: くさち`、オーバーレイがあれば `forbidden: そうち`

## コード

- `dogido_server/catalog_readings.py` … オーバーレイ辞書
- `dogido_server/memory.py` … `save_haiku_feedback` / `save_reading_correction` / `search_haiku_memory`
- `dogido_server/player_input/guardrails.py` … 意図抽出
- `dogido_server/service.py` … 返事と保存配線

## テスト

```bash
pytest tests/test_haiku_feedback.py -q
```
