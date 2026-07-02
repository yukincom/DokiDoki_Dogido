# モンスター調査メモスキーマ

この文書は、Minecraft のモンスターデータを調査メモとして保存するためのスキーマです。

runtime の正本は [data/catalogs/entries/mobs](/Users/yukin_co/Documents/DokiDoki-Dogido/data/catalogs/entries/mobs) です。
ここで説明する YAML は、Wiki から抽出した事実や未確定メモを残すための資料用フォーマットです。

## 1. 基本方針

- runtime の正本は `mobs/`
- `YAML` は資料・調査メモ
- `事実` と `ドギド用解釈` を分ける
- バージョン差や難易度差を最初から入れられる形にする
- 未確認情報は `null` や `confidence: low` で持つ

## 2. 推奨ディレクトリ構成

```text
data/
  catalogs/
    entries/
      mobs/
        schema.json
        hostile.json
        neutral.json
        passive.json
  mobs/
    ambient_reactions.json
docs/
  research/
    mobs/
      _template.monster.yaml
      zombie.yaml
      creeper.yaml
      skeleton.yaml
      witch.yaml
      enderman.yaml
      ender_dragon.yaml
      wither.yaml
      warden.yaml
      elder_guardian.yaml
```

## 3. YAML を資料に残す理由

- 人間が追記しやすい
- コメントを書きやすい
- 未確定値を残しやすい
- LLM やスクレイパからの中間生成物をあとで直しやすい

## 4. スキーマの考え方

1 ファイルに以下の 4 層を持つ。

### `identity`

- 名前
- カテゴリ
- 次元
- 性質

### `canonical`

- Wiki などから取ったゲーム内事実
- 体力
- 攻撃
- スポーン
- 振る舞い
- 難易度差

### `adapter_hints`

- Minecraft client adapter が何を観測できると嬉しいか
- 視認、音、距離、方向

### `dogido`

- ドギドがどう扱うか
- 危険分類
- panic 方針
- 警告距離
- 発話優先度
- 使いたい短文

## 5. 必須フィールド

最低限これだけあれば使える。

- `schema_version`
- `id`
- `identity.name_ja`
- `identity.name_en`
- `identity.category`
- `identity.default_disposition`
- `canonical.stats.health`
- `canonical.movement.primary_style`
- `dogido.threat_class`
- `dogido.priority`

## 6. 推奨フィールド一覧

```yaml
schema_version: 1
id: zombie

identity:
  name_ja: ゾンビ
  name_en: Zombie
  category: overworld_hostile
  family: undead
  default_disposition: hostile
  dimensions:
    - overworld

source:
  primary_urls: []
  extraction_notes: []
  last_reviewed: "2026-05-25"
  overall_confidence: medium

canonical:
  stats:
    health: 20
    armor: null
    size:
      width: 0.6
      height: 1.95
    attack_damage:
      easy: 2
      normal: 3
      hard: 4

  spawn:
    light_rule:
      type: threshold
      max_block_light: 7
    player_distance:
      min: 24
      max: 128
    group_size: null
    special_cases: []

  aggro:
    detection_range: null
    triggers:
      - type: sight
        target: player
    loses_aggro_conditions: []

  movement:
    primary_style: walk
    speed:
      qualitative: medium
      numeric_blocks_per_sec: null
    special_movement: []
    pathing_notes: []

  attacks:
    primary:
      type: melee
      range: close
    secondary: []

  difficulty_overrides:
    easy: []
    normal: []
    hard: []

  special_behaviors: []
  weaknesses: []
  resistances: []

adapter_hints:
  visual_signals: []
  audio_signals: []
  derived_metrics: []
  must_detect:
    - visual_distance
    - visual_direction
  nice_to_have: []

dogido:
  threat_class: melee
  priority: high
  panic_policy: normal
  rear_warning_range: 8
  panic_range: 7
  audio_name_safe: false
  callouts:
    seen: []
    heard: []
    aftermath: []
  notes: []

quality:
  status: draft
  missing_fields: []
```

## 7. 難易度差の持ち方

難易度差は `canonical.difficulty_overrides` に集約する。

```yaml
difficulty_overrides:
  hard:
    - id: break_wooden_door
      effect: can_break_wooden_doors
      confidence: medium
    - id: call_reinforcements
      effect: can_spawn_reinforcements_when_hit
      confidence: medium
```

### ここに入れたいもの

- 攻撃力差
- 攻撃間隔差
- 命中精度差
- ドア破壊
- 増援呼び
- 状態異常の長さ

## 8. 移動の癖の持ち方

移動の癖は `canonical.movement` に入れる。

```yaml
movement:
  primary_style: teleport
  speed:
    qualitative: fast
    numeric_blocks_per_sec: null
  special_movement:
    - type: teleport
      purpose: close_distance
      trigger: damaged_or_chasing
      confidence: high
```

### `primary_style` の候補

- `walk`
- `run`
- `teleport`
- `fly`
- `swim`
- `burrow`
- `charge`

## 9. 遅延トリガーの持ち方

エンダーマンのように「見た瞬間ではなく少し間がある」ものは `aggro.triggers` に遅延を持たせる。

```yaml
aggro:
  triggers:
    - type: eye_contact
      max_distance: 64
      trigger_delay:
        game_ticks: 5
        confidence: low
      break_on_lost_contact: true
```

### ここに入れたいもの

- 視線を合わせ続ける必要があるか
- 何 tick で敵対するか
- 射線が切れたら解除されるか
- 近づきすぎだけで敵対するか

## 10. `confidence` の基準

- `high`
  - 明確な一次情報や複数ソースで裏取りできた
- `medium`
  - Wiki 記述はあるが、細部の条件や版差が怪しい
- `low`
  - 実測メモや会話ベース、未検証

## 11. 推奨 enum

### `identity.category`

- `overworld_hostile`
- `overworld_neutral`
- `nether_hostile`
- `nether_neutral`
- `end_hostile`
- `raid_hostile`
- `aquatic_hostile`

### `dogido.threat_class`

- `explosive`
- `melee`
- `ranged`
- `ambush`
- `teleport`
- `swarm`
- `tank`
- `trickster`

### `dogido.priority`

- `critical`
- `high`
- `medium`
- `low`

### `dogido.panic_policy`

- 省略時は `normal` とみなしてよい
- `normal`
  - 通常の `panic` ルールを使う
- `reveal_only`
  - 初回の出現や召喚時だけ悲鳴を許可し、その後は状況説明と助言を優先する
- `tactical`
  - 継続的な panic を抑え、方向や攻撃タイミングの案内を優先する

## 12. 収集時に削ってよい情報

初期実装では以下は後回しでよい。

- 細かいドロップ率
- トリビア
- 版ごとの履歴
- 全アイテム一覧
- 長い攻略文

## 13. 収集時に優先したい情報

- 体力
- 攻撃方式
- 難易度差
- 移動の癖
- 敵対条件
- 逃げ条件
- 日光、水、遮蔽物への反応
- 音で識別しやすいか

## 14. サンプル運用ルール

- 最初は 5 体で十分
  - ゾンビ
  - クリーパー
  - スケルトン
  - ウィッチ
  - エンダーマン
- 未確定の数値は空欄で残す
- 先に `canonical` を埋めてから `dogido` を埋める
