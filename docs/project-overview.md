# プロジェクト概要

## 目的

Minecraft のプレイ状況をもとに、怖がりな AI キャラクター「ドキドキドギド」が実況・警告・雑談を行う。

目標は、単なる音声アシスタントではなく、ゲーム状況に強く反応するキャラクターとして振る舞わせること。

## Minecraft からのデータ取得

専用の Fabric client adapter（`adapter/minecraft-fabric/`）でイベントを取得し、Rust本体（`dogido-rust/`）に送る。

## 外部連携・周辺機能の方針（2026-07-09 更新）

### 対話設計が固まってから再検討するもの

- M5Stack Push Avatar（出力デバイス）
- 定時スケジュールお知らせ
- LINE / Discord メッセージの受信
  - 例: 「あ。お母さんがそろそろやめなはれ言うとるよ」

これらは「ゲーム状況に反応する対話」が安定してからの拡張とする。  
[yuno-chan-api](https://github.com/yukincom/yuno-chan-api) からの流用可否も、その時点で見直す。

### いま参考にするもの

- 音声入力（whisper 周り）の実装ノウハウ
  - 現行実装は `dogido-rust/src/voice/`。旧 `dogido_server/voice_input.py` の運用は2026-10-03に終了し、機器接続・AECだけPython補助を残した。起動手順は [Rust本体](../dogido-rust/README.md) を参照。

### 使わない / 別途やるもの

- 天気取得（リアルの天気予報は使わない）
- yuno-chan のメモリーシステムそのもの（ドギドは JSONL ベースの記憶を別設計）
- 入力パターンによる複数ユーザー判定
- Hermes などの汎用エージェント基盤（機能過剰。必要な LLM 経路は閉じた route とコード検証で小さく構成する）

長時間プレイへの注意メッセージは、yuno-chan-api の流用ではなく別途対応する。

## このプロジェクトで先に固めるべきこと

- Minecraft から取得するデータの形式
- イベントの優先順位
- ドギドの内部状態
- 発話を抑制する条件
- プレイヤー入力への反応品質（対話設計の本丸）

相棒としての完成度の上げ方・何を後回しにするかは [companion-maturity.md](companion-maturity.md)。
