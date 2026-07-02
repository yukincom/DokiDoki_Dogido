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
  "combat": {},
  "meta": {}
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
- `inventory`
- `combat`

### 任意

- `passive_mobs`
- `nearby_resources`
- `meta`

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
  "vertical": "same"
}
```

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

初期実装では以下を採用する。

- `threat_detected`
- `threat_approaching`
- `hostile_audio_detected`
- `danger_darkness_changed`
- `resource_option_found`
- `ambient_mob_detected`
- `player_died`
- `time_phase_changed`
- `combat_ended`
- `status_snapshot`

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
  "held_item": "stone_sword"
}
```

### 項目

- `name`
- `position`
- `yaw`
- `pitch`
- `health`
- `hunger`
- `dimension`
- `held_item`

## 10. `world` オブジェクト

```json
{
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
- `ceiling_height`
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
    "direction": { "horizontal": "back", "vertical": "same" },
    "approaching": true,
    "certainty": "high"
  }
]
```

### 項目

- `type`
- `distance`
- `direction`
- `approaching`
- `certainty`

### 方針

- 視認済みなので具体名を送ってよい
- プレイヤーへの発話でも具体名を使ってよい

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
  }
]
```

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

## 16. `combat`

戦闘や被弾に関する最近の状態。

```json
{
  "recent_damage_ms": 1200,
  "recent_hostile_visual_ms": 300,
  "recent_hostile_audio_ms": 900,
  "hostiles_within_7": 1,
  "hostiles_within_10": 2,
  "combat_active_hint": true
}
```

### 方針

- 生データだけでなく、状態機械がすぐ使える集約値も持たせてよい

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

### `danger_darkness_changed`

- `player`
- `world`
- `inventory`
- 任意で `nearby_resources`

### `resource_option_found`

- `player`
- `inventory`
- `nearby_resources`

### `ambient_mob_detected`

- `player`
- `world`
- `passive_mobs`

### `player_died`

- `player`
- `world`
- `meta.death_cause`

### `time_phase_changed`

- `player`
- `world.time_phase`

### `combat_ended`

- `player`
- `world`
- `combat`

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

```json
{
  "schema_version": "2026-05-24",
  "game": "minecraft-java",
  "adapter": "dogido-fabric-client",
  "observed_at": "2026-05-24T14:35:40.000+09:00",
  "sequence": 1901,
  "event": {
    "name": "danger_darkness_changed",
    "source_kind": "inferred",
    "priority_hint": "normal",
    "certainty": "medium"
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
