# ドキドキドギド

冒険の主役は、いつもあなた。

Minecraft の洞窟も、朝日も、突然のクリーパーも——  
そのそばに、ちょっと怖がりの相棒 **ドギド** がついてきます。

危ないときは慌てて声を上げ、平和なときはぼそっと雑談し、  
ふいに **川柳** を詠みます。

でも、ドギドの句はちょっぴり下手くそです。

- 「どこが変？」
- 「どう直す？」
- 「いい句だね」

とツッコミながら、言葉を観察して表現を工夫していく。  
ゲームの中で見たものが、そのまま句の材料になります。

自分で詠んだ句も残せます。

---

## コンセプト

**いっしょに見つけて、いっしょに詠む。マイクラ世界の新しい見方。**

ドギドはプレイヤーの旅に寄り添い、状況を盛り上げ、ときに言葉で景色を表現します。

句が外れたら、直しながら話す。  
指摘は参考として記録し、次の句を考える材料にします。

---

## ドギドができること

| 場面 | ドギドの動き |
| :--- | :--- |
| **危険が迫ったとき** | 敵や被弾などに反応し、警告や悲鳴で知らせる |
| **一緒に冒険しているとき** | 周囲の様子や会話を受けて雑談する。黙って受け止めることもある |
| **身体Modを入れたとき** | プレイヤーについて歩き、周囲を見回し、気になった対象を見に行く |
| **川柳を詠むとき** | ゲーム内の観測や直近の会話を材料に一句を作る |
| **句について話すとき** | 意味や表現を話し合い、プレイヤーの言葉や提案で一緒に直す |
| **冒険を振り返るとき** | 保存した句・写真・修正履歴や相談の記録を本棚で見返す |
| **手助けを頼んだとき** | 敵の情報を伝える、剣へ持ち替える、条件を満たす乗用動物を呼び戻す |

マイクとVOICEVOXで声のやりとりができ、Minecraftの `/dogido <文章>` やブラウザーから文字でも話せます。`/dogido voice off` で音声を休止し、文字だけで続けられます。

画像入力に対応した会話モデルと身体Modを設定すると、立ち止まったときのドギド視点の画像も会話に使えます（画像入力は既定で無効）。川柳に添える写真は、そのときの記録として保存します。

---

## どう動いているか

**会話や沈黙、相談の次手はモデルが選び、危険への対応・操作条件・状態と保存はコードが管理します。**

| 担当 | 役割 |
| :--- | :--- |
| **Fabricアダプター・身体Mod** | ゲーム内の観測、身体の動き、画像取得、文字表示と許可された操作の実行 |
| **Rust本体** | 危険・割込みの優先制御、会話状態、操作対象と実行条件の確認、取消、句や記録の保存 |
| **ModernBERT-Ja** | 今回の発言を「依頼でない／敵情報／剣への持ち替え／乗用動物の呼び戻し／その他の依頼」に分類 |
| **会話・川柳モデル** | 観測と会話から発話・沈黙を選び、句や説明・修正案を作る。相談の次手や、許可された候補内の身体行動も選ぶ |
| **Python補助・音声** | モデルや辞書・音声機器との接続を補助し、Whisperで音声入力、VOICEVOXで読み上げる |

BERTには別途学習済みモデルの設定が必要です。戦闘で中断した川柳相談の小さな入力分類には、任意のOS／端末内AIも使います。利用できない場合は会話モデルへ切り替えます。

会話での指摘は、保存した記録を次の生成の参考にする仕組みです。会話のたびにモデルの重みを学習し直すわけではありません。現在のゲーム観測、一般知識、過去の会話・記録を分けて扱います。

詳しい仕様は [docs](docs/README.md)、開発時の方針は [AGENTS.md](AGENTS.md) を参照してください。

---

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

## 評価協力版（実験ブランチ）

ドギドの返答を、Minecraft 上で `↑`（良い例）／`↓`（要レビュー）に振り分ける
**[評価協力版ブランチ](https://github.com/yukincom/DokiDoki_Dogido/tree/codex/training-feedback-pipeline)** を公開しています。

- 評価したデータは各自の PC 内にある Git 対象外の `.dogido_training/` へ保存されます
- データを外部へ自動送信・自動アップロードする機能はありません
- `↑`を押した内容もそのまま学習へ使わず、人が確認したものだけを候補にします
- JSONL の形は、実データを含まない[架空データ例](https://github.com/yukincom/DokiDoki_Dogido/tree/codex/training-feedback-pipeline/examples/training-feedback)で確認できます

現時点では評価データの送信受付を設けていません。
試してみたい方、将来のデータ作りに協力してくださる方がいれば、
まずは [Issues](https://github.com/yukincom/DokiDoki_Dogido/issues) でお知らせください。
協力者が現れた段階で、本人による内容確認と同意を前提に、安全な共有方法を検討します。

---

## ライセンス・開発

**ライセンス表記の訂正（2026年10月6日）**

作者の不注意により、ライセンス表記に誤りがありました。お詫びして訂正します。

- プログラムコード：[MIT License](LICENSE.md)
- ドギドのキャラクター・公式素材・二次創作：[キャラクター利用・二次創作ガイドライン](LICENSE-CHARACTER.md)

コードを触る AI アシスタント向けの注意は **[AGENTS.md](AGENTS.md)** にまとめています。

TTS 読み補正（optional `pip install -e ".[tts-reading]"`）では [fugashi](https://github.com/polm/fugashi) と [unidic-lite](https://github.com/polm/unidic-lite) を利用します。UniDic は国立国語研究所の成果物で、GPL / LGPL / BSD のトリプルライセンスです。詳細は各パッケージのライセンス表記を参照してください。

---

## Acknowledgments / 謝辞

本プロジェクトは OpenAI の Series T - Post AGI from Kyoto プログラムより API クレジット支援を受けています。ありがとうございます。

**[note](https://note.com/yukin_co/n/n74eefa93ed24)**
