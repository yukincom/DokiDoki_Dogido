# バグメモ: player_chat の観測ギャップ（旗・地下・空メモ）

**日付:** 2026-07-15  
**状態:** 切り分け・対策方針（実装は段階的）  
**関連:** [sound-identity-plan.md](sound-identity-plan.md)、[dialogue-design.md](dialogue-design.md)、[event-schema.md](event-schema.md)

---

## 1. きっかけとなったログ

### 1.1 旗持ち（ピリジャー）を言い当てられなかった

プレイヤー入力:

```text
なんだあいつら変な旗持ってるよ
```

サーバログ（要約）:

```text
player_chat_hearing empty=True named=- auditory=0 ambient=0 buffer=0
llm_leaf kind=player_chat result=fallback reason=unusable_output
  raw=旗か、NPCかダンジョンの跡かもしれん。近寄って詳しく見てみる？
decision_debug visual_count=0 recent_visual_ms=127050 recent_audio_ms=127050
action_emit text=おう、聞こえとるで〜。
```

**正解の認識（プレイヤー側）:** 不吉な旗を持った **ピリジャー**（襲撃隊系）。  
**ドギド側:** そのフレームでは **視覚脅威ゼロ** のため「あいつら」の根拠が無く、LLM が NPC／ダンジョンと推測 → 出力に `NPC`（英字）が含まれ **unusable_output** → 固定 fallback「おう、聞こえとるで〜。」

カタログ上の知識はある（例）:

- モブ: `pillager` → ピリジャー ほか illager 系
- 旗: `ominous_banner` → 不吉な旗（note あり）

→ **知識不足というより、その瞬間の観測が chat に載っていない**のが主因。

### 1.2 洞窟にいるのに地上バイオーム前提で話す

洞窟探索中でも、ドギドが地表バイオーム名だけで「地上にいる」前提の話をする。

Minecraft では地下でも **地表バイオーム id のまま**のことが多い（`plains` の下の洞窟など）。  
専用洞窟バイオーム（`lush_caves` / `dripstone_caves` / `deep_dark` 等）以外は id だけでは「洞窟」と分からない。

adapter は `sky_visible` / `ceiling_height` / `enclosure_score` / `local_light` / `y` などを送っているが、  
**player_chat は主に biome ラベルと時間帯**で場所を説明しているため、地下性が伝わらない。

### 1.3 先行関連: 音が「空メモ」で野犬連想

別ログ:

```text
text=まだなんか低い声が聞こえるような
… 野犬が遠くで鳴いてるだけちゃうかな
decision_debug recent_audio_ms=7600 visual_count=0
```

- player_chat は `hearing_summary` を渡す経路はある  
- ただし **今フレームの** `auditory_threats` / `ambient_sounds` だけ見ていた  
- `recent_audio_ms` のような「ついさっき」は combat 側に残り、会話に載らないことがあった  

→ 対策として **直近音バッファ（約12秒）＋ sound_event→カタログ名** をサーバ側に入れた（詳細は [sound-identity-plan.md](sound-identity-plan.md)）。

---

## 2. 問題の切り分け

### 問題 A — 旗・ピリジャー（視覚が空）

```text
プレイヤー: 旗持ちのあいつら（視覚）
    ↓
adapter visual_threats: 今回 visual_count=0 → 未載荷
    ↓
player_chat: 脅威メモなし・音メモなし
    ↓
LLM: 旗→NPC/ダンジョンと妄想
    ↓
sanitize: 「NPC」英字で unusable
    ↓
fallback: 「おう、聞こえとるで〜。」（話題不一致）
```

| 仮説 | 内容 | 確度 |
|---|---|---|
| A1 | 距離・LOS で視認リストに入っていない | 高（visual_count=0） |
| A2 | 載っていても type が chat に渡っていない | 中（今回はそもそも 0） |
| A3 | 旗は entity 装備／ブロックで、threat に乗らない | 高 |
| A4 | LLM + unusable + fallback が二次被害 | 確定 |

### 問題 B — 洞窟なのに地上バイオーム

| 仮説 | 内容 | 確度 |
|---|---|---|
| B1 | 専用 cave biome なのに id が地表のまま（adapter バグ） | 要ログ |
| B2 | 地表 biome の洞窟（仕様上よくある） | 高 |
| B3 | chat が `sky_visible` 等の地下指標を渡していない | 高（B2 とセットで症状が出る） |

### 問題 C — フォールバック文言

`fallback_text("general", "chat", "reply")` が **「おう、聞こえとるで〜。」** 固定。  
音の話以外（旗・見たもの）でも同じ文が出て、完全にズレる。

### 問題 D — 音の空メモ（関連・一部対応済）

hearing が **今フレーム配列のみ** → 数秒前の音が会話に載らない。  
バッファ化は対応済み。旗ケースは **視覚ゼロ** なので音バッファだけでは足りない。

---

## 3. 解決方針（提案）

### 原則

1. **観測を増やす**（視認ピリジャー／旗／地下性）  
2. **chat に載せる**（threat・地下フラグ・直近バッファ）  
3. **空のとき妄想させない**（見えてへん／わからん）  
4. **fallback を話題非依存に**  

禁止リストで「NPC」だけ潰しても、正しい答えには届かない。

### 対策案

#### P0 — 切り分けログ

player_chat 時に:

- `visual_count` / 各種 type  
- `structure` / `sky_visible` / `y` / enclosure  
- hearing（既に `player_chat_hearing` ログあり）  

可能なら問題再現時の **status_snapshot 生 JSON を1本**保存。

#### P1 — 止血（小さく）

- chat fallback を「ようわからん、もうちょい教えて」系の中立文に変更  
- player_chat に **地下コンテキスト**（`sky_visible`・天井・囲まれ度・深度帯）を載せる  
  - **2026-07-15 実装:** `_player_chat_place_context` で地表バイオームと空間（空が見えるか／地下っぽさ）を分離し `place_context` / `space_kind` を details へ  
- プロンプト: 地下っぽいとき地上散歩扱い禁止  

#### P2〜 — 旗・ピリジャー（詳細計画）

**正本:** [pillager-banner-chat-plan.md](pillager-banner-chat-plan.md)

1. **観測** … visual に pillager を載せる／装備旗／直近 visual バッファ  
2. **キーワード** … 「旗」「不気味」→ poetic / ominous_banner 照合ヒント  
3. **補強** … tactics・threat 文・中立 fallback  

#### P3 — 直近 visual バッファ

→ 上記詳細計画の ①-D に統合。

#### P4 — adapter 深掘り（必要時）

→ 上記詳細計画の ①-B / ①-C。

---

## 4. 今回ログへの短い答え

| 質問 | 答え |
|---|---|
| ドギドの視覚の範囲外？ | **そのフレームでは視覚脅威が 0。** 最後の視認は約 2 分前。ピリジャー／不吉な旗の知識はカタログにあるが、**今の観測が無い** |
| 正解が言えなかった主因 | 観測空 → LLM 妄想 → `NPC` で reject → 的外れ fallback |
| 洞窟なのに地上 | biome が地表 id なのはよくある。**地下指標を chat に渡していない**のが主因候補 |

---

## 5. 実装優先度（案）

```text
1. P0 ログ強化 / 生 JSON 1本
2. P1 fallback 修正 + 地下コンテキスト
3. P2 旗・ピリジャー観測と chat 連携
4. P3 直近 visual バッファ
5. P4 adapter 拡張（必要なら）
```

---

## 6. やらないこと（このバグ対応では）

- リアル動物・英単語の巨大禁止リストを本丸にすること  
- 観測が無いのに「ピリジャー確定」と LLM に断定させること  
- 地下でも biome id を無理やり cave に書き換えること（事実とずれる）  
