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

# 依存（初回）
pip install -e .

# 設定（初回）
cp .env.example .env

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

## ライセンス・開発

MIT ライセンス

コードを触る AI アシスタント向けの注意は **[AGENTS.md](AGENTS.md)** にまとめています。

---

## Acknowledgments / 謝辞

本プロジェクトは OpenAI の Series T - Post AGI from Kyoto プログラムより API クレジット支援を受けています。ありがとうございます。

**[note](https://note.com/yukin_co/n/n74eefa93ed24)**
