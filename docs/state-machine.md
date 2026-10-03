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

- 安堵と労いの短い `speech`
- 明示 `combat_ended` では現在の視認敵・敵音・周辺敵数が空かをコード確認する
- 確認済みなら古い戦闘音声を割り込み停止し、残敵を疑う言い方はしない
- `player_kill / explosion_death / other_death / disengaged` を分け、死亡根拠なしで撃破を断定しない
- 余韻文の敵名は今回の結果、または今回最後に追った敵だけ。一度話した戦闘主語は消費し、別の敵の離脱へ持ち越さない

### クリーパーの導火・爆散

- `fuse_active` が `false` から `true` へ変わった時だけ、方向つきで慌てる
- 複数体の数え上げでも通常／帯電を別種として扱い、例: `帯電クリーパー2体おるで` と知らせる
- 導火中の同一個体へ毎フレーム同じ警告を繰り返さない。いったん導火が戻れば再び反応できる
- 実爆発の `creeper_detonated` は即時割り込みで驚き、通常／帯電を言い分ける
- 爆散を一度話題にしたら、後の `combat_ended` で同じ個体をもう一度列挙しない

### 通常敵の索敵と方位応答

- 通常敵はプレイヤーから16ブロック以内かつ、見通しを確認できた個体だけを視認脅威として扱う
- `どっち？` と聞かれた時は、現在視認中の最寄りの敵について絶対方位8方位と概算距離を返す
- 例: `えーと……南東や。だいたい12ブロック先くらいやな。`
- 方位・距離はLLMに推測させず、adapterの座標観測からコードで組み立てる

### 敵の死亡と安全確認

- `hostile_defeated` は実死亡を観測した時点で即時に一度だけ反応する
- `player_kill` は褒め、`explosion_death` は爆発へ驚き、`other_death` は誰が倒したか断定しない
- 後続 `combat_ended` は周囲が安全になったことへの安堵に役割を分ける
- 即時反応済みの `entity_id` は戦闘終了の余韻文から除外する

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

- 明示 `combat_ended` で現在の視認敵・敵音・周辺敵数が空

### `suppressed_panic -> aftermath`

以下を満たすと遷移する。

- 明示 `combat_ended` で現在の視認敵・敵音・周辺敵数が空

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

- プレイヤー撃破確認あり: `よっしゃ、倒せたな！ 怖かったけど、ほんまお疲れさんや。`
- 敵の死亡だけ確認: `敵は倒れたみたいやな。あー……怖かったわ。`
- 死亡未確認・観測範囲から離れた: `あー……怖かったぁ。ひとまず気配は遠のいたみたいやな。`

### 開始時処理

- `aftermath_until = now + aftermath_time_ms`
- `last_combat_end_at = now`
- `combat_ended` の視認敵・敵音・7/10/30マス周辺敵数が空であることをコードで確認する
- 確認済みの開始発話は `speech + interrupt` とし、再生中・待機中の古い戦闘キューを破棄する
- LLM には「戦闘終了・現在の観測範囲で残敵0」と、今回の `hostile_outcomes`、または今回最後に追った敵名だけを渡す。会話用の直近視認・聴取メモは混ぜない
- `player_kill` / `explosion_death` / `hostile_defeated` / `disengaged` をコードで確定する。`disengaged` では「倒した」「退治した」と言わせない
- ウォーデンとエンダードラゴンは、従来どおり各ボス専用の討伐確認を別途必須とする

## 14. 暗所リスク時の会話優先度

暗所リスクは `alert` を引き起こすが、即 `panic` にはしない。

### 優先順位

- 敵接近
- 被弾
- 複数敵
- 実雷鳴・近距離落雷
- 地表夕方の一度だけの注意
- 暗所危険
- プレイヤー主体の会話
- 非敵対ambient

### プレイヤー主体の会話所有権

- `none / casual / learning / web / haiku_workshop` の一つだけをsession内foregroundとして扱う
- `casual / learning / web / haiku_workshop` 中は、友好・中立Mobのambient発話を止める。敵対警告は止めない
- hostileで `casual / learning` を一件だけ保留し、戦闘後10件の受理済みplayer turn以内に明示再開されなければ破棄する。game tickと自動calloutは数えない
- 国語・語句の明示質問と学習中の続きは有界workerへ渡し、完了結果を次の安全なgame eventで回収する。正本DBの明示知識回答、戦況・assist・workshopは状態機械側に残す
- 利用前提が揃うMacでは、Web同意と案内音声の実再生完了後だけ、同じ有界workerで専用Chromeへ検索を一度開く。調査中は `web` がforegroundを所有し、Web用30分期限までambientと発句時計を止める
- assistant本文は選択時でなく、発話IDに対応する実再生 `completed` を回収した後だけ5往復履歴へ入れる
- 学習中の無関係な別話題は、直前会話から2分以上または明示名指し／転換なら本体chatへ即時移管する。2分未満で宛先不明なら5分だけ無言保留し、呼び直しの確認音声が完了してから同じ元turnを一度だけ移管する
- 有効な本人入力は再生中音声へのbarge-inになる。純粋な音声叫声は通常会話へ入れず、コード観測の状況メモだけを使う
- 危険前の通常5往復は危険中に保護し、危険終了後の通常player turn 3件目まで共有する。戦況発話と叫び声はこの履歴を消費しない
- 詳細と確認済みのMinecraft自動ポーズは [main-dialogue-integration.md](main-dialogue-integration.md)

### 暗所助言フロー

1. 松明があるなら使用を促す
2. 松明がないが材料があれば作成を促す
3. 材料が足りず周辺資源があれば取得を促す
4. ベッドがあれば使用を促す
5. ベッドを作れるなら作成を促す
6. それも無理なら帰宅を促す

### 照明器具が増えたときの発話

- `status_snapshot` の前回値との差から分かるのは、松明・魂の松明・ランタン類の所持総数が増えたことだけ。クラフト、設置、拾得、持ち替えのどれかは推定しない
- 半スタック以上を持ち、周囲が既存の暗所警告条件に当たらない場合は即時に無言。直近5分に同種コメント済みの場合と、現在も危険な暗さの場合も無言にする
- それ以外だけ、有界 `light_source_comment_plan` が `stay_silent / acknowledge_supply_gain / relief_after_darkness` の一件を選ぶ。未知action・未知basis・低信頼の発話判断は無言へ落とす
- `dark_push` の停止可否は従来どおり現在の明るさ・危険度でコード判定する。所持数が増えただけでは停止せず、実際に回復していればplannerが無言を選んでも呼吸音と内部stageは停止する
- 発話leafへ正確な本数は渡さず、未観測の入手方法や本数を述べた生成結果は固定fallbackへ置き換える

### 簡易シェルター

- `cardinal_wall_count` と `ceiling_height` は簡易シェルターの形状判定に使う
- 形状だけで昼・夕方に「避難できた」とは発話しない。入場安堵は夜のみ
- `respawn_point_set` + 近距離の `respawn_distance` + `nearby_bed_count` は自宅拠点として扱い、緊急避難扱いしない
- 同じ夜は、形状観測が一瞬外れて戻っても入場安堵を繰り返さない

### 雑談用の安全方針と帰宅予定

- 雑談には `time_phase` と、コード導出の `safety_priority` を別々に渡す
- 地表の夕方または雷雨では `seek_safe_place`。夜警告と同じ場所判定を共有し、洞窟バイオーム・水中・空が見えない場所・安全な屋内ではオフ
- 安全方針は保存せず毎フレーム再計算するため、朝昼や雷雨終了で自動的に `none` へ戻る
- 「家へ帰る」は現在の発話に明示されたターンだけ `player_turn_plan=return_home` とし、短期目標メモリにはしない
- `respawn_point_set` と直近の `respawn_distance` 複数サンプルは接近傾向の補助にだけ使い、単発差分や道具使用からプレイヤー目標を推測しない

### 雷鳴反応

- 実雷鳴・近距離落雷の悲鳴 cue は共通タイマーで10分抑制し、雷への短い一言は別タイマーで3分抑制する
- 一言は LLM leaf で「鳴り続ける雷への小さな独り言」に調整し、LLM 利用不可時は固定 fallback を使う
- Rust本体ではこの一言を、会話モデルが実観測と完了会話から「話す／黙る」を含めて選ぶ。実雷鳴と近傍落雷はそれぞれの観測から区別し、天候区分だけで聞こえた扱いにしない。悲鳴cueはモデル生成に先行し、戦闘への切替で生成を取り消す
- 最初の実雷鳴／近距離落雷はplayer replyより先に割り込み、同じ入力を次tickへ戻す。会話所有権と履歴は消さない
- 以後cooldown後の一言は非割り込みで小さく心配する。player_chatを返したことを理由に雷自体を3分無視しない
- 天候が雷の間、地表では友好・中立 Mob の ambient 発話を止める。洞窟バイオームでは天候値を無視して Mob 反応を維持する
- 敵対 Mob の警告・戦闘 cue はこの雷専用タイマーの対象外

## 15. 視認と音の扱い

### 視認脅威

- 具体名を使ってよい
- `後ろのクリーパー` のような強い警告を許可

### 音脅威

- 方向中心
- `なんか左奥で声する`
- 断定は抑える

### 匂い

- `smell_observation` はadapter側の[スメルバトル](smell-policy.md)で解決済みの一件。`none / present / suppressed` を区別する
- 新しい `present` は同じsignatureを2観測連続で受けてから有効化し、境界で勝者が揺れた1frameを話さない
- 同じ優勢状態では一度だけ、消失後の再出現や別の勝者にも全体2分クールダウン
- Rust環境対話・明示質問は任意の粗い方向だけを使う。再生前に推定だけが変わった生成は取消し、2観測で安定した現在値へ一度だけ即時再判断する。再取消後は通常間隔を守り、無言選択・再生済み発話はやり直さない
- visual／auditory脅威と同frameなら自発匂い発話を後回しにするが、匂いの存在状態は失わない
- 特定ゾンビは従来の警告優先度、それ以外はplayer inputと安全反応の後のambientに置く
- 匂い単独ではmodeを `normal` のまま保ち、combat activeやworkshop pauseを立てない
- 同tickにplayer inputがあれば返答を先にし、安定した未発話の匂いは次の安全なtickまで保持する

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
