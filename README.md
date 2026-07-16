# ドキドキドギド

Minecraft の冒険に、ちょっと怖がりな AI 相棒 **ドギド** がついてくるプロジェクトです。

---

## コンセプト

**AI と発見しよう！マイクラ世界の新しい見方！**

冒険の主役はいつもプレイヤー。  
ドギドは状況を盛り上げ、危ないときに慌て、平和なときに雑談し、ふいに **川柳** を詠みます。

でも、ドギドの句はちょっぴり下手くそです。

- 「どこが変？」
- 「どう直す？」
- 「いい句やな」

とツッコミながら、言葉を観察して表現を工夫していく——  
Minecraft の洞窟・朝日・クリーパー・ウーパールーパーが、そのまま言葉の材料になります。

自分で詠んだ句も残せます。

---

## ドギドは何をしてくれるの？

| とき | だいたいこんな感じ |
|---|---|
| 敵が近い | 警告・パニック気味のリアクション |
| 落ち着いている | 雑談・観察・実況 |
| ふとした瞬間 | いまの景色から川柳 |
| プレイヤーが句にツッコミ | 狙いや材料を正直に話して、一緒に直す |
| プレイヤーが直した句 | 覚えておいて、次の句に薄く反映（締めすぎない） |

「なんでも知ってる万能 AI」ではなく、  
**ゲームの中にいる、ちょっと下手な相棒** を目指しています。

---

## ざっくり仕組み

```text
Minecraft (Fabric アダプタ)
    ↓  いまの位置・敵・天気・持ちもの…
dogido-server
    ↓  状態機械で「今なにを言うか」を決める
    ↓  必要なときだけ LLM（雑談 / 川柳）
音声・テキストでプレイヤーへ
```

- **Minecraft 側** … 状況を取ることに専念  
- **サーバー側** … キャラクターの判断と記憶（コードが主、LLM は生成の一部）  
- **声** … PC 上の TTS（VOICEVOX など）

詳しい設計は `docs/` にあります。人間向けの入口はこの README、  
AI エージェント向けの注意は [AGENTS.md](AGENTS.md) です。

---

## はじめて動かす

```bash
# 依存
pip install -e .

# 設定
cp .env.example .env

# サーバー
python -m dogido_server
```

Minecraft 用アダプタは `adapter/minecraft-fabric/`（Java 1.21.11 / Fabric）。  
ビルドと入れ方は [adapter/minecraft-fabric/README.md](adapter/minecraft-fabric/README.md) を見てください。

テキストで話しかけるテスト（サーバー起動中）:

```bash
curl -X POST http://127.0.0.1:5055/api/v1/player-input \
  -H 'Content-Type: application/json' \
  -d '{"text": "おはようさん"}'
```

マイクから話しかける:

```bash
python -m dogido_server.voice_input
```

ヘッドホン推奨（スピーカーだとドギドの声を拾ってループしやすいです）。

fixture 再生 / スモーク:

```bash
python -m dogido_server.replay fixtures --no-audio
python -m dogido_server.smoke_test --mode all
```

---

## いまできること（雰囲気）

- 脅威に応じた警告・余韻・雑談
- 状況に根ざした川柳（カタログ・読み・材料）
- 句への自然なツッコミ（ワークショップ）と、薄〜い教訓の反映
- 句の保存・直し・「思い出して」
- 読みの訂正（例: 草地の読み）

完璧な名句ジェネレータではありません。  
**一緒に直しながら遊ぶ** 方がコンセプトに近いです。

---

## ドキュメント

全部読まなくて大丈夫。気になったところから。

| 読みたいこと | ドキュメント |
|---|---|
| **目次・読む順番（正）** | **[docs/README.md](docs/README.md)** |
| 製品の心 | [docs/concept.md](docs/concept.md) |
| 何が動いているか | [docs/project-overview.md](docs/project-overview.md) |
| 完成度をどう上げるか | [docs/companion-maturity.md](docs/companion-maturity.md) |
| 川柳を一緒に直す設計 | [docs/haiku-player-improvement-plan.md](docs/haiku-player-improvement-plan.md) |
| 雑談の方針 | [docs/player-chat-casual-plan.md](docs/player-chat-casual-plan.md) / [docs/dialogue-design.md](docs/dialogue-design.md) |
| AI がコードを触るとき | [AGENTS.md](AGENTS.md) |

仕様の一覧・推奨読む順は **[docs/README.md](docs/README.md)** を正とします。

---

## 方針（短く）

- プレイヤーが冒険の主体。ドギドは相棒
- キャラクター判断はコード（状態機械）が持つ。LLM に任せきりにしない
- 川柳の記憶は JSONL。プロンプトに過去句を山盛りしない
- プレイヤーからの注意は **ゆるく** 効かせ、ほめたり「気にせんで」で緩められる
- 外部連携（M5Stack / LINE など）は対話が安定してから
- 完成は急がない。観測を厚くして、外したときは一緒に直す

---

## ライセンス・開発

個人・研究寄りの実験プロジェクトです。  
コードを触る AI アシスタント向けの注意は **[AGENTS.md](AGENTS.md)** にまとめています。

楽しんで、マイクラとことばを。
