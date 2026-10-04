# 視線先（クロスヘア）観測 — look_target

**初版:** 2026-08-01 · **更新:** 2026-10-04  
**状態:** L1〜L3・L6の視線先観測と会話への反映は実装済み。L4は旧Python版の完了記録と、現行Rustで接続されている補正の範囲を分ける。  
**未実装:** L5の川柳材料への追加は任意の将来案。実機の確認結果は[実機チェック](../dogido-rust/manual-dialogue-check.md)で扱う。  
**関連 issue:** [#30](https://github.com/yukincom/DokiDoki_Dogido/issues/30)  
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
雰囲気はbiome、**指差した対象の名前当て**はlook_target。視線先を使うSTT補強は当初の設計範囲であり、現行Rustの接続状況は下表のL4を参照する。

---

## 3. 現行スキーマ

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
| `kind` | `block` / `entity`。オブジェクト内で省略した場合は`block` |
| `name` | Minecraft id（path または `namespace:path`） |
| `distance` | プレイヤーから目標までの距離（任意） |

- FabricのクロスヘアがMISS・空気・対象外のエンティティを指す場合は、**`look_target`フィールド自体を省略**する。`kind`の省略とは別に扱う  
- サーバで catalog により `label_ja` を付与（adapter は id のみでよい）

---

## 4. 実装段階

| 段階 | 内容 | 状態 |
|------|------|------|
| **L1** | Fabric: crosshair raycast → 各 status 系イベントに `look_target` | **済** |
| **L2** | `GameEvent.look_target` + event-schema 記載 | **済** |
| **L3** | player_chat: observation / details に視線先ラベル | **済** |
| **L4** | STT後処理: 固定表、視線先・手持ち・現在候補による限定補正 | **一部**。現行Rustは感圧板等の固定補正と、音声のworkshop入力への音近傍補正。通常会話で視線先・手持ちを候補にする経路は未接続 |
| **L5** | 川柳 materials に look を薄く載せる | 任意・後回し可 |
| **L6** | look を指差し時だけ強く（戦況・在否では控えめ） | **済**（#31–33 戦況方針と同時） |

---

## 5. やらないこと

- 近傍全ブロックの毎 tick 送信  
- whisper prompt に用語山盛り  
- 読み仮名を catalog 全項目必須にする  
- VLM 常時  

---

## 6. 確認する振る舞い

1. ポピーを見て「この花は何？」→ 観測または chat 材料にポピー系が載る  
2. STTが`関圧番`と返したとき、固定表で`感圧板`へ補正する（視線先の有無には依存しない）  
3. 空を見ているとき `look_target` が無くてもイベントは壊れない  

---

## 7. 実装と検証の入口

- Fabricの`DogidoClientAdapter.buildLookTarget`がクライアントのクロスヘア結果を読み、観測イベントへ付与する。
- Rustの[イベント型](../dogido-rust/src/events/models.rs)が受理し、[会話観測](../dogido-rust/src/chat_observation.rs)と[planner](../dogido-rust/src/planner/)が指差し・在否・戦況を区別する。
- 固定の入力補正は[player_text](../dogido-rust/src/player_text.rs)。[contextual_asr](../dogido-rust/src/contextual_asr.rs)の音近傍補正は、音声かつworkshop内で、現在句・未採用案・保存材料を候補に使う。解釈面だけを補正し、原文を操作根拠として保持する。

受け入れ例は実機で確認する条件であり、この文書の更新による確認済みの宣言ではない。

## 8. 改訂

2026-08の実装記録は旧Python本体の時点。現行Rustの状態は第4節を正とする。

| 日付 | 内容 |
|------|------|
| 2026-08-01 | 初版。方針確定・L1–L4 実装対象 |
| 2026-08-01 | L1–L4 実装。Issue #30 |
| 2026-08-02 | L6: 指差し時だけ look を chat 材料に（#33 方針） |
| 2026-08-12 | L4拡張: `source=voice` のみ、現在候補への一意な音近傍を会話解釈面へ適用。明示操作は原文のまま |
| 2026-10-04 | Rustの実装先、kind省略時、L4の接続範囲、任意のL5と実機確認を整理 |
