# 状態機械

この文書は、ドギドの内部状態と状態遷移の現行仕様です。

入力イベントの形は [イベントスキーマ](event-schema.md) を参照します。

## 1. 目的

- ドギドのキャラクター性を LLM 任せにせず、コードで安定させる
- `危険時は会話より実況優先` を一貫して実現する
- `うるさい` 抑制と `余韻` を自然に扱う

## 2. 状態一覧

- `normal`
- `alert`
- `panic`
- `suppressed_panic`
- `aftermath`

## 3. 補助フラグと記憶

状態本体とは別に、以下の内部値を持つ。

- `shut_up_count`
- `suppression_started_at`
- `suppression_until`
- `aftermath_until`
- `last_visual_threat_at`
- `last_audio_threat_at`
- `last_damage_at`
- `last_combat_end_at`
- `panic_scream_cooldown_until`
- `last_confirmed_hostiles`
- `last_known_hostile_directions`

## 4. 派生シグナル

イベントから毎 tick または毎イベント時に以下を導出する。

### 脅威

- `nearest_visual_threat_distance`
- `visual_threat_count_within_7`
- `visual_threat_count_within_10`
- `has_approaching_visual_threat`
- `recent_hostile_audio_ms`
- `recent_hostile_visual_ms`

### 戦闘

- `recent_damage_ms`
- `combat_active_hint`
- `combat_end_candidate`

### 暗所

- `danger_darkness_score`
- `torch_available`
- `bed_available`
- `bed_craftable`

## 5. 初期パラメータ

以下は初期値であり、実機調整を前提とする。

- `rear_warning_distance = 8.0`
- `panic_distance = 7.0`
- `multi_hostile_distance = 10.0`
- `combat_clear_distance = 10.0`
- `combat_clear_time_ms = 5000`
- `suppression_time_ms = 7000`
- `aftermath_time_ms = 8000`
- `panic_scream_cooldown_ms = 1200`
- `recent_damage_window_ms = 3000`

## 6. 状態ごとの役割

### `normal`

- 平常時
- 雑談
- 昼の mob 解説
- 通常会話応答

### `alert`

- 危険の気配がある
- 暗所リスクがある
- 視認敵はまだ緊急距離ではない
- 短い警告と確認が増える

### `panic`

- 緊急距離の敵
- 複数敵
- 直近被弾

絶叫系悲鳴と短い方向警告を優先する。

### `suppressed_panic`

- `うるさい` 系を 3 回以上言われた後の一時抑制状態
- 絶叫を止め、弱い悲鳴と情報寄りコールアウトへ切り替える

### `aftermath`

- 戦闘終了直後
- 危険が去っても挙動不審さを残す

## 7. 音声レイヤ

状態機械は、発話を以下の 3 レイヤに振り分ける。

### `panic_cue`

- SD キャッシュ音声
- 絶叫系
- 最優先
- 既存 TTS を中断可能

### `callout`

- 短い状況説明
- 例: `後ろ！`, `右！`, `あと 2 体！`
- TTS だが高優先度

### `speech`

- 通常会話
- 助言
- 雑談

## 8. 状態ごとの出力方針

### `normal`

- `speech` のみ
- `panic_cue` は出さない

### `alert`

- `callout` または短い `speech`
- 必要に応じて暗所助言
- 会話応答はまだ可能

### `panic`

- `panic_cue` を許可
- `callout` は短く優先
- 通常会話はほぼ抑止

### `suppressed_panic`

- `panic_cue` は絶叫系を禁止
- 低刺激 cue または呼吸系 cue のみ
- `callout` を優先
- 戦闘が続く場合は `speech` をほぼ使わず、情報寄りに固定

### `aftermath`

- 弱い `speech`
- 安全確認
- まだ怖がっている雰囲気を残す

## 9. 基本遷移

### `normal -> alert`

以下のいずれかで遷移する。

- 敵を視認した
- 敵音を検知した
- `danger_darkness_score` が閾値を超えた

### `alert -> panic`

以下のいずれかで遷移する。

- `nearest_visual_threat_distance <= panic_distance`
- `visual_threat_count_within_10 >= 2`
- `recent_damage_ms <= recent_damage_window_ms`
- 高危険度の敵が `rear_warning_distance` 以内で後方にいる

### `panic -> suppressed_panic`

以下を満たすと遷移する。

- `shut_up_count >= 3`
- `combat_active_hint == true`

### `panic -> aftermath`

以下を満たすと遷移する。

- `combat_end_candidate == true`

### `suppressed_panic -> aftermath`

以下を満たすと遷移する。

- `combat_end_candidate == true`

### `alert -> normal`

以下を満たすと遷移する。

- 視認敵なし
- 音脅威なし
- 暗所危険も低い

### `aftermath -> normal`

以下を満たすと遷移する。

- `now >= aftermath_until`
- 新しい脅威がない

## 10. `combat_end_candidate`

以下をすべて満たすと真とみなす。

- `10` マス以内に敵がいない
- 直近 `4` 〜 `5` 秒で被弾していない
- 直近 `4` 〜 `5` 秒で敵音または敵視認更新がない

## 11. `panic` の詳細

### 目的

- プレイヤーに即座に危険を伝える
- ドギドらしさとして、取り乱しを表現する

### 許可する出力

- 絶叫系 cue
- 方向コールアウト
- 敵数コールアウト

### 禁止または抑制するもの

- 長い会話応答
- 雑談
- 攻略解説

### cue 条件

- `now >= panic_scream_cooldown_until` のときだけ新規 cue を出す
- cue 再生後に `panic_scream_cooldown_until = now + panic_scream_cooldown_ms`

## 12. `suppressed_panic` の詳細

### 発動

- `うるさい`, `静かにして`, `黙れ` をルールベースで検知
- 3 回以上で発動

### フェーズ 1: 低刺激悲鳴

発動直後から `suppression_time_ms` の間は以下を使う。

- `ハァハァ`
- `ひっ・・・・`
- `あかん・・・・`

### フェーズ 2: 情報寄りコールアウト

抑制時間経過後も交戦継続なら以下へ寄せる。

- `後ろ！`
- `右！`
- `あと 2 体！`
- `まだおる！`

### 抑制解除

以下のいずれか。

- `now >= suppression_until` かつ戦闘終了
- 戦闘終了により `aftermath` へ移行

## 13. `aftermath` の詳細

### 目的

- 安全になってもすぐ平常へ戻らない
- ドギドの怖がりキャラを残す

### 出力例

- `あー・・・こわかったぁ・・・`
- `ほんまにもうおらんかな`
- `まだちょっと嫌な感じする`

### 開始時処理

- `aftermath_until = now + aftermath_time_ms`
- `last_combat_end_at = now`

## 14. 暗所リスク時の会話優先度

暗所リスクは `alert` を引き起こすが、即 `panic` にはしない。

### 優先順位

- 敵接近
- 被弾
- 複数敵
- 暗所危険
- 通常会話

### 暗所助言フロー

1. 松明があるなら使用を促す
2. 松明がないが材料があれば作成を促す
3. 材料が足りず周辺資源があれば取得を促す
4. ベッドがあれば使用を促す
5. ベッドを作れるなら作成を促す
6. それも無理なら帰宅を促す

## 15. 視認と音の扱い

### 視認脅威

- 具体名を使ってよい
- `後ろのクリーパー` のような強い警告を許可

### 音脅威

- 方向中心
- `なんか左奥で声する`
- 断定は抑える

### 記憶を伴う場合

- 直前に視認していた敵なら、推定表現を許可
- 例: `さっきのウィッチ、向こうにまだおるかも`

## 16. 会話割り込み

### 通常会話中

- `panic` へ入ったら割り込む

### TTS 再生中

- `panic_cue` は即時割り込み
- `callout` も割り込み可

### 割り込み禁止

- `speech` は `panic_cue` や `callout` を止めない

## 17. 死亡イベント

`player_died` は状態とは別の高優先イベントとして扱う。

### 基本方針

- モンスター死でも強く責めない
- 事故死でも励ます

### 状態への影響

- 死亡時はアクティブ戦闘を終了扱いにする
- 復帰時は `aftermath` から始めてもよい

## 18. 実装優先度

1. `normal / alert / panic / aftermath` を先に作る
2. `suppressed_panic` を追加する
3. cue の種類とクールダウンを調整する
4. 暗所助言フローをつなぐ
5. 昼 mob 雑談と死亡フォローを拡張する
