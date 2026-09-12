# ドギド表示オーバーレイ（Minecraft 内 UI）計画

**日付:** 2026-07-31  
**状態:** 2026-09-12、右下の静止画HUDと通常時の配置プレビューを試作。Javaビルド・配置計算テスト済み、実Minecraft表示は未確認。字幕・workshop連動は未実装。ゲーム外の発言履歴は別機能として実装済み。
**きっかけ:** [issue #28](https://github.com/yukincom/DokiDoki_Dogido/issues/28) — 川柳 preface を材料どおり長くしたい一方、TTS だけでは長い説明が鬱陶しい。workshop 中にセリフを画面に残したい。

関連:

- [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md)（workshop / preface）
- [adapter-api.md](adapter-api.md)（観測はadapter→server。支援commandだけgame-event応答に逆向きで載る）
- [integration-architecture.md](integration-architecture.md)
- [companion-maturity.md](companion-maturity.md)
- [dogido-utterance-display.md](dogido-utterance-display.md)（実装済みのゲーム外画面）
- Fabric: [adapter/minecraft-fabric/README.md](../adapter/minecraft-fabric/README.md)

---

## 1. ねらい

### 2026-09-12: 最初に通常時の居場所を決める

- 通常時はドギド一枚。右下案をプレビューした結果、手持ち道具と重なるため、**右上案へ変更**。配置検討用の [`tools/character-placement/index.html`](../tools/character-placement/index.html) で、ホットバーを含む過去のプレイスクリーンショット全体の上に、大きさ・余白・軽い上下動を比較する。原画は作者提供の `animation/ドギド整え.png` を無加工で使用。Fabric静止画試作はまだ右下基準で、位置が決まってから揃える。
- Fabric側の `DogidoCharacterHud` は透過静止画の表示のみ。独立した `config/dogido-character.properties` に位置を保存し、サーバーなしでも表示できる。F1、メニュー、インベントリ、チャット入力中は隠す。詳細・試し方は [adapter README](../adapter/minecraft-fabric/README.md#右下のドギド静止画試作)。
- **今後の予定（今回実装しない）:** 通常時は画面の隅でホヨホヨ（現在は右上案）。workshop開始時だけ掛け軸のような縦長表示を開き、ドギドの句を縦書きで残す。音声での修正が正本／未採用案に反映されたら、表示もその状態へ更新する。正本と未採用案を混同しない。通信・状態連携・アニメーションの細部は配置を確認してから決める。
- ブラウザの上下動は配置検討用。ゲーム内のアニメーションや句表示はまだ接続していない。

実態のない相棒を、**声だけ**ではなく **画面上の痕跡** としても感じさせる。

| やりたい | やらない（当面） |
|---|---|
| セリフをゲーム内に残す（聞き逃し・長さ対策） | 3D モデル常駐・フルアバター |
| workshop 中は材料説明・句の話を見返せる | サーバが Minecraft を直接描画 |
| 右サイドの薄い UI（縦書き 2D を本命） | 最初から完璧な組版・立ち絵必須 |

音声はこれまでどおり **dogido-server が TTS**。  
UI は **表示の複製・保持**であり、判断の主は状態機械のまま。

なお、聞き逃した本文の閲覧・コピーと参考資料一覧は、Minecraft内HUDを待たず、
同一サーバーの `/dogido` にゲーム外の読み取り専用画面として実装した。本計画は
引き続き、プレイ中にMinecraft画面へ薄く残すHUDだけを対象とする。

---

## 2. 動機（#28 との関係）

irony/scene は例えば:

```text
温かい昼下がりの平原で、冷たく硬いネザライトのツルハシが木を切る
```

preface を根拠と無関係に短く切ると口上は「温かい平原」だけになり、句が「つめたい」側を取ると **材料説明と句が食い違うように聞こえる**。

- **済:** preface を1〜3個の検証済み節として生成し、一次atom・事実／解釈・主張範囲を保ったまま発話する
- **中期:** 長い口上を **画面に載せて**、TTS は短く or 全文のまま聞き逃しを UI で補う

本ドキュメントは後者の UI 計画。

---

## 3. 現状アーキテクチャ

```text
Minecraft (Fabric adapter)
    --HTTP game-events-->  dogido_server
    <--typed assist command--   （持ち替えだけ）
                              |-- TTS --> スピーカー
                              (adapter へ発話・表示テキストは返していない)
```

- game-event応答の逆向き経路は型付き支援command専用で、表示キューではない（[adapter-api.md](adapter-api.md)）
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
| **P0（済）** | preface を節単位の出典・主張範囲つきにし、根拠を保った口上にする | サーバのみ。UI 不要 |
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

1. 本計画を issue からリンク（任意）
2. **P1** 着手時: `adapter-api` に GET display を追記し、Fabric に HUD 1 枚

状態が動いたらこの文書のヘッダ「状態」を更新する。
