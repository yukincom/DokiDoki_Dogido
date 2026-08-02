# 視線先（クロスヘア）観測 — look_target 計画

**日付:** 2026-08-01  
**状態:** 方針確定・実装対象  
**関連 issue:** 本計画とセットで GitHub に登録  
**関連:** [companion-maturity.md](companion-maturity.md) · [bug-player-chat-observation-gaps.md](bug-player-chat-observation-gaps.md) · [research/minecraft-ja-stt-dictionary-2026-07.md](research/minecraft-ja-stt-dictionary-2026-07.md) · [#29](https://github.com/yukincom/DokiDoki_Dogido/issues/29)

---

## 1. ひとことで

プレイヤーが「これ何？」「この花」「感圧板」と言うときの指差しは、だいたい画面中央の **＋（クロスヘア）**。

```text
プレイヤーの指差し ＝ クロスヘア
ドギドの共有注意 ＝ look_target（視線先 1 ブロック or エンティティ）
```

近傍フルスキャンより **1 点**の方が「見てるだろ」感が出る。通信量も小さい。

---

## 2. 役割分担（何を足す／足さない）

| 話題 | 既存 or 方針 |
|------|----------------|
| 背後の音・脅威 | 既存 auditory / hearing |
| 友好 mob | 既存 `passive_mobs` |
| 地帯の空気（砂っぽい・森っぽい） | **バイオーム**で足りることが多い |
| 資源の木・石炭 | 既存 `nearby_resources`（限定フィルタ） |
| **指差し（花・感圧板・色付きブロック）** | **`look_target`（本計画）** |
| 近傍フルブロック列挙 | **急がない** |

バイオーム ≠ 足元のブロック（平原の砂利道、砂漠村のオーク床など）。  
雰囲気は biome、**名前当て・STT 補強**は look_target。

---

## 3. スキーマ案

イベント任意フィールド:

```json
"look_target": {
  "kind": "block",
  "name": "poppy",
  "distance": 2.4
}
```

| フィールド | 内容 |
|------------|------|
| `kind` | `block` / `entity` / 省略時 miss |
| `name` | Minecraft id（path または `namespace:path`） |
| `distance` | プレイヤーから目標までの距離（任意） |

- 空気・遠すぎ・MISS → **フィールド自体を省略**（null 送信しない）  
- サーバで catalog により `label_ja` を付与（adapter は id のみでよい）

---

## 4. 実装段階

| 段階 | 内容 | 状態 |
|------|------|------|
| **L1** | Fabric: crosshair raycast → 各 status 系イベントに `look_target` | **済** |
| **L2** | `GameEvent.look_target` + event-schema 記載 | **済** |
| **L3** | player_chat: observation / details に視線先ラベル | **済** |
| **L4** | STT 後処理: 固定表 A + 視線先/手持ちが pressure_plate なら感圧板系を強化 | **済**（軽い） |
| **L5** | 川柳 materials に look を薄く載せる | 任意・後回し可 |
| **L6** | look を指差し時だけ強く（戦況・在否では控えめ） | **済**（#31–33 戦況方針と同時） |

---

## 5. やらないこと

- 近傍全ブロックの毎 tick 送信  
- whisper prompt に用語山盛り  
- 読み仮名を catalog 全項目必須にする  
- VLM 常時  

---

## 6. 受け入れ

1. ポピーを見て「この花は何？」→ 観測または chat 材料にポピー系が載る  
2. 感圧板を見て/持って STT が `関圧番` → 固定表または文脈で `感圧板`  
3. 空を見ているとき `look_target` が無くてもイベントは壊れない  

---

## 7. 改訂

| 日付 | 内容 |
|------|------|
| 2026-08-01 | 初版。方針確定・L1–L4 実装対象 |
| 2026-08-01 | L1–L4 実装。Issue #30 |
| 2026-08-02 | L6: 指差し時だけ look を chat 材料に（#33 方針） |
