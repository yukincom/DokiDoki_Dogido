# 共通聴覚への集約（パート4）

2026-10-29。書類側mainへ編集を反映する段階。実行・コンパイル・モデル応答の確認は未実施。テストの作成・変更・実行は別担当のため、この編集では行っていない。

## 入力と保持

通常会話と環境反応は、`world_context.observations.hearing` の同じ投影を使う。現在の実音ID・音種・方向・粗い距離・確度・元の聴取時刻を残し、未確定音も削除しない。同一の元レコードだけを重複排除する。音IDは内部のカタログ説明への参照であり、辞書に鳴き声があることから実際の聴取を追加しない。

2026-11-04の自発発話向け集約は、末尾の追記を優先する。通常会話と質問用の元の投影・保持は維持する。

通常会話の旧 `hearing_block` と、脅威・観測の要約へ重ねていた音文を外した。旧要約は内部診断に残す。既存の保持期間・件数・更新規則を使い、`HearingMemo.observation` に共通投影の事実を保持する。保持へ移った後も音源の由来、確度、種名の呼称可否、調査打切りを引き継ぐ。通常会話と環境反応は同じ観測時刻を参照し、返答の準備時刻を新しい発音時刻にしない。

雨・雷は共通聴覚内へ移し、内部の周期比較用boolは維持する。身体感覚が取得できないときは `status=unavailable` とし、プレイヤー視点や過去の音で埋めない。

## 音源の由来

| `emitter_attribution` | 根拠 |
|---|---|
| `confirmed` | 実音のsource UUIDが受理済みowner UUIDならプレイヤー。別UUIDなら本人以外 |
| `inferred` | UUIDで同定できないブロック足音／破壊音に、同じ発音時の本人位置・歩行／破壊動作・聴取範囲の完了した調査が揃い、他の発音候補がいない |
| `unknown` | 根拠欠落・時刻不一致・別候補あり・未ロード・調査打切りなど。音の事実は残す |

`pos:` や `played:` は追跡キーで、UUIDによる同定に使わない。推定の時刻差は100ms以内、足音は本人から2ブロック以内、破壊音は6ブロック以内。調査半径は実音の到達距離を使い、最大64ブロック。32候補を超えたら `truncated` とする。視認できないモブも候補へ入れる。

採取は既存の位置付きclient sound受付で行う。遅配server音へ受信時の周辺調査を付けない。同じ発音として既存deduplicatorが一対一照合した場合だけ、UUID・発音時の根拠を引き継ぐ。

現行 `dogido.lab.DogidoEntity` はnoClip移動で通常の足音経路を通らず、ブロック破壊音を発生する処理もない。この身体だけを対象に、足音はnoClipを確認して候補から外す。別の身体実装・一般モブは除外しない。身体に発音・破壊能力を追加する際はこの条件も更新する。

共通groundingに、音自体の確度と音源の推定を分ける指示、種名を確定できない音の扱い、調査打切りで由来が未確定の音を自発話題にせず質問時に推測として答える指示を追加した。モデルの返答を固定文や語句一致で棄却する処理は追加していない。

## モデル入力例

以下は編集後の投影形式に合わせた説明用の抜粋であり、実行して採取したpayloadではない。実payloadとモデル応答の確認は試験担当へ残す。

```json
{
  "world_context": {
    "observations": {
      "hearing": {
        "status": "observed",
        "observer": "dogido",
        "observed_at": "2026-10-29T03:00:00Z",
        "truncated": false,
        "sounds": [{
          "description": "石系の足音",
          "sound_event": "minecraft:block.stone.step",
          "certainty": "high",
          "heard_ago_ms": 1200,
          "emitter": "プレイヤー",
          "emitter_attribution": "inferred",
          "source_survey_status": "complete"
        }]
      }
    }
  }
}
```

調査打切りでUUIDによる同定もない音は、同じ音を残したまま `emitter=null / emitter_attribution=unknown / source_survey_status=truncated` になる。

## 変更ファイル

- `adapter/minecraft-fabric/src/main/java/dogido/fabric/body/BodySoundContext.java`（新規）、`BodySensesCollector.java`: 発音時の根拠採取と打切り表示。
- 同 `observation/sound/AudioThreatObservation.java`、`SoundObserver.java`、`protocol/EventJsonBuilder.java`: 既存音経路で根拠を運び、同じ発音の対応を保つ。
- `dogido-rust/src/conversation_observation/hearing.rs`（新規）、`conversation_observation.rs`、`dialogue/body_runtime.rs`: 共通の聴覚投影。
- `dogido-rust/src/chat_observation.rs`、`dialogue/chat_context.rs`: 既存ラベル参照の共有と保持音の事実継承。
- `dogido-rust/src/chat_materials.rs`、`chat_materials/current.rs`、`chat_prompt.rs`、`chat_prompt/data.json`: 通常入力の重複を除去。
- `dogido_server/llm/companion_prompts.json`: 共通の音の扱い。

既存テストの `HearingMemo` 直接構築には追加fieldの適応が必要。受入テストは本編集で変更していない。

## 2026-11-04 自発発話の同じ音を継続として渡す

自発発話の生成直前に、`hearing.sounds` のコピーを既存の音源ID・実音種ごとに一件へまとめる。最新の方向・距離・確度・由来・聴取時刻を採用し、初回は `presentation: "初回提示"`、前の自発判断から同じ音が続くときは `presentation: "継続提示"` を添える。沈黙や生成失敗でも、同じ音を次の判断で新着へ戻さない。待ち列への予約・材料の再読・通常質問だけでは提示の基準を進めない。

聴取が既存の保持期間内（既定20秒）で続いていれば、判断の間隔が長くても継続を保つ。保持期限を越えた再出現、身体の失効・世代変更では引き継がない。保持音には従来どおり `basis: "retained_sound"` と元の聴取からの経過時間を残す。「継続提示」は同じ材料の再提示であり、途切れず鳴っていることや発話済みの断定ではない。

音源ID・音種が欠ける音は同一音源としてまとめない。`pos:` 等は同じ追跡キー内だけで使い、別位置の音を同じ個体と推定しない。別音種・別音源・警戒区分の変化は分ける。IDや個体の座標はモデルへ渡さない。

変更対象は通常の自発発話用コピーだけ。通常会話・明示質問、元の聴覚記録とその保持期間、緊急警告、川柳再開、音による差分検出は変更しない。共有プロンプトの文面も変更しない。

検証: 集約・継続・音源欠落・保持期限・身体失効と、通常質問の入力・保持記録の不変を含む聴覚関連Rust試験29件成功。実Minecraft・実モデルで発話の繰り返しが減るかは未確認。
