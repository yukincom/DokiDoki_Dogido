# 連携構成

## Minecraft 側で使う部分

`mindcraft` は主に参考実装として扱う。

実際の観測は、プレイヤー本人を対象にした Minecraft クライアントアダプタで行う前提とする。

### 参考にしたい機能

- 周辺 mob の見方
- インベントリ取得の考え方
- 状態の組み立て方
- イベント分離の考え方

### 使わない機能

- AI が bot を操作する部分
- companion bot 前提の構成

## 想定アーキテクチャ

```text
Minecraft Java Edition
  -> Minecraft client adapter (Fabric client mod 想定)
  -> イベント取得 (visual threats, auditory threats, light, inventory, player_death, time, block_break)
  -> dogido-server (Python / FastAPI)
     -> ステートマネージャー
     -> ルールエンジン
     -> LLM 呼び出し
     -> TTS 出力
     -> M5Stack Push
```

## サーバー側の責務

### dogido-server

- Minecraft 由来イベントを受け取る
- イベントを正規化する
- 内部状態を更新する
- どの話題を優先するか判定する
- 必要なときだけ LLM に発話生成を依頼する
- 生成した発話を TTS に流す
- M5Stack Push へ送信する

## 実装イメージ

### Minecraft adapter 側

- プレイヤー本人のクライアント状態を読む
- Minecraft のイベントを JSON で `dogido-server` に送る

### Python 側

- API と音声処理は本リポジトリの `dogido_server` で実装する
- 会話だけでなく、環境イベントを処理できる構成にする

## 最初に定義したいイベント

- `hostile_detected`
- `hostile_approaching`
- `hostile_lost`
- `ambient_mob_detected`
- `light_level_changed`
- `inventory_changed`
- `nearby_resources_detected`
- `player_died`
- `time_phase_changed`
- `block_break_toward_hostile`
- `hostile_audio_detected`
- `danger_darkness_changed`

## 実装優先度

1. モンスター検知
2. 暗さ検知
3. インベントリと周辺資源を使った助言
4. 昼のモブ雑談
5. 死亡時フォロー
