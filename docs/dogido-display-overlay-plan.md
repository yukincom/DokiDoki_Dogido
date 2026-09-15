# ドギド表示オーバーレイ（Minecraft 内 UI）計画

**日付:** 2026-07-31  
**状態:** 2026-09-12、採用した左下・右向きドギドと、右下の川柳掛け軸をFabricへ実装。読み取り専用snapshotでworkshop開始・終了・未採用案・編集対象・危険中断へ接続した。サーバー・Java自動テストとビルド済み。実Minecraftでの表示・音声編集・危険割り込みを含む一連の確認は未完了。通常雑談のゲーム内字幕は対象外。ゲーム外の発言履歴は別機能。
**きっかけ:** [issue #28](https://github.com/yukincom/DokiDoki_Dogido/issues/28) — 川柳 preface を材料どおり長くしたい一方、TTS だけでは長い説明が鬱陶しい。workshop 中にセリフを画面に残したい。

2026-09-13: 負の行列scaleによる裏面カリングが常駐ドギド非表示の原因となることを確認。暫定の画像U座標反転も撤回し、作者制作の右向き原画 `Dogido_nomalR.png`（1305×1298）を無加工で採用。デザイン上、CSS・画像座標・描画面の反転は行わず、向きごとに原画を使う。左下位置・上下動・表示設定は維持し、縦横比は新原画に合わせる。修正版の実ゲーム表示確認は未完了。掛け軸の `closed` 時の非表示とは別の問題。

2026-09-15: 作者が太線化した `Dogido_nomal.png` / `Dogido_nomal_close.png`（両方1398×1336）を無加工で採用。対称化した生成案は不採用。常駐HUDは通常顔4.5秒・閉じ目140ミリ秒をクライアント内だけで切り替え、`motion off` では上下動と瞬きを止める。配置・反転なし・表示条件・掛け軸判断は維持。新しい瞬きの実ゲーム確認は未完了。

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

**現在の実装が正:** 以下のブラウザ検討を経て、ゲーム内への連動を追加した。
実際の操作と設定は [adapter README](../adapter/minecraft-fabric/README.md#左下のドギドとワークショップの掛け軸)、通信契約は [adapter-api §25](adapter-api.md#25-ゲーム内の川柳掛け軸) を参照。
下記の「未反映／今後」は配置検討時点の記録。現在は左下配置・右向き・上下動、右側の三行、1秒の出現／終了、危険時の即遮蔽、未採用案ラベル、音声編集の対象表示まで接続済み。
本文はMinecraft標準フォントによる一文字ずつの縦積み。左側の掛け軸への切替・マウスによる行編集・通常字幕・TTSとの厳密な同期は接続しない。

- 通常時はドギド一枚。右下→右上→洞窟での左下比較を経て、**定位置は左下を採用**。盾で隠れる範囲との重なりを許容し、表示だけ左右反転して右を向かせる。配置検討用の [`tools/character-placement/index.html`](../tools/character-placement/index.html) で、地上・洞窟の録画とホットバーを含むスクリーンショット全体を背景にする。作者提供の `animation/ドギド整え.png` は変更しない。Fabric静止画試作はまだ右下基準で、採用配置のゲーム内反映は未実施。
- Fabric側の `DogidoCharacterHud` は透過静止画の表示のみ。独立した `config/dogido-character.properties` に位置を保存し、サーバーなしでも表示できる。F1、メニュー、インベントリ、チャット入力中は隠す。詳細・試し方は [adapter README](../adapter/minecraft-fabric/README.md#右下のドギド静止画試作)。
- **採用した配置（プレビュー済み）:** ドギドは左下・幅9%・左2.5%・下4%。掛け軸は右下・幅26.5%・右1.5%・下10.5%・高さ74%・文字3.5cqwを初期値にする。右から上五・中七・下五を大きく表示し、約1秒でじわっと現れる。workshop終了時は約1秒でじわっと消え、敵の接近など危険時はアニメーション途中でも即非表示にする。プレビューの「終了」「危険」ボタンで模擬できるが、実ゲーム状態との連動は未実装。通常の途中切り替えは現在の透明度から継続し、動きを減らすOS設定では即時表示。仮の句の手入力変更は画面内だけに反映し、保存・音数判定・音声連動はしない。
- **比較操作:** ドギドは従来の右上へ、掛け軸は左下へも切り替え可能。位置はそれぞれ独立して維持し、同じ側に置いた場合はドギドを手前に描く。ゲーム内HUDには未反映。
- **編集用UI（プレビュー済み）:** 編集意思を模擬する「句を直す」で、左から上・中・下の独立選択バーを即時表示。対象ボタンで該当する本文一列だけを濃色の太枠と淡い橙背景で囲み、ボタンの橙背景・太枠と「選択中」の文字を同時に出す。点滅・選択フェードは使わず、明示操作時の短い「ピッ」音はオフにできる。選択後は対象行だけの仮の表示変更を試せる。通常モードへ戻ると選択を解除する。音声指定・実workshop状態・採用・保存には接続しない。
- **表装の制約:** 作者指定は「素人の川柳に高い格の表装を使わない」。今回は過美を避け、一文字を省いた「草の草」を参考に、細い左右の裂地・無地の天地・簡素な軸だけをCSSで試作。金襴・豪華な縁・落款は加えない。正式な表装の再現や生成素材の確定ではない。[野村美術の表装解説](https://nomurakakejiku.jp/lesson_lineup/yamato-style-mounting)を参照。
- **今後の予定（今回接続しない）:** workshop開始時だけ句を表示し、音声修正が正本／未採用案に反映されたら画面も更新する。正本と未採用案を混同しない。平和／戦闘の判定、通信・状態連携は本体側で別途実装する。
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

以下の§3〜13は初期計画の記録。現在はgame-event応答を拡張せず、独立した読み取り専用GETと400msの非同期取得で縦書き掛け軸へ接続した。現行の正は上記adapter-api §25。

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
| workshop **close** / 次の句 | 掛け軸は終了時に約1秒でフェードアウト／次の句で更新 |
| 通常 chat | 短時間表示（数秒〜十数秒）のち消す |
| alert / panic | 掛け軸は即非表示。句・未採用案の保持は既存workshop状態に従う |

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
