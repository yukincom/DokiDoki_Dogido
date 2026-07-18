# 村人コンテキスト（職業・子供・日課）設計

**日付:** 2026-07-18  
**状態:** 方針（実装前）  
**目的:** 村人を「villager 一括」から、職業・子供・日課が分かる観測にし、ambient / 雑談の違和感を減らす。  
**原則:** 判断は状態機械・純関数。LLM は言い回しのみ。アダプタは事実だけ送る。

関連: [event-schema.md](event-schema.md) · [state-machine.md](state-machine.md) · [mob-interaction-tone.md](mob-interaction-tone.md)

---

## 1. 現状

| 項目 | 状態 |
|---|---|
| `passive_mobs[].type` | 常に `villager` |
| 職業 / 子供 / 日課 | **未送信・未解釈** |
| カタログ | `villager` 一括、「こんちはー！」 |
| ゲーム時刻 | `world.time_of_day`（0–24000）と `time_phase` **既存**。日課表引きにそのまま使える |

---

## 2. 責務分担（無駄を出さない核）

```text
アダプタ          … 観測事実のみ（誰が・どんな村人か・いま何時 tick）
        ↓
DerivedSignals   … 日課フェーズ等の純関数解決（状態機械まわり）
        ↓
発話ゲート       … いつ喋るか（既存 normal + ambient 枝）
        ↓
details / カタログ … 何を材料にするか
        ↓
LLM              … 短いセリフ（ラベルをそのままドラマ化しない）
```

| 層 | やる | やらない |
|---|---|---|
| **Adapter** | `profession`, `is_baby`,（任意）`villager_type`, `world.day_time` | 日課の解釈、「仕事中」判定、セリフ |
| **状態機械 / 純関数** | tick + 属性 → `schedule_activity`、ambient の details 組み立て | 職業ごとの巨大 if を mixin にベタ書き |
| **カタログ** | 職業別ラベル・短い fallback 文 | 全職業の LLM 長文 |
| **LLM** | 観察・やさしい一言 | 職業当てずっぽう、日課の再計算 |

**新しい SM mode（`villager_work` 等）は作らない。**  
日課は `danger_darkness_score` と同様の **派生シグナル** として、既存 `normal` + ambient 枝の材料に足す。

---

## 3. スキーマ拡張（最小）

### 3.1 `passive_mobs[]` 要素（村人のとき）

既存フィールドに加え:

```json
{
  "type": "villager",
  "distance": 8.2,
  "direction": { "horizontal": "front", "vertical": "same" },
  "certainty": "high",
  "temperament": "friendly",
  "is_baby": false,
  "profession": "farmer",
  "villager_type": "plains"
}
```

| フィールド | 必須 | 説明 |
|---|---|---|
| `is_baby` | 推奨 | `true` / `false`。子供 |
| `profession` | 推奨 | レジストリ短名。例: `farmer`, `librarian`, `none`, `nitwit` |
| `villager_type` | 任意 | 見た目バイオーム種。例: `plains`, `desert`（第一波で省略可） |

- 非村人はこれらのキーを送らない（null 省略）  
- `PassiveMob` モデルも同キーを optional で追加  

### 3.2 `world`（時刻は既存）

日課テーブルは **ゲーム内 tick（0–23999）** が必要だが、既に送受信されている。

```json
"time_of_day": 6000
```

- アダプタ: `getTimeOfDay() % 24000` → `world.time_of_day`  
- サーバー: `WorldState.time_of_day` / `_effective_time_of_day`  
- `time_phase` は粗い帯用。**日課の正は `time_of_day`**  
- 新規フィールドは不要（PR-A は村人属性が主）

---

## 4. 日課ロジック（純関数・状態機械側）

ユーザ提供の Java 日課表をコード定数にする。

### 4.1 ロール（表の列）

| ロール | 条件 |
|---|---|
| `child` | `is_baby == true` |
| `employed` | 大人かつ profession ∉ {`none`, `nitwit`, 空} |
| `unemployed` | 大人かつ無職 / nitwit / 求職扱い |

### 4.2 活動（表のセル）

| activity | 意味 | ambient のトーン材料 |
|---|---|---|
| `wander` | 散歩 | ふらふら、のんびり |
| `work` | 仕事 | 職場っぽい・忙しそう（就職者のみ） |
| `gather` | 集会 | 集まってる、わいわい |
| `play` | 遊び | はしゃいでる（子供） |
| `sleep` | 睡眠 | 寝てる／静か（起こさない方向） |

### 4.3 境界（提供表の要約）

tick は **Minecraft 日周 0–23999**（表の 0000=6:00 表記に対応する実装定数で持つ）。

| おおよその帯 | 就職者 | 求職/無職 | 子供 |
|---|---|---|---|
| 早朝 | 散歩 | 散歩 | 散歩 |
| 日中前半 | **仕事** | 散歩 | **遊び** → 散歩 |
| 午後 | **集会** | **集会** | 散歩 / 遊び |
| 夕方前 | 散歩 | 散歩 | 散歩 |
| 夜 | **睡眠** | **睡眠** | **睡眠** |

実装は「境界 tick の配列 + ロール別 activity」の表引き 1 関数に閉じる。

```text
resolve_villager_schedule(day_time: int, *, is_baby: bool, profession: str | None)
  -> schedule_activity
```

- LLM に表を渡さない  
- 境界は 1 ファイル（例: `villager_schedule.py`）に定数化  
- バージョン差でズレても **ここだけ直せばよい**

### 4.4 DerivedSignals への載せ方

毎イベント:

1. `day_time` を signals または world から読む  
2. ambient 対象が村人のときだけ `resolve_villager_schedule`  
3. details に例えば:

```text
mob: 村人
mob_profession: farmer          # 表示はカタログで「農民」
mob_is_baby: false
villager_schedule: work         # コード解決済み
villager_schedule_ja: 仕事中    # 人間/LLM 用の短いラベル
```

**threat_summary の失敗を繰り返さない:**  
「仕事中」は事実ラベルに留め、「必死に働いてる危機」などドラマ語にしない。

---

## 5. 状態機械の使い方

### 5.1 いつ喋るか（既存のまま主）

- ambient は従来どおり **normal（peace 相当）で、脅威優先が空いたとき**  
- alert / panic / 交戦ヒントが強いときは **村人日課より脅威**（既存優先度）  
- クールダウン: 種キーは当面 `villager` のまま、または  
  `villager:{profession}` / `villager:baby` で分散（連打「こんちは」軽減）

新しい mode は増やさない。  
**ゲートは py_trees / `_should_emit_ambient_mob_comment`、中身の材料だけ厚くする。**

### 5.2 睡眠帯の扱い（違和感防止）

| schedule | 推奨 |
|---|---|
| `sleep` | ambient **抑制** または超控えめ fallback（起こさない・静か） |
| `work` / `gather` / `play` / `wander` | 通常 ambient 可 |

睡眠抑制は **SM / policy の if**（コード判断）。LLM に「寝てるから静かに」と任せて突破されないようにする。

### 5.3 player_chat

- 話題が村人・村のとき、視認中の村人 details に profession / schedule を載せてよい  
- stance は既存 policy。職業当ては **視認 ID があるときだけ**

---

## 6. カタログ（薄く）

第一波は最小:

| key | label 例 |
|---|---|
| `none` / 無職 | 無職の村人 |
| `nitwit` | ニットウィット |
| `farmer` | 農民 |
| `librarian` | 司書 |
| …主要職 | … |
| `baby` | 子供の村人（profession より優先表示） |

ambient fallback:

- 子供 + play → 「ちびっ子はしゃいどるな」系  
- farmer + work → 「畑仕事しとるな」系  
- sleep → 出さない or 「しずかにしとこか」  
- 未知 profession → 汎用「村人やな」

LLM 必須にしない。fallback だけでも違和感は減る。

---

## 7. 実装順序（無駄のない PR 分割）

### PR-A — 事実を取る（アダプタ + スキーマ）

1. `PassiveMob` に `is_baby`, `profession`, `villager_type?`  
2. Fabric: `VillagerEntity` から profession / baby を読む  
3. `world.day_time` を送る（`getTimeOfDay() % 24000`）  
4. テスト: fixture JSON に村人農民・子供  

**この時点ではセリフ変更なしでも価値あり**（ログで検証可能）。

### PR-B — 日課純関数 + Derived / details

1. `resolve_villager_schedule` + 表定数  
2. ambient details に profession / baby / schedule ラベル  
3. sleep 帯の ambient 抑制（SM 側 1 条件）  
4. 単体テスト: 各帯・3 ロール  

### PR-C — カタログ・プロンプト（薄い）

1. 職業ラベル / 短い fallback  
2. ambient user に「職業・活動はメモの事実。誇張しない」  
3. 性格トーン（やさしい）と整合  

### PR-D（任意）— クールダウン細分化・villager_type

---

## 8. やらないこと

- SM に `villager_morning` 等の mode 乱立  
- LLM に日課表全文を載せる  
- 職業を見た目推定で当てる  
- 村にいるだけで毎 tick 職業解説  
- 睡眠中に大きなリアクション  

---

## 9. 成功条件

1. ログで `profession=farmer`, `is_baby=true` 等が見える  
2. 同じ村人でも **昼仕事 / 集会 / 夜** でメモが変わる  
3. 子供は遊び帯で大人と違う材料になる  
4. 睡眠帯でうるさい ambient が減る  
5. 脅威中は従来どおり脅威優先  

---

## 10. 職業 ID 一覧（アダプタ実装用）

Java でよく使う短名（実装時に `getId()` で確認）:

`none`, `nitwit`, `armorer`, `butcher`, `cartographer`, `cleric`, `farmer`, `fisherman`, `fletcher`, `leatherworker`, `librarian`, `mason`, `shepherd`, `toolsmith`, `weaponsmith`

---

## 変更履歴

| 日付 | 内容 |
|---|---|
| 2026-07-18 | 初版。職業・子供・日課表。SM は mode 増やすより派生シグナル + ambient 材料 |
