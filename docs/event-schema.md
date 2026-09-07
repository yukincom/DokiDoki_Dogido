# イベントスキーマ

この文書は、Minecraft client adapter から `dogido-server` へ送るイベント JSON の現行仕様です。

状態遷移ルールは [状態機械](state-machine.md) を参照します。
受信 endpoint 自体の仕様は [受信 API 仕様](adapter-api.md) を参照します。

## 1. 目的

- プレイヤー本人の観測結果を、`dogido-server` が処理しやすい形で正規化する
- `visual / auditory / inferred` の区別を明示する
- Minecraft 固有情報を残しつつ、最低限の抽象イベントも持たせる
- チート寄りの透視情報をイベントとして送らない

## 2. 送信方針

このスキーマは transport-agnostic だが、初期実装ではローカル HTTP 送信を前提とする。

## 3. 設計原則

- 観測対象はメインプレイヤー本人
- 1 メッセージは 1 つの主要イベントを持つ
- 主要イベントに加え、判定に必要な周辺コンテキストを同梱する
- 音由来情報は方向中心にし、未視認敵の断定を避ける
- 後方警告は面白さ優先で許可するが、距離は短めに保つ

## 4. トップレベル構造

```json
{
  "schema_version": "2026-05-24",
  "game": "minecraft-java",
  "adapter": "dogido-fabric-client",
  "observed_at": "2026-05-24T14:32:10.512+09:00",
  "sequence": 1842,
  "event": {
    "name": "threat_approaching",
    "source_kind": "visual",
    "priority_hint": "urgent",
    "certainty": "high"
  },
  "player": {},
  "world": {},
  "visual_threats": [],
  "auditory_threats": [],
  "passive_mobs": [],
  "inventory": {},
  "nearby_resources": [],
  "dropped_items": [],
  "recent_block_breaks": [],
  "look_target": null,
  "combat": {},
  "meta": {},
  "command_results": []
}
```

## 5. トップレベル項目

### 必須

- `schema_version`
- `game`
- `adapter`
- `observed_at`
- `event`
- `player`
- `world`

### 推奨

- `sequence`
- `visual_threats`
- `auditory_threats`
- `ambient_sounds`
- `inventory`
- `combat`

### 任意

- `passive_mobs`
- `nearby_resources`
- `dropped_items` … 周囲8ブロック以内の落下アイテム。同種は集約する
- `recent_block_breaks` … ローカルプレイヤーが直近10秒に実際に壊したブロック
- `look_target` … クロスヘア（＋）が刺さっているブロック/エンティティ。MISS 時は省略可
- `meta`
- `command_results` … adapterで未ackの型付きcommand実行結果。通常は空配列

## 6. 共通 enum

### `source_kind`

- `visual`
- `auditory`
- `inferred`
- `system`

### `certainty`

- `low`
- `medium`
- `high`

### `priority_hint`

- `critical`
- `urgent`
- `normal`
- `background`

### `horizontal_direction`

- `front`
- `front_right`
- `right`
- `back_right`
- `back`
- `back_left`
- `left`
- `front_left`

### `cardinal_direction`

- `north`
- `northeast`
- `east`
- `southeast`
- `south`
- `southwest`
- `west`
- `northwest`

### `vertical_relation`

- `above`
- `same`
- `below`

### `distance_band`

- `touching`
- `very_close`
- `close`
- `mid`
- `far`

## 7. 共通オブジェクト

### `direction`

```json
{
  "horizontal": "back",
  "cardinal": "northwest",
  "vertical": "same"
}
```

`horizontal` はプレイヤーの向きに対する相対方向、`cardinal` はMinecraft座標を基準にした絶対方位。

### `position`

```json
{
  "x": 120.5,
  "y": 64.0,
  "z": -35.25
}
```

## 8. `event` オブジェクト

主要イベントを 1 つだけ持つ。

```json
{
  "name": "threat_approaching",
  "source_kind": "visual",
  "priority_hint": "urgent",
  "certainty": "high"
}
```

### `name`

スキーマ上は以下を受け付ける。

- `threat_detected`
- `threat_approaching`
- `hostile_audio_detected`
- `danger_darkness_changed`
- `resource_option_found`
- `ambient_mob_detected`
- `player_died`
- `time_phase_changed`
- `hostile_defeated`
- `creeper_detonated`
- `combat_ended`
- `status_snapshot`

#### 本番 adapter の主経路（2026-07 時点）

Fabric adapter が実際に送る中心は次のとおり。

- `status_snapshot`
- `threat_approaching`
- `hostile_audio_detected`
- `ambient_mob_detected`
- `player_died`
- `hostile_defeated`
- `creeper_detonated`
- `combat_ended`

#### レガシー / テスト用（専用イベントを主経路にしない）

- `danger_darkness_changed` … 暗所は `status_snapshot` 上の `danger_darkness_score` 等と server 内の多段状態（`dark_push` / shelter 等）で扱う。経緯は [現行仕様 §6](current-spec.md)
- `threat_detected` … 接近は `threat_approaching`、平常同期は snapshot で足りることが多い
- `resource_option_found` … `nearby_resources` 同梱で代替
- `time_phase_changed` … `world.time_phase` を snapshot 等で継続更新

## 9. `player` オブジェクト

```json
{
  "name": "main_player",
  "position": { "x": 0, "y": 64, "z": 0 },
  "yaw": 180.0,
  "pitch": 0.0,
  "health": 20,
  "hunger": 18,
  "dimension": "minecraft:overworld",
  "held_item": "minecraft:stone_sword",
  "block_breaking_active": false,
  "hotbar": {
    "selected_slot": 2,
    "slots": [
      {
        "slot": 2,
        "item_id": "minecraft:stone_sword",
        "count": 1,
        "damage": 120,
        "max_damage": 131,
        "attack_damage": 5.0,
        "weapon_kind": "sword"
      }
    ]
  },
  "vehicle": {
    "vehicle_id": "minecraft:horse",
    "activity": "running",
    "controlling": true
  }
}
```

### 項目

上の `hotbar.slots` は1枠だけの抜粋。現行Fabric adapterは空枠を含む0〜8の9行を送る。

- `name`
- `position`
- `yaw`
- `pitch`
- `health`
- `hunger`
- `dimension`
- `held_item`
- `block_breaking_active`（クライアント上で現在ブロック破壊を継続中か）
- `hotbar`（Fabric adapterは0〜8の全枠を送る）
  - `selected_slot`: 現在選択中の0〜8
  - `slots[].slot`: 安定した0〜8のslot番号
  - `item_id / count`: namespaced item IDと個数。空枠はitem IDを省略し `count: 0`
  - `damage / max_damage`: 消費済み耐久と最大耐久
  - `attack_damage`: main handへ装備した場合の実測属性値
  - `weapon_kind`: `empty | sword | trident | axe | bow | tool | other`。候補分類であり、選択判断はserver
- `vehicle`（**乗車中だけ存在**。未乗車時はキーごと省略）
  - `vehicle_id`: 乗っているエンティティの Minecraft ID
  - `activity`: `riding | moving | running | rowing | dashing`
  - `controlling`: プレイヤーが操縦者か

`vehicle` の内部値を直接 LLM に見せず、server が
`プレイヤーはウマに乗って走っている` のような主語付き観測事実へ変換する。
「プレイヤー」を省略してドギド自身の行動に見せない。船の `rowing` は操縦者かつ
パドル動作中だけ、馬系の `running` は操縦者かつ方向入力＋水平移動中だけとする。

エリトラ飛行は乗り物ではないため、この `vehicle` には含めない。将来の
`player_activity` 拡張として別途扱う。

## 10. `world` オブジェクト

```json
{
  "time_of_day": 13000,
  "time_phase": "night",
  "weather": "clear",
  "biome": "plains",
  "local_light": 7,
  "sky_visible": false,
  "surface_y": 72,
  "depth_below_surface": 48,
  "ceiling_height": 3,
  "overhead_cover_type": "stone",
  "enclosure_score": 0.68,
  "connected_dark_volume": 42,
  "nearest_dark_spawn_distance": 5.5,
  "danger_darkness_score": 0.81
}
```

### 項目

- `time_of_day`
- `time_phase`
  - `morning`, `day`, `evening`, `night`
- `weather`
  - `clear`, `rain`, `thunder`
- `biome`
- `local_light`
- `sky_visible`
- `surface_y`（現在X/Zの地表高。地表高を意味づけられないdimensionでは省略）
- `depth_below_surface`（`surface_y` と現在Yの差。絶対Yより地下判定を安定させる）
- `nearby_window_present`（周囲8ブロック以内に、両側へ視界が通るガラス・格子・柵などの窓があるか。水に接するガラスは水槽等と区別して除外）
- `ceiling_height`
- `overhead_cover_type`（`stone | earth | ore | wood | foliage | fluid | solid | none`）
- `enclosure_score`
- `connected_dark_volume`
- `nearest_dark_spawn_distance`
- `danger_darkness_score`

### 注意

- `enclosure_score` は補助指標
- 暗所判定は `danger_darkness_score` を優先する

## 11. `visual_threats`

視認できている敵の一覧。

```json
[
  {
    "type": "creeper",
    "distance": 5.8,
    "direction": { "horizontal": "back", "cardinal": "southeast", "vertical": "same" },
    "approaching": true,
    "fuse_active": true,
    "certainty": "high"
  }
]
```

### 項目

- `type`
- `distance`
- `direction`
- `approaching`
- `fuse_active`（クリーパー／帯電クリーパーが膨らみ始め、導火中なら `true`）
- `certainty`

### 方針

- 視認済みなので具体名を送ってよい
- プレイヤーへの発話でも具体名を使ってよい
- 通常敵の索敵半径は16ブロック。見通しの通る敵だけを `visual_threats` に載せる
- `どっち？` などの現在位置質問には `direction.cardinal` と `distance` をコードで答える

## 12. `auditory_threats`

音だけで検知した脅威の一覧。

```json
[
  {
    "label": "hostile_presence",
    "sound_event": "minecraft:entity.witch.ambient",
    "direction": { "horizontal": "left", "vertical": "same" },
    "distance_band": "close",
    "certainty": "low",
    "spoken_name_allowed": false
  }
]
```

### 項目

- `label`
- `sound_event`
- `direction`
- `distance_band`
- `certainty`
- `spoken_name_allowed`

### 方針

- 未視認敵の exact position は送らない
- 未視認敵の entity id は送らない
- `sound_event` は内部処理用には持ってよい
- ただし `spoken_name_allowed=false` の間は、発話で具体名を出さない

### 推奨 `label`

- `hostile_presence`
- `hostile_voice_like`
- `movement_like`
- `explosive_threat_like`

## 12.1 `ambient_sounds`

戦闘判定に使わない周囲音。非敵対 Mob の声に加え、クライアントで実際に再生された
ブロック・天候・環境音を載せる。

```json
[
  {
    "type": "block:campfire",
    "sound_event": "block.campfire.crackle",
    "direction": { "horizontal": "right", "vertical": "same" },
    "distance_band": "close",
    "certainty": "medium"
  }
]
```

- `type=block:* / weather:* / environment:*` は実再生された `sound_event` から機械的に決める
- 近くにブロックがあるだけで「その音がした」と推測しない
- Mob 音は従来どおり、敵対中なら `auditory_threats`、非敵対なら `ambient_sounds`
- 音源位置は粗い方向・距離帯だけを発話材料にし、exact position は渡さない
- UI・音楽・プレイヤー自身の声は周囲音から除外する。ジュークボックスのレコードは対象

## 13. `passive_mobs`

旧スキーマ名 `peaceful_mobs` も受信時には受け付ける（移行用）。非敵対状態の中立モブも `temperament="neutral"` として含まれる。

昼の雑談に使う平和 mob の一覧。

```json
[
  {
    "type": "rabbit",
    "distance": 6.2,
    "direction": { "horizontal": "front_left", "vertical": "same" },
    "certainty": "high"
  },
  {
    "type": "villager",
    "distance": 8.0,
    "direction": { "horizontal": "front", "vertical": "same" },
    "certainty": "high",
    "is_baby": false,
    "profession": "farmer",
    "villager_type": "plains"
  }
]
```

村人のみ任意:

| フィールド | 説明 |
|---|---|
| `is_baby` | 子供か |
| `profession` | `none`（求職者）/ `nitwit`（ニート）/ `farmer` 等 |
| `villager_type` | 見た目種（plains 等） |

日課はサーバが `world.time_of_day` と上記から解決する（[villager-context-plan.md](villager-context-plan.md)）。

## 14. `inventory`

プレイヤー本人の所持品。

```json
{
  "torch": 0,
  "coal": 3,
  "stick": 5,
  "bed": 0,
  "wool": 2,
  "oak_log": 4
}
```

### 方針

- キーは Minecraft の item id ベース
- 値は所持数
- ドギド側で松明やベッド材料の判定に使う

## 14b. `command_results`

serverから受けた型付き支援commandの実行結果。adapterはackされるまで後続イベントへ再添付する。

```json
[
  {
    "command_id": "cmd_01JY2ABCXYZ",
    "command_type": "select_hotbar",
    "status": "succeeded",
    "executed_at": "2026-08-15T12:00:01.510+09:00",
    "selected_slot": 2,
    "selected_item_id": "minecraft:stone_sword",
    "detail_code": "selected"
  }
]
```

- `status`: `succeeded | rejected | failed | expired`
- staleなhotbar観測と現在のitemが一致しない場合は `rejected / expected_item_mismatch`
- serverはcommand IDで発行記録と相関し、`acknowledged_command_ids`をgame-event応答へ返す
- server → adapter のcommand本文はイベントではなく [Adapter API §8](adapter-api.md#8-post-apiv1game-events) の応答に載る

## 15. `nearby_resources`

周辺にある取得候補ブロックや資源。

```json
[
  {
    "type": "block",
    "name": "coal_ore",
    "distance": 4.0,
    "direction": { "horizontal": "right", "vertical": "below" }
  },
  {
    "type": "block",
    "name": "oak_log",
    "distance": 9.0,
    "direction": { "horizontal": "front", "vertical": "same" }
  }
]
```

現行 adapter は用途限定フィルタ（原木・板・羊毛・石炭鉱石に加え、
積雪の実測用 `snow` / `snow_block` / `powder_snow`）。雪は標高やバイオーム名だけで
「積もっている」と推測せず、この実ブロック観測を川柳・雑談の共通根拠にする。
**指差しの花・感圧板等は `look_target` を使う**（[look-target-observation-plan.md](look-target-observation-plan.md)）。

## 15a. `dropped_items` / `recent_block_breaks`

`dropped_items` はクライアントに読み込まれている周囲8ブロック以内の落下物を、
item IDごとに集約した観測。`age_ms` は同種のうち最も新しい個体を表す。

```json
{
  "dropped_items": [
    {
      "name": "cobblestone",
      "count": 3,
      "entity_count": 2,
      "distance": 1.4,
      "age_ms": 850,
      "block_item": true,
      "mining_related": true
    }
  ],
  "recent_block_breaks": [
    { "name": "stone", "material": "stone", "age_ms": 900 }
  ]
}
```

- `dropped_items` だけで採掘中とは断定しない。投棄・爆発・別原因の可能性がある
- `recent_block_breaks.material` は `stone | earth | ore | other`
- serverは、空が見えないこと、採掘道具、直近の破壊実績を主根拠に「採掘中」を確定する
- 石・土系の天井、地表からの深さ、屋内設備の不在だけなら「坑道らしい場所」に留める
- 時刻・天候と非洞窟バイオームは、空が見えない場面の対話・川柳材料へ投影しない

## 15b. `look_target`

画面中央クロスヘア（＋）が刺さっている対象。プレイヤーの「これ何？」の共有注意。

```json
{
  "kind": "block",
  "name": "poppy",
  "distance": 2.4
}
```

| フィールド | 内容 |
|---|---|
| `kind` | `block` / `entity` |
| `name` | Minecraft id path（例: `oak_pressure_plate`, `sheep`） |
| `distance` | プレイヤーからの距離（任意） |

MISS・空気のときは **フィールド自体を省略**する。

## 16. `combat`

戦闘や被弾に関する最近の状態。

```json
{
  "recent_damage_ms": 1200,
  "recent_hostile_visual_ms": 300,
  "recent_hostile_audio_ms": 900,
  "hostiles_within_7": 1,
  "hostiles_within_10": 2,
  "hostile_scan_distance": 16,
  "hostiles_within_scan_ground": 2,
  "hostile_outcomes": [
    {
      "entity_id": "0f6c…",
      "type": "zombie",
      "outcome": "player_kill",
      "evidence": "server_death_event"
    }
  ],
  "combat_active_hint": true
}
```

### 方針

- 生データだけでなく、状態機械がすぐ使える集約値も持たせてよい
- `hostile_scan_distance` は通常敵の索敵半径、`hostiles_within_scan_ground` はその範囲内の地上系敵数
- `hostile_outcomes` は、追跡中の個体について実際の死亡またはクリーパー爆発を観測した結果。一覧要素は `entity_id / type / outcome / evidence`
- `entity_id` は同じ結果を即時発話と戦闘終了で二重に話さないための個体ID。旧adapterでは省略可
- `outcome` は `player_kill / explosion_death / other_death / creeper_detonation`
- `evidence` は `server_death_event / client_death_state / explosion_packet`
- `player_kill` は論理サーバーの死亡イベントで `DamageSource` の攻撃者が当該プレイヤーだった場合だけ。攻撃履歴、経験値、敵数0、観測範囲からの消失だけでは付けない
- リモートサーバーでクライアント死亡状態しか取れない場合は `other_death` とし、プレイヤー撃破へ推測しない
- `creeper_detonation` は実際の爆発パケットと、直前まで追跡したクリーパー個体の消失が位置・時刻とも対応した場合だけ
- 通常／帯電クリーパーの導火開始は `visual_threats[].fuse_active`、実爆発は一回限りの `creeper_detonated` で通知する。死亡音はどの結果の根拠にも使わない
- 新adapterは結果なしを空配列で送る。項目自体が無い場合は旧adapterとして扱う

## 17. `meta`

任意の補助情報。

```json
{
  "adapter_build": "0.1.0",
  "profile_name": "main-player",
  "debug": false
}
```

## 18. 発話制限ルール

### 視認由来

- 具体名を出してよい
- 後ろ警告を出してよい
- `8` マス以内は高優先度

### 音由来

- 方向は出してよい
- 基本は存在中心
- 具体名は原則出さない
- 以前に視認していた敵を記憶から参照する場合のみ、控えめな推定表現を許可する

### 推定由来

- `暗い`, `湧きそう`, `この先危ない` のような表現に留める

## 19. 主要イベントごとの最低要件

### `threat_detected`

- `player`
- `world`
- `visual_threats` または `auditory_threats`

### `threat_approaching`

- `player`
- `world`
- `visual_threats`
- `combat`

### `hostile_audio_detected`

- `player`
- `world`
- `auditory_threats`

### `danger_darkness_changed`（レガシー / テスト用）

- `player`
- `world`
- `inventory`
- 任意で `nearby_resources`

> 現行の本番経路では、暗所判定の入力は主に `status_snapshot`（および他イベント同梱の `world`）のスコア群。  
> 専用イベント発火に依存した反応設計はしない。

### `resource_option_found`（レガシー寄り）

- `player`
- `inventory`
- `nearby_resources`

> 現行は `status_snapshot` 等への `nearby_resources` 同梱で代替する。

### `ambient_mob_detected`

- `player`
- `world`
- `passive_mobs`

### `player_died`

- `player`
- `world`
- `meta.death_cause`

### `time_phase_changed`（レガシー寄り）

- `player`
- `world.time_phase`

> 現行は snapshot 等での `world.time_phase` 継続更新が本流。

### `combat_ended`

- `player`
- `world`
- `combat`

### `creeper_detonated`

- `player`
- `world`
- `combat.hostile_outcomes`（この通知で新たに確認した爆散だけ）

このイベントは驚き・慌てる即時反応用で、一つの爆散を一度だけ送る。送信済みの
個体は後続 `combat_ended` の敵名一覧へ持ち越さない。

### `hostile_defeated`

- `player`
- `world`
- `combat.hostile_outcomes`（この通知で新たに確認した死亡だけ）

実死亡を観測した時点の即時反応用。`player_kill` ならプレイヤーを褒め、
`explosion_death` なら爆発への驚き、`other_death` なら帰属を断定しない反応にする。
同じ `entity_id` は後続 `combat_ended` で再び話題にしない。

## 20. サンプル 1: 視認クリーパー接近

```json
{
  "schema_version": "2026-05-24",
  "game": "minecraft-java",
  "adapter": "dogido-fabric-client",
  "observed_at": "2026-05-24T14:32:10.512+09:00",
  "sequence": 1842,
  "event": {
    "name": "threat_approaching",
    "source_kind": "visual",
    "priority_hint": "urgent",
    "certainty": "high"
  },
  "player": {
    "name": "main_player",
    "position": { "x": 0, "y": 64, "z": 0 },
    "yaw": 180.0,
    "pitch": 0.0,
    "health": 20,
    "hunger": 18,
    "dimension": "minecraft:overworld",
    "held_item": "stone_sword"
  },
  "world": {
    "time_of_day": 13000,
    "time_phase": "night",
    "weather": "clear",
    "biome": "plains",
    "local_light": 7,
    "sky_visible": false,
    "ceiling_height": 3,
    "enclosure_score": 0.68,
    "connected_dark_volume": 42,
    "nearest_dark_spawn_distance": 5.5,
    "danger_darkness_score": 0.81
  },
  "visual_threats": [
    {
      "type": "creeper",
      "distance": 5.8,
      "direction": { "horizontal": "back", "vertical": "same" },
      "approaching": true,
      "certainty": "high"
    }
  ],
  "auditory_threats": [],
  "inventory": {
    "torch": 0,
    "coal": 3,
    "stick": 5,
    "bed": 0
  },
  "combat": {
    "recent_damage_ms": 999999,
    "recent_hostile_visual_ms": 50,
    "recent_hostile_audio_ms": 220,
    "hostiles_within_7": 1,
    "hostiles_within_10": 1,
    "combat_active_hint": true
  }
}
```

## 21. サンプル 2: 音だけの敵気配

```json
{
  "schema_version": "2026-05-24",
  "game": "minecraft-java",
  "adapter": "dogido-fabric-client",
  "observed_at": "2026-05-24T14:33:02.101+09:00",
  "sequence": 1854,
  "event": {
    "name": "hostile_audio_detected",
    "source_kind": "auditory",
    "priority_hint": "normal",
    "certainty": "low"
  },
  "player": {
    "name": "main_player",
    "position": { "x": 0, "y": 64, "z": 0 },
    "yaw": 180.0,
    "pitch": 0.0,
    "health": 20,
    "hunger": 18,
    "dimension": "minecraft:overworld",
    "held_item": "torch"
  },
  "world": {
    "time_of_day": 13500,
    "time_phase": "night",
    "weather": "clear",
    "biome": "plains",
    "local_light": 10,
    "sky_visible": false,
    "ceiling_height": 8,
    "enclosure_score": 0.40,
    "connected_dark_volume": 75,
    "nearest_dark_spawn_distance": 4.0,
    "danger_darkness_score": 0.74
  },
  "auditory_threats": [
    {
      "label": "hostile_presence",
      "sound_event": "minecraft:entity.witch.ambient",
      "direction": { "horizontal": "left", "vertical": "same" },
      "distance_band": "close",
      "certainty": "low",
      "spoken_name_allowed": false
    }
  ]
}
```

## 22. サンプル 3: 暗所危険

本番 adapter では `status_snapshot` に暗所スコアを載せる形が本流。  
以下は互換例（`danger_darkness_changed` も受理するが主経路ではない）。

```json
{
  "schema_version": "2026-05-24",
  "game": "minecraft-java",
  "adapter": "dogido-fabric-client",
  "observed_at": "2026-05-24T14:35:40.000+09:00",
  "sequence": 1901,
  "event": {
    "name": "status_snapshot",
    "source_kind": "system",
    "priority_hint": "background",
    "certainty": "high"
  },
  "player": {
    "name": "main_player",
    "position": { "x": 0, "y": 20, "z": 0 },
    "yaw": 45.0,
    "pitch": -5.0,
    "health": 20,
    "hunger": 15,
    "dimension": "minecraft:overworld",
    "held_item": "stone_pickaxe"
  },
  "world": {
    "time_of_day": 14000,
    "time_phase": "night",
    "weather": "clear",
    "biome": "dripstone_caves",
    "local_light": 9,
    "sky_visible": false,
    "ceiling_height": 20,
    "enclosure_score": 0.55,
    "connected_dark_volume": 180,
    "nearest_dark_spawn_distance": 3.0,
    "danger_darkness_score": 0.88
  },
  "inventory": {
    "torch": 0,
    "coal": 3,
    "stick": 2
  },
  "nearby_resources": [
    {
      "type": "block",
      "name": "coal_ore",
      "distance": 4.0,
      "direction": { "horizontal": "right", "vertical": "below" }
    }
  ]
}
```
