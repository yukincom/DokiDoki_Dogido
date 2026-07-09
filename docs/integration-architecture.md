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

### 現行

```text
Minecraft Java Edition
  -> Minecraft client adapter (Fabric client mod)
  -> イベント取得 (visual threats, auditory threats, light, inventory, player_death, time, block_break)
  -> dogido-server (Python / FastAPI)
     -> ステートマネージャー
     -> ルールエンジン / py_trees policy
     -> LLM 呼び出し（必要なときだけ）
     -> PC 音声 (VOICEVOX / say / afplay)
```

### 将来（対話設計が固まってから）

```text
  ... dogido-server ...
  -> M5Stack Push（出力デバイス差し替え）
  -> （任意）LINE / Discord 等の外部メッセージ入力
```

M5Stack と外部メッセージ連携は未実装でよい。  
先にゲーム内対話の品質を固める。

## サーバー側の責務

### dogido-server

- Minecraft 由来イベントを受け取る
- イベントを正規化する
- 内部状態を更新する
- どの話題を優先するか判定する
- 必要なときだけ LLM に発話生成を依頼する
- 生成した発話を音声バックエンドに流す
- （将来）M5Stack Push へ再生命令を送る

## 実装イメージ

### Minecraft adapter 側

- プレイヤー本人のクライアント状態を読む
- Minecraft のイベントを JSON で `dogido-server` に送る

### Python 側

- API と音声処理は本リポジトリの `dogido_server` で実装する
- 会話だけでなく、環境イベントを処理できる構成にする

## イベントの扱い（現行の実装寄りの見方）

adapter が主に送るもの:

- `status_snapshot`（平常同期。暗所スコアや inventory も同梱）
- `threat_approaching`
- `hostile_audio_detected`（現状は遮蔽敵ヒューリスティック。実サウンド packet は未）
- `ambient_mob_detected`
- `player_died`
- `combat_ended`

専用イベントより snapshot 上のフィールドで扱うもの:

- **暗所** … `danger_darkness_score` 等を継続参照（`danger_darkness_changed` はレガシー / テスト用）
- 時間帯・資源候補なども snapshot / 同梱コンテキスト中心

スキーマ上の名前として残しているもの（本番主経路ではない）:

- `threat_detected`
- `danger_darkness_changed`
- `resource_option_found`
- `time_phase_changed`

## 実装優先度

1. モンスター検知
2. 暗さ検知（snapshot スコア + 多段状態。専用イベント依存にしない）
3. インベントリと周辺資源を使った助言
4. 昼のモブ雑談
5. 死亡時フォロー
6. 対話品質の磨き込み
7. （後回し）M5Stack / 外部メッセージ連携
