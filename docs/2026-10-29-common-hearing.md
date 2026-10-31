# 共通聴覚への集約（パート4）

2026-10-29。書類側mainへ編集を反映する段階。実行・コンパイル・モデル応答の確認は未実施。テストの作成・変更・実行は別担当のため、この編集では行っていない。

## 入力と保持

通常会話と環境反応は、`world_context.observations.hearing` の同じ投影を使う。現在の実音ID・音種・方向・粗い距離・確度・元の聴取時刻を残し、未確定音も削除しない。同一の元レコードだけを重複排除する。音IDは内部のカタログ説明への参照であり、辞書に鳴き声があることから実際の聴取を追加しない。

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
