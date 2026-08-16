# 日本語 Minecraft × STT 辞書・語彙の調査（2026-07）

**日付:** 2026-07-31  
**状態:** 調査メモ（仕様の正本ではない）  
**きっかけ:** [Issue #29](https://github.com/yukincom/DokiDoki_Dogido/issues/29)  
（感圧板を whisper が `関圧番` 等に誤変換 → ドギドが未知語扱い）

関連:

- 音声入力実装: `dogido_server/voice_input.py`（ffmpeg → whisper.cpp → `POST /api/v1/player-input`）
- プレイヤー入力正規化: `dogido_server/player_input/normalize.py`（現状は空白のみ）
- 正表記の原料: `entry_catalog` の block/item/biome 等ラベル
- TTS 読み（別物）: `catalog_readings` / `docs/tts-reading-unidic-plan.md`
- 実行時 STT 方針の古い記述: [../runtime-dependencies.md](../runtime-dependencies.md)

---

## 1. ひとことで

| 問い | 答え |
|---|---|
| 日本語マイクラ用の**完成された STT 辞書**は公開されているか？ | **ほぼ無い** |
| 正表記（感圧板など）のリストはあるか？ | **ある**（公式 `ja_jp`・Wiki・**自前 catalog 約 1800 語**） |
| 誤変換一覧（関圧番・管轄版…）は公開されているか？ | **無い**（個人の認識ログにしか無い） |
| 実況が多いのに無い理由は？ | 配信音声≠学習用公開データ。操作勢は語彙を狭く閉じる |
| Dogido が借りるべきもの | 完成辞書より **正側リスト + 後処理パターン** |

**正側は手元にある。誤側は #29 ログから育てる。**

---

## 2. 症状と層（#29 RCA 要約）

観測ログ:

```text
player_input_pushed … text=関圧番だよ
llm_leaf player_chat … 関圧番？ なんていう名前やねん。初めて聞いたわ。
```

| 層 | 内容 | 判定 |
|----|------|------|
| STT（whisper.cpp） | 意図「感圧板」→ `関圧番` 等（漢字の寄せ集め） | **一次原因** |
| `player-input` | 文字列をそのまま受信 | 正常 |
| `normalize_player_text` | 空白のみ | 補正なし＝ギャップ |
| player_chat | 未知語として応答 | 入力が正しければ妥当 |
| entry_catalog | すでに「感圧板」ラベルあり | ASR 経路では未使用 |

同じ音（かんあつばん 付近）で観測・想定されるゴミ例:

- 関圧番
- 管轄版
- 間月版
- 貨物板

公式訳は **感圧板**（「版」ではない）。

STT が困る理由: 日常コーパスに稀な複合語 → 音は取れるが **よくある漢字列** に落とされる。

---

## 3. 外部調査結果

### 3.1 完成「STT 辞書パッケージ」

| 探したもの | 結果 |
|---|---|
| whisper 用 Minecraft 日本語 hotwords.txt | **定番リポジトリなし** |
| 実況音声由来の公開誤変換コーパス | **なし** |
| kotoba-whisper / whisper.cpp 向け MC fine-tune 公開モデル | **すぐ使える定番なし** |

### 3.2 近いが用途が違うもの

#### Vosk 系（語彙を閉じる）

| もの | URL / 備考 |
|---|---|
| Vosk | https://alphacephei.com/vosk/ — offline ASR、**grammar / 語彙再設定**が売り |
| VoskLib（MC mod） | https://modrinth.com/project/XnPcX7yP — Forge 向け。Literal / Grammar モード |
| 朝日新聞 M 研 note | https://note.com/asahi_ictrad/n/nb52890d2c642 — Vosk + **pykakasi でひらがな化** + コマンド語（はれ/あめ等） |

示唆:

- マイクラ音声操作の先行例は **広い一般認識より語彙を狭くする**
- 公開されているのは全アイテム辞書ではなく **自作の短いコマンド語リスト**
- Dogido 現行は **whisper.cpp**（open vocabulary）。Vosk 全面乗り換えは別プロジェクト級

#### 正表記の原料（辞書の「right」側）

| もの | 備考 |
|---|---|
| Minecraft 公式言語ファイル `ja_jp` | ブロック・アイテムの正表記 |
| [ja.minecraft.wiki 感圧板](https://ja.minecraft.wiki/w/%E6%84%9F%E5%9C%A7%E6%9D%BF) 等 | 用語の正本 |
| Crowdin / 言語 ID 対応メモ | https://github.com/jackassmc/minecraft-crowdin-languages — ローカライズ ID の対応。STT 辞書ではない |

#### whisper 周辺

| もの | 備考 |
|---|---|
| whisper.cpp `--prompt` | 通常時は Minecraft の主要カテゴリ、workshop 中だけ日本語の読み・言い換え・上五／中七／下五の推敲を加える。状態別の**短い文脈バイアス**であり、正表記の保証にはしない |
| 長い prompt に用語山盛り | その語が**出やすくなる副作用**（無関係場面でも混入しうる）→ #29 方針では非推奨 |
| whisper fine-tune | データ・評価・メンテが重い。本丸（相棒・川柳）から遠い |

### 3.3 なぜ「実況があるのに辞書が無い」か（仮説）

1. 実況は **配信コンテンツ**であり、認識エンジン用にラベル付きで配布されていない  
2. コマンド操作勢は **語彙を十個前後に閉じる**ので全アイテム辞書が要らない  
3. whisper は open vocabulary 文化で、Vosk/Kaldi のような「辞書ファイルを差す」前提が薄い  

---

## 4. このリポジトリが既に持っているもの

調査時点（2026-07-31）の `entry_catalog` 由来ラベル目安:

| 種別 | 件数（おおよそ） |
|---|---|
| block | ~1125 |
| item | ~565 |
| biome | ~65 |
| structure | ~34 |
| passive_mob 等 | ~55 |
| **日本語ユニーク（非 ASCII）** | **~1811**（感圧板を含む） |

既存の whisper 関連の「近い」実装:

| 既存 | 何をするか | ASR 本文補正か |
|---|---|---|
| inventory ヒントの `明る`（明かりの誤変換） | **意図判定**の別名 | 本文は直さない |
| `voice_input` の NOISE_PATTERNS | ノイズ文スキップ | 弱い後処理 |
| `catalog_readings` | **TTS 読み** | 別物 |

現在は、既知誤変換の固定表に加えて、**その会話で有効な候補だけ**を使うかな近傍補正を実装済み。
全 catalog を常時検索する仕組みにはしていない。

---

## 5. 調査から採用した方針（#29・現行）

| 候補 | 内容 | メモ |
|---|---|---|
| prompt に用語山盛り | whisper `--prompt` 拡張 | **副作用あり** → 非推奨寄り |
| モデル差し替え / 大型化 | kotoba 確認・サイズ上げ | 全体精度は上がりうるが誤変換ゼロにはならない |
| **後処理 A: 固定表** | 関圧番・管轄版… → 感圧板 | 実装済み。範囲が閉じ、テストしやすい |
| **後処理 A': 文脈候補のかな近傍** | 句・直近材料・時刻など、その場の候補だけに寄せる | 実装済み。曖昧候補は補正しない |
| チャット緩和のみ | 「それ感圧板？」と聞き返す | 本文は直さない |
| Vosk 乗り換え | grammar で語彙閉鎖 | 現行 voice_input の作り直し。別イシュー級 |

現行実装:

```text
固定表: 明確な既知誤変換を normalize で補正する
文脈候補: workshop 中の句、catalog の明示読み、時刻語、呼び出し側の追加候補
音声だけ: source=voice の入力だけ、読みの近い文脈候補へ保守的に補正する
原文保持: 状態変更の判定は原文、LLM の会話理解は補正後の interpreted_text を使う
安全側: 同距離の候補が複数ある、文脈候補がない、距離が遠い場合は補正しない
```

運用サイクル:

1. `player_input_pushed text=…` で奇妙な語を見つける  
2. 明確な既知誤変換なら固定表へ追加する
3. その場の固有語なら、汎用の文脈候補供給元へ追加する
4. テスト 1 本 + `asr_fix` / `asr_fix_conversation` ログで確認する

**全 block/item を予測で埋めない。**

この調査メモは経緯の記録であり、現行の入力契約は
`docs/adapter-api.md` と `docs/dialogue-design.md` を正とする。

---

## 6. やらないこと（この調査の範囲）

- 外部「完成辞書」待ちで #29 を止める  
- whisper prompt に用語を山盛りする（副作用）  
- 本調査メモを仕様正本として実装を強制する（go は issue 側）  
- Vosk 乗り換えの詳細設計（必要なら別 issue）  

---

## 7. 参照リンク集

- Issue #29: https://github.com/yukincom/DokiDoki_Dogido/issues/29  
- Vosk: https://alphacephei.com/vosk/  
- VoskLib (Modrinth): https://modrinth.com/project/XnPcX7yP  
- 朝日 M 研 note（Vosk + かな化）: https://note.com/asahi_ictrad/n/nb52890d2c642  
- 感圧板（Wiki）: https://ja.minecraft.wiki/w/%E6%84%9F%E5%9C%A7%E6%9D%BF  
- Crowdin 言語 ID: https://github.com/jackassmc/minecraft-crowdin-languages  

---

## 8. 改訂

| 日付 | 内容 |
|---|---|
| 2026-07-31 | 初版。#29 外部調査 + 自前 catalog 件数 + 方針含意 |
| 2026-07-31 | 後処理 A 実装: `player_input/asr_fixes.py`（感圧板まわり固定表） |
