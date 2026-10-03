# 連携構成

本体の実行時正本はRustです。旧Python本体の運用は終了し、Pythonには設定・辞書・外部SDKや音声機器の接続補助を残します。起動方法は [Rust本体の案内](../dogido-rust/README.md)、移植時の経緯は [Rust移行記録](rust-migration-plan.md) を参照してください。

## 現行構成

```text
Minecraft Java Edition
  <-> FabricクライアントMod
       -- 実観測のイベント --> dogido-rust (Rust / Axum)
       <-- 検証済みのtyped command --
            -> 戦闘・環境・会話所有権の判断
            -> 必要なときだけLLMへ生成・限定抽出
            -> 句・会話・操作結果のコード検証
            -> JSONL記憶 / 表示データ / 評価ログ
            -> VOICEVOXによる合成と音声配送

音声機器・Whisper・VAD・AEC
  -> Rustの音声入力制御
  -> 同じ本体のplayer入力
```

標準は `launch_dialogue.py --settings-dir .` と `--voice`、または `scripts/start_dogido.command server|voice` によるRust起動です。一般設定とFabricのポート既定は5055。`dogido-rust/start_dialogue.command` / `start_voice.command` は5056と従来の保存先を維持する専用経路です。接続先は選んだ起動経路に揃え、設定した `memory_dir` の記憶を自動移動しません。

## 責務分担

| 部分 | 所有する処理 |
|---|---|
| Fabric | プレイヤー本人・実視認・実音・周辺環境の観測。許可されたslot変更の最終検証と実行 |
| Rust本体 | 状態、優先順位、会話・川柳、生成結果の採否、保存、表示、音声入力と配送、取消・停止 |
| Python補助 | 設定解決、UniDic token、Whisper設定、AEC、端末AI、Chrome/MCPのSDK接続 |
| LLM | 言い回し、workshopの有界な次手選択、原文根拠を伴う限定抽出 |
| 既存計算エンジン | MLX、VOICEVOX、Whisper、VAD、AECの計算。Rust化のために再実装しない |

通常会話・知識検索・workshopの状態と保存判断をPython補助やLLMへ戻しません。端末AIは戦闘中断中の小分類、Chromeは同意と案内音声の実再生完了後の一度の検索に限定します。

残存モジュールと共有資料は [Rust本体の補助一覧](../dogido-rust/README.md#残すpython補助と資料) を参照してください。旧 `dogido_server/tts_reading.py` は廃止し、`tts_shared_tokens.py` がUniDic tokenを取得します。読みの判定・整形はRustが行います。データ整備用補助は `dev_tools/catalog_tools/` に分離します。

## 観測と操作

Fabricは主に `status_snapshot` と、その間に起きた敵接近・実音・死亡・戦闘終了などのイベントを送ります。暗所・時間帯・所持品等はsnapshotの実測値を使います。実際の送信形は [イベント仕様](event-schema.md)、HTTPとtyped commandの往復は [API仕様](adapter-api.md) が正本です。

現在観測、一般的なカタログ知識、プレイヤーの報告、再生済み会話は別の根拠として扱います。APIの読み取りから判断・保存・世界操作を行いません。

## 資料と開発ツール

`source_cards.json`、国語・詩形資料、Minecraft技術資料、カタログは現役の読み取り資料です。旧Python本体と一括して撤去しません。`dev_tools` のラボ候補と複合構造物試作は本体とは別に置き、接続状態は各READMEへ記します。

## 将来の連携

M5Stack等の出力機器とLINE・Discord等の外部メッセージは将来枠です。`mindcraft` は観測設計の参考であり、汎用の自律botやゲーム操作エージェントを導入する計画ではありません。

実Minecraft・マイク・スピーカー・Chromeを重ねた確認と、別マシンでの速度・メモリ測定は、自動テスト・模擬通信の結果と区別します。
