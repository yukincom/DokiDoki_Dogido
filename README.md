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

## ドギドがすること

| とき | 動作 |
|---|---|
| 敵が近い | 警告・パニック気味のリアクション |
| 平時 | 雑談・観察・実況 |
| 詩吟 | いまの状況から川柳 |
| プレイヤーが句に指摘 | 狙いや材料を話して、一緒に直す |
| プレイヤーが直した句 | 指摘を記憶、次の句に反映 |

---

## 構成

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

## 導入方法

```bash
# 依存
pip install -e .

# 設定
cp .env.example .env

# サーバー
python -m dogido_server
```

Minecraft 用アダプタは `adapter/minecraft-fabric/`（Java 1.21.11 / Fabric）。  
ビルドと入れ方は [adapter/minecraft-fabric/README.md](adapter/minecraft-fabric/README.md) をご確認ください。

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

ヘッドホン推奨。

fixture 再生 / スモーク:

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
