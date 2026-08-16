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
指摘は覚えておいて、次の句に活かします。

---

## ドギドがすること

| とき | 動作 |
|---|---|
| 敵が近い | 警告・パニック気味のリアクション |
| 平時 | 雑談・観察・実況 |
| ふとしたとき | いまの状況や持ち物から川柳 |
| プレイヤーが句に指摘 | 狙いや材料を話して、一緒に直す |
| プレイヤーが直した句 | 指摘を記憶し、次の句に反映 |

声は PC 上の読み上げ（VOICEVOX）で聞こえます。  
マイクから話しかけることもできます（ヘッドホン推奨）。

---

## どう動いているか

見た目の魔法の裏では、次のように分かれています。

```text
Minecraft (Fabric アダプタ)
    ↓  現在地・Mob・天気・持ちもの…
dogido-server
    ↓  ステートマシンで「今なにを言うか」を決める
    ↓  必要なときだけ言語モデル（雑談 / 川柳の言い回し）
音声でプレイヤーへ
```

- **Minecraft 側** … 状況を取ることに専念  
- **サーバー側** … キャラクターの判断と記憶（コードが主。言葉の生成の一部に言語モデル）  
- **声** … PC 上の TTS（VOICEVOX）

「いつ慌てるか」「いつ黙るか」「いつ詠むか」はコードが決めます。  
言語モデルに判断を丸投げしません。

詳しい設計は `docs/` にあります。  
コードを触る AI アシスタント向けの注意は [AGENTS.md](AGENTS.md) です。

---

## 導入方法


```bash
cd /path/to/DokiDoki-Dogido
python -m venv .venv
source .venv/bin/activate

# 依存（初回）。テストも回すなら: pip install -e ".[dev]"
pip install -e .
# 任意: VOICEVOX の音読み誤読を減らす（fugashi + UniDic lite、~250MB）
# pip install -e ".[tts-reading]"
# まとめて: pip install -e ".[dev,tts-reading]"

# 設定（初回）
cp .env.example .env
# TTS 読み: DOGIDO_TTS_READING_ENGINE=auto|unidic|off（既定 auto）

# サーバー
python -m dogido_server
```

マイクから話しかける（別ターミナル・サーバー起動中）:

```bash
source .venv/bin/activate
python -m dogido_server.voice_input
```

ヘッドホン推奨。

Minecraft 用アダプタは `adapter/minecraft-fabric/`（Java 1.21.11 / Fabric）。  
ビルドと入れ方は [adapter/minecraft-fabric/README.md](adapter/minecraft-fabric/README.md) をご確認ください。

動作確認（開発用）:

```bash
python -m dogido_server.replay fixtures --no-audio
python -m dogido_server.smoke_test --mode all
```

---

## ドキュメント

詳細は **[docs/README.md](docs/README.md)** をご確認ください。

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

MIT ライセンス

コードを触る AI アシスタント向けの注意は **[AGENTS.md](AGENTS.md)** にまとめています。

TTS 読み補正（optional `pip install -e ".[tts-reading]"`）では [fugashi](https://github.com/polm/fugashi) と [unidic-lite](https://github.com/polm/unidic-lite) を利用します。UniDic は国立国語研究所の成果物で、GPL / LGPL / BSD のトリプルライセンスです。詳細は各パッケージのライセンス表記を参照してください。

---

## Acknowledgments / 謝辞

本プロジェクトは OpenAI の Series T - Post AGI from Kyoto プログラムより API クレジット支援を受けています。ありがとうございます。

**[note](https://note.com/yukin_co/n/n74eefa93ed24)**
