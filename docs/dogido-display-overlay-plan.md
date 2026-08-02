# ドギド表示オーバーレイ（Minecraft 内 UI）計画

**日付:** 2026-07-31  
**状態:** 計画メモ（未実装）  
**きっかけ:** [issue #28](https://github.com/yukincom/DokiDoki_Dogido/issues/28) — 川柳 preface を材料どおり長くしたい一方、TTS だけでは長い説明が鬱陶しい。workshop 中にセリフを画面に残したい。

関連:

- [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md)（workshop / preface）
- [adapter-api.md](adapter-api.md)（現状は adapter→server 一方向が基本）
- [integration-architecture.md](integration-architecture.md)
- [companion-maturity.md](companion-maturity.md)
- Fabric: [adapter/minecraft-fabric/README.md](../adapter/minecraft-fabric/README.md)

---

## 1. ねらい

実態のない相棒を、**声だけ**ではなく **画面上の痕跡** としても感じさせる。

| やりたい | やらない（当面） |
|---|---|
| セリフをゲーム内に残す（聞き逃し・長さ対策） | 3D モデル常駐・フルアバター |
| workshop 中は材料説明・句の話を見返せる | サーバが Minecraft を直接描画 |
| 右サイドの薄い UI（縦書き 2D を本命） | 最初から完璧な組版・立ち絵必須 |

音声はこれまでどおり **dogido-server が TTS**。  
UI は **表示の複製・保持**であり、判断の主は状態機械のまま。

---

## 2. 動機（#28 との関係）

irony/scene は例えば:

```text
温かい昼下がりの平原で、冷たく硬いネザライトのツルハシが木を切る
```

preface を短く切ると口上は「温かい平原」だけになり、句が「つめたい」側を取ると **材料説明と句が食い違うように聞こえる**。

- **短中期:** preface を対比が残る長さまで伸ばす（サーバのみ・別作業）
- **中期:** 長い口上を **画面に載せて**、TTS は短く or 全文のまま聞き逃しを UI で補う

本ドキュメントは後者の UI 計画。

---

## 3. 現状アーキテクチャ

```text
Minecraft (Fabric adapter)
    --HTTP game-events-->  dogido_server
                              |-- TTS --> スピーカー
                              (adapter へ発話テキストを返していない)
```

- adapter は主に **送信専用**（[adapter-api.md](adapter-api.md)）
- 画面オーバーレイには **server → client の表示チャネル** が必要

---

## 4. 目標アーキテクチャ

```text
dogido_server
    |-- 音声（既存）
    |-- display queue（最新セリフ・pin 状態）
         |
         v
Fabric client（ポーリング or WebSocket）
         |
         v
In-game HUD（画面右・2D。縦書きは P2）
```

### 4.1 表示ペイロード案

```json
{
  "display_id": "dsp_...",
  "kind": "haiku_preface | haiku_verse | workshop_reply | chat | ambient",
  "text": "温かい昼下がりの平原で、冷たいネザライトが木を切ってる、なんか浮かんできたわ",
  "pinned": true,
  "workshop_open": true,
  "updated_at": "ISO-8601",
  "expires_at": null
}
```

| フィールド | 意味 |
|---|---|
| `kind` | 見た目や優先度のヒント |
| `pinned` | workshop 中など、次の短文で消さない |
| `workshop_open` | pin 寿命と連動 |
| `expires_at` | 非 pin の自動消去（任意） |

### 4.2 寿命

| 状況 | 表示 |
|---|---|
| workshop **open** | 句・材料寄りの最新セリフを **pin**（閉じるまで残す） |
| workshop **close** / 次の句 | 更新 or フェードアウト |
| 通常 chat | 短時間表示（数秒〜十数秒）のち消す |
| alert / panic | 邪魔なら薄く・退避・非表示（要プレイ感で調整） |

---

## 5. 通信の段階

| Phase | 手段 | 備考 |
|---|---|---|
| **P1** | `GET /api/v1/sessions/{id}/display` を adapter が 0.3〜0.5s ポーリング | 実装が簡単。遅延は許容範囲 |
| **P2** | サーバ→adapter の WebSocket or SSE | 低遅延・負荷減。API 拡張 |
| **P3** | バッチ・優先度付きキュー | 戦闘中スキップ等 |

**最初は P1 で十分。** 音声と完全同期は目指さない（字幕は「残す」用途）。

認証・bind は既存 adapter-api のローカル前提に合わせる。

---

## 6. Fabric 側（描画）

### 6.1 置き場

- `HudRenderCallback`（または現行 MC 版の同等 API）でオーバーレイ
- ロジックは `dogido.fabric` パッケージ内の薄い `DogidoDisplayHud` 等

### 6.2 レイアウト案

| 要素 | 案 |
|---|---|
| 位置 | 画面 **右側**（ホットバー・ハートを避ける） |
| 向き | **P1 横書き**でも可 → **P2 縦書き**（1 文字ずつ縦積み） |
| 幅 | GUI scale に追従。最大行長・最大行数を固定 |
| スタイル | 半透明背景 or 縁取り文字。実況風 UI に寄せすぎない |

縦書きは標準 API を当てにせず、**自前で文字を積む**前提。

### 6.3 表示優先

1. `pinned` workshop 関連  
2. 直近の speech（chat / ambient）  
3. 戦闘中は callout と被らないよう抑制オプション  

### 6.4 やらない（初期）

- 3D エンティティとしてのドギド常駐  
- 表情・リップシンク必須  
- フルスクリーン会話 UI  

「いる感」は **右に薄い文字が残る** で足りる段階から始める。

---

## 7. サーバ側

| 作業 | 内容 |
|---|---|
| 発話時に display を更新 | preface / 句 / workshop 返事 / 必要なら chat |
| セッション単位の latest display | SessionInfo に 1 本（or 短い履歴） |
| workshop open/close と pin 連動 | 既存 `haiku_workshop` と同期 |
| GET endpoint | adapter-api に追記 |

**判断・いつ喋るかは状態機械のまま。** display は出力のミラー。

---

## 8. 実装フェーズ

| Phase | 内容 | 依存 |
|---|---|---|
| **P0（別 PR 可）** | preface 字数を上げ、irony 対比を口上に残す | サーバのみ。UI 不要 |
| **P1** | GET display + ポーリング + 右サイド **横書き** 字幕 | adapter + server API |
| **P2** | **縦書き**・workshop pin・フェード | P1 |
| **P3** | 見た目の味（影・簡易立ち絵・テーマ） | 任意 |

---

## 9. リスクと方針

| リスク | 緩和 |
|---|---|
| 戦闘中に邪魔 | alert/panic で非表示 or 極小 |
| ポーリング負荷 | 0.5s・ローカルのみ |
| 長文で画面が埋まる | 最大文字数・スクロール or 最新 N 行 |
| API が双方向化で複雑化 | 表示専用の薄い GET から |
| 「UI 必須」に見える | TTS 単体でも動く。overlay は任意・設定で off |

---

## 10. 設定案（将来）

```properties
# Fabric client
display_overlay=true
display_poll_ms=400
display_side=right
display_vertical=false
```

サーバは session の display を常に持ってよく、クライアントが読まなければ無視。

---

## 11. 成功の定義

- workshop 中、長い preface を **画面で読み返せる**  
- 「温かい平原」だけ口にして「つめたい」句、の **誤解が減る**（preface 延長とセット）  
- 音声オフでも「ドギドが何か言った」痕跡が残る  
- 本編プレイの邪魔にならない（off 可能）

---

## 12. 非ゴール

- 汎用チャット UI プラットフォーム  
- 複数プレイヤー分の吹き出し空間配置（[multi-user-tenancy](multi-user-tenancy.md) とは別軸）  
- VLM / 画面認識との統合  

---

## 13. 次のアクション

1. **P0:** preface 延長（#28 向け・本 doc 外でも可）  
2. 本計画を issue からリンク（任意）  
3. **P1** 着手時: `adapter-api` に GET display を追記し、Fabric に HUD 1 枚  

状態が動いたらこの文書のヘッダ「状態」を更新する。
