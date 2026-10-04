# 村人コンテキスト（職業・子供・日課）

**作成:** 2026-07-18 · **更新:** 2026-10-04  
**状態:** 属性観測、日課計算、カタログ参照、ambientの抑制を実装済み。職場の近傍スキャンは任意の将来案。

関連: [イベント仕様](event-schema.md) · [状態機械](state-machine.md) · [モブ反応トーン](mob-interaction-tone.md) · [実機チェック](../dogido-rust/manual-dialogue-check.md)

## 1. 目的と現在の範囲

村人を一律の`villager`として扱うだけでなく、観測できた職業・年齢を会話へ渡す。ゲーム内時刻から求める日課は、実際に働いた・寝た・遊んだことの観測とは分ける。

| 項目 | 現在の扱い |
|---|---|
| 種別 | `passive_mobs[].type`は`villager` |
| 職業・子供・外見タイプ | Fabricが取得できた`profession`、`is_baby`、`villager_type`を送る |
| 日課 | Rustが時刻と年齢・職業から生活予定を求める。必要な情報がなければ未確認とする |
| 表示名 | カタログの職業名・子供ラベルを使い、不明な職業を求職者へ決めつけない |
| 周辺への一言 | コードが候補・優先度・間隔・睡眠帯の抑制を決め、その機会にモデルが発話／無言を選ぶ |

実装済みはコード上の到達点。移植後のMinecraft・マイク・TTSを通した確認結果は、実機チェックに別途記録する。

## 2. 責務分担

| 担当 | 処理 | 実装先 |
|---|---|---|
| Fabric | 村人の属性とゲーム時刻を取得・送信 | [DogidoClientAdapter.java](../adapter/minecraft-fabric/src/main/java/dogido/fabric/DogidoClientAdapter.java) |
| 受信契約 | 型・値域・省略を検査 | [events/models.rs](../dogido-rust/src/events/models.rs) |
| 日課の投影 | 現在観測中の村人に、時刻・属性から求めた生活予定を添える | [villager_routines.rs](../dogido-rust/src/villager_routines.rs) |
| ambient | 対象・職業名・反応間隔・睡眠帯の抑制を決める | [environment/ambient/mobs.rs](../dogido-rust/src/environment/ambient/mobs.rs) |
| カタログ | 種別・職業・子供のラベルや説明を読む | [catalog_knowledge.rs](../dogido-rust/src/catalog_knowledge.rs) |
| 反応モデルへの入口 | 観測事実と日課を分けて渡し、返答の形式を検査する | [environment/reaction.rs](../dogido-rust/src/environment/reaction.rs) |

村人専用の新しい戦況modeは増やさない。敵対警告や進行中の会話との優先関係は、本体のコードが所有する。

## 3. 送受信する観測

`passive_mobs`内の村人一件の例:

```json
{
  "type": "villager",
  "distance": 8.2,
  "direction": { "horizontal": "front", "vertical": "same" },
  "certainty": "high",
  "temperament": "passive",
  "is_baby": false,
  "profession": "farmer",
  "villager_type": "plains"
}
```

| フィールド | 意味 |
|---|---|
| `is_baby` | 取得できた年齢区分。省略は未確認であり、大人と同じではない |
| `profession` | 職業IDのpath。例: `farmer`、`librarian`、`none`、`nitwit`。取得できなければ省略 |
| `villager_type` | 村人の外見タイプ。現在地のバイオームとは別 |
| `world.time_of_day` | 日周のtick、0〜23999。日課計算に使う。`time_phase`は粗い時間帯 |

村人以外へ職業・外見タイプを付けない。プレイヤーから見た個体情報と、カタログにある職業一般の説明も区別する。

## 4. 日課と実際の行動

`villager_routines::activity`は一日の境界tickと年齢・職業から、`wander / work / gather / play / sleep`を求める。境界はこの関数にまとめ、モデルへ日課表を計算させない。

モデルに渡すラベルは「仕事の時間」「遊びの時間」「休息・睡眠の時間」など。これは予定を説明する材料であって、「今その個体が働いている」と確認した記録ではない。

- 時刻か年齢が未確認なら、生活予定も未確認にする。
- 仕事の時間帯に大人の職業を確認できない場合も、仕事中とは決めない。
- 村人数や視線先だけで村人の存在が分かる場合は、年齢・職業未確認の行として扱う。
- 会話用の観測と日課の欄を分け、属性や時刻だけから具体的な動作を補作しない。

## 5. 周辺反応と表示名

コードは日課上の睡眠帯の村人をambient候補から外す。これは「眠っているところを実測した」という意味ではなく、静かにするための発話抑制規則である。

職業別・子供・群衆のキーで反応間隔を管理する。複数の村人を群衆として扱う場合は「村人」とし、一人の職業を全員へ広げない。候補選択後も、反応モデルは無言を選べる。

| 取得した属性 | 表示・会話の扱い |
|---|---|
| 職業が既知 | カタログの職業ラベルを使う |
| 子供 | 子供のラベルを優先し、大人の職業を当てない |
| 職業が不明 | 汎用の「村人」を使う |
| `none` / `nitwit` | 別のIDとして保持する。見た目の推測や取得失敗でどちらかへ補わない |

観測にない職業をモデルに当てさせず、友好Mobへの操作禁止も言わせない。口調は[モブ反応トーン](mob-interaction-tone.md)に従う。

## 6. 職業取得の不具合と修正記録（2026-07-18）

当初は`VillagerData.profession()`の`RegistryEntry.getKey()`だけを読み、キーを取得できない場合に`none`へ置き換えていた。そのため、就職済みの村人も求職者として扱う場合があった。

修正では既知の職業キーとの照合、取得可能なキー、値のregistry IDを順に確認し、取得できなければ属性を省略する形へ変更した。`villager_type`も同じ考え方で扱う。

当初のPR-A（属性）、PR-B（日課・抑制）、PR-C（カタログ・発話材料）は実装済み。旧Pythonの`villager_schedule.py`やpy_treesを使う手順は終了した。職業・子供別の間隔管理と`villager_type`送信も存在するため、当時のPR-Dを丸ごと未実装とは扱わない。

## 7. 確認と残る候補

日課・未確認値・環境反応の自動検証は、リポジトリルートから実行する。

```sh
./dogido-rust/cargo.sh test --locked --lib villager_routines::tests::
./dogido-rust/cargo.sh test --locked --lib dialogue::environment_runtime::tests::
```

実機では農民・子供・職業未確認の各観測、昼夜での材料の変化、睡眠帯の静かさ、職業変更後の表示、脅威を優先できることを確認する。確認前に成功扱いにはしない。

職場ブロックの近傍スキャンは任意の将来案として残す。必要性は、現行の属性と日課だけでは説明できない実例から判断する。全職業の長いプロンプトや、毎tickの職業解説は追加しない。
