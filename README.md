# ドキドキドギド

Minecraft Java Edition向けのAIコンパニオンです。ゲーム内の観測とプレイヤーの発言をもとに会話し、川柳の作成・共同編集・記録を行います。音声と文字の入力に対応し、身体Modとの連携では移動や対象の観察も行います。

## 主な機能

| 機能 | 内容 |
| :--- | :--- |
| 危険への反応 | 敵の接近や被弾などに応じた警告・悲鳴 |
| 会話・環境反応 | 現在の観測と会話履歴に基づく発話、または沈黙の選択 |
| 身体 | プレイヤーへの追従、周囲の見回し、対象への観察移動（身体Modが必要） |
| 川柳 | ゲーム内の観測や直近の会話を材料とした生成 |
| 共同編集 | 句の意味・表現の相談、修正案の比較と採否、プレイヤーによる編集 |
| 本棚 | 句・写真・修正履歴・相談記録の閲覧 |
| ゲーム内支援 | 敵情報の回答、剣への持ち替え、条件を満たす乗用動物の呼び戻し |

音声入力にはWhisper、読み上げにはVOICEVOXを使います。Minecraft内の `/dogido <文章>` とブラウザーから文字入力もできます。`/dogido voice off` はマイク入力と読み上げを休止します。

画像入力に対応した会話モデルと身体Modを設定すると、静止時に取得するドギド視点の画像を会話に利用できます。画像入力は既定で無効です。発句時の写真は句に紐付けて保存します。

## 構成と役割

通常会話と環境反応では、モデルが発話内容と発話／沈黙を選択します。Rustは優先制御、実行条件、会話状態、取消、保存を担当します。

| 構成要素 | 役割 |
| :--- | :--- |
| Fabricアダプター・身体Mod | ゲーム内の観測、身体の動き、画像取得、文字表示、許可された操作の実行 |
| Rust本体 | 危険・割込みの優先制御、会話状態、操作対象と実行条件の確認、取消、句や記録の保存 |
| ModernBERT-Ja | 現在の発言を「依頼でない／敵情報／剣への持ち替え／乗用動物の呼び戻し／その他の依頼」に分類 |
| 会話・川柳モデル | 発話・沈黙の選択、句・説明・修正案の生成、相談の次手と候補内の身体行動の選択 |
| Python補助・音声 | モデル・辞書・音声機器への接続、Whisperによる音声入力、VOICEVOXによる読み上げ |

BERTには別途学習済みモデルの設定が必要です。戦闘中断中の川柳相談では、再開・継続・終了などの入力分類にOS／端末内AIを利用できます。利用できない場合は会話モデルへ切り替えます。

句への指摘は記録として保存し、後の生成時に参考情報として渡します。モデルの重みの更新は伴いません。現在のゲーム観測、一般知識、過去の会話・記録は区別して扱います。

詳細仕様は [docs](docs/README.md)、開発方針は [AGENTS.md](AGENTS.md) を参照してください。

## 導入方法

### 動作要件

- Minecraft Java Edition 1.21.11 + Fabric Loader + Fabric API（ModのビルドにはJDK 21）
- Rust（版は `dogido-rust/rust-toolchain.toml` で固定）
- Python 3.11 以上（設定・辞書・音声機器などの接続補助）
- 同じPCで動く、設定済みのLLM APIと [VOICEVOX](https://voicevox.hiroshiba.jp/)

### 1. 本体の準備と起動

ソースから導入する開発版です。以下はmacOS／Linuxのシェル例です。リポジトリのルートで、Python補助とRust本体を準備します。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

# 任意: VOICEVOXの漢字の読みを補うUniDic辞書
# pip install -e ".[tts-reading]"

cp .env.example .env
./dogido-rust/cargo.sh build --release --locked
```

`.env` にLLMの接続先・モデル名と音声設定を記入し、LLM APIとVOICEVOXを起動してから本体を開きます。標準ランチャーの接続先はlocalhostに限ります。モデル本体の導入は別途必要です。

依頼分類用BERTを使う場合は、[学習済みモデルの設定](docs/2026-10-15-modernbert-integration-preparation.md#通常起動の設定)も行います。

```bash
python dogido-rust/scripts/launch_dialogue.py --settings-dir . --check
python dogido-rust/scripts/launch_dialogue.py --settings-dir .
```

`--check` は設定・本体ファイル・補助を確認し、サーバーや録音は開始しません。

標準の接続先は `http://127.0.0.1:5055` です。起動後は [会話画面](http://127.0.0.1:5055/rust-chat) で文字入力から返答を確認できます。ポートを設定で変更した場合は、Minecraft側の接続先も同じポートに合わせてください。

### 2. マイク音声入力

Whisper・VAD・AECの準備は [音声入力の案内](docs/voice-echo-cancellation.md) を参照してください。準備済みの環境では、本体を起動したまま別のターミナルで次を開きます。

```bash
source .venv/bin/activate
python dogido-rust/scripts/launch_dialogue.py --settings-dir . --voice
```

終了は、それぞれの起動画面で `Ctrl+C`。LLM APIとVOICEVOXは各アプリで終了します。

### 3. Minecraft連携

[Fabric Modの導入手順](adapter/minecraft-fabric/README.md)に従ってModを配置します。Minecraftの `config/dogido-fabric-client.properties` は、標準設定では次の接続先を使います。

```properties
server_base_url=http://127.0.0.1:5055
```

3Dの身体を使う場合は [身体Modの導入手順](animation/cube_dogido/cube-dogido-lab/README.md) も参照し、対応するアダプター・身体Mod・Rust本体を揃えてください。

### 4. 動作確認

```bash
./dogido-rust/cargo.sh test --all-targets --locked
```

本体の設定・検証方法は [Rust本体の案内](dogido-rust/README.md)、実際の会話や音声の確認は [実機チェック](dogido-rust/manual-dialogue-check.md) を参照してください。

---

## ドキュメント

- [設定・起動・検証](dogido-rust/README.md)
- [Minecraftを起動せず、保存した句を相談する](dogido-rust/workshop-text.md)
- [句と写真を見返す本棚](docs/2026-10-13-bookshelf-integration.md)
- [会話への画像入力](docs/2026-10-21-chat-image.md)
- [仕様・開発記録の一覧](docs/README.md)

---

## 評価用の実験ブランチ

Minecraft内で返答を `↑`（良い例）／`↓`（要レビュー）と評価する[実験ブランチ](https://github.com/yukincom/DokiDoki_Dogido/tree/codex/training-feedback-pipeline)を公開しています。

- 評価データの保存先は、各PCのGit対象外ディレクトリ `.dogido_training/` です。
- 外部への自動送信・アップロードは行いません。
- 評価済みのデータも、人が確認してから学習候補にします。
- データ形式は[架空データ例](https://github.com/yukincom/DokiDoki_Dogido/tree/codex/training-feedback-pipeline/examples/training-feedback)で確認できます。

評価データの一般収集窓口は設けていません。試用や協力については [Issues](https://github.com/yukincom/DokiDoki_Dogido/issues) へ連絡してください。データ共有は内容確認と本人の同意を前提に個別に検討します。

---

## ライセンス・開発

**ライセンス表記の訂正（2026年10月6日）**

作者の不注意により、ライセンス表記に誤りがありました。お詫びして訂正します。

- プログラムコード：[MIT License](LICENSE.md)
- ドギドのキャラクター・公式素材・二次創作：[キャラクター利用・二次創作ガイドライン](LICENSE-CHARACTER.md)

コードを触る AI アシスタント向けの注意は **[AGENTS.md](AGENTS.md)** にまとめています。

TTS 読み補正（optional `pip install -e ".[tts-reading]"`）では [fugashi](https://github.com/polm/fugashi) と [unidic-lite](https://github.com/polm/unidic-lite) を利用します。UniDic は国立国語研究所の成果物で、GPL / LGPL / BSD のトリプルライセンスです。詳細は各パッケージのライセンス表記を参照してください。

---

## 謝辞

本プロジェクトは OpenAI の Series T - Post AGI from Kyoto プログラムより API クレジット支援を受けています。

開発記録: [note](https://note.com/yukin_co/n/n74eefa93ed24)
