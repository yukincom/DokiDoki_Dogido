# Minecraft Java 公式技術データベース

ドギドが参照する Minecraft Java Edition の版固定・ローカル専用データベースです。出典URLの一覧だけではありません。公式配布物から生成したレジストリ項目、データパック項目、タグ定義、公式日本語名と、公式リリースノートを日本語で要約した変更事項を実際に検索できます。

対象版は、Fabricアダプターと同じ **Minecraft Java Edition 1.21.11** です。2026-09-01時点の「最新版」を実行時に追い掛けるのではなく、版番号・公式アーティファクトのハッシュ・正規化仕様を `source_lock.json` で固定しています。

## 収録内容

| データセットID | 件数 | 日本語分類名 | 主な内容 |
|---|---:|---|---|
| `version_information` | 1 | 配布版情報 | ワールド版、プロトコル版、データパック版、リソースパック版、必要なJava版 |
| `official_changes` | 25 | 公式変更事項 | 1.21.11公式リリースノートから選んだ主要な技術的変更の抜粋。分野・変更種別・関連ID・移行上の注意に分けた短い日本語要約 |
| `registry_entries` | 6,726 | レジストリ項目 | 95種のレジストリに属する名前空間付きID、プロトコルID、公式日本語名。ブロックは状態数、アイテムは耐久値・最大スタック数などを併記 |
| `datapack_entries` | 5,671 | データパック項目 | 通常のバニラ・データパックにある項目。JSONの種類、最上位のフィールド名、配列・辞書の件数、参照する名前空間付きIDを短い構造要約として収録 |
| `tag_definitions` | 625 | タグ定義 | 直接参照、タグ参照、必須・任意の別、および再帰的に展開した項目ID |

合計 **13,048件**です。実験的データパックは、通常のバニラ・データパックの収録件数には含めていません。

公式変更事項は原文の転載ではなく、技術的事実の短い日本語要約です。収録した25件は主要項目の抜粋であり、リリースノートの全変更を網羅する一覧ではありません。ゲームルールの旧名と新しい名前空間付きIDの対応も代表的な6件です。全変更の確認には公式リリースノートを使用してください。各レコードは、この収録範囲と公式ページの節名を出典情報として持ちます。

公式日本語名は、公式の `ja_jp` 言語資産に対応するキーがある項目だけに付けています。レジストリ項目では2,852件、データパック項目では65件が公式日本語名を持ち、それ以外は `title_ja_status: "identifier_fallback"` として名前空間付きIDを表示名に使います。

データパック項目も単なるファイル名一覧ではありません。たとえば `minecraft:diamond_sword` のレシピには、種類 `minecraft:crafting_shaped`、参照ID `minecraft:stick` と `#minecraft:diamond_tool_materials`、最上位のフィールド `key`・`pattern`・`result` などが記録されます。原JSON全文は保存しません。

## 保存場所と権利上の区分

追跡対象とローカル生成物を明確に分けています。

| 場所 | 内容 |
|---|---|
| `reference/minecraft_technical/source_lock.json` | 対象版、公式URL、SHA-1、サイズ、Fabric互換版、正規化版 |
| `reference/minecraft_technical/official_changes.json` | 公式リリースノートから選んだ主要25件の短い事実要約と収録範囲 |
| `reference/minecraft_technical/schema.json` | 各レコード型と日本語分類名のJSON Schema |
| `scripts/build_minecraft_technical_data.py` | 公式data generatorを実行し、ローカルDBを生成するコード |
| `scripts/validate_minecraft_technical_data.py` | 出典連鎖、件数、全レコード、索引、権利境界を検証するコード |
| `.dogido_reference/minecraft_technical/` | 全量の検索用JSON Linesとバイト索引。Git管理外 |

ゲームJAR、資産索引、公式日本語言語ファイル、data generatorのレポート、生成した原JSONはリポジトリにもローカルDBにも保存しません。処理中の一時ディレクトリだけで読み、終了時に破棄します。全量の正規化版も `.gitignore` 対象の `.dogido_reference/` に限定し、ほかの出力先は生成器が拒否します。

Minecraftの配布物と利用については、公式の [Minecraft EULA](https://www.minecraft.net/en-us/eula) と [Usage Guidelines](https://www.minecraft.net/en-us/usage-guidelines) が基準です。本データベースは Mojang Studios または Microsoft の公式製品ではありません。

> NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.

## 検索

資料整備時に、生成済みのローカルDBを `dev_tools.catalog_tools.minecraft_knowledge` のCLI/APIから検索できます。以下はリポジトリのルートから実行します。ドギド本体の実行時検索はRustの `dogido-rust/src/knowledge/` が担当し、このPython補助は呼びません。

```bash
python -m dev_tools.catalog_tools.minecraft_knowledge ダイヤモンドの剣
python -m dev_tools.catalog_tools.minecraft_knowledge minecraft:diamond_sword
python -m dev_tools.catalog_tools.minecraft_knowledge minecraft:swords --record-type tag_definition
python -m dev_tools.catalog_tools.minecraft_knowledge doMobSpawning --record-type official_change
python -m dev_tools.catalog_tools.minecraft_knowledge minecraft:diamond_sword --dataset datapack_entries --registry minecraft:recipe
```

Pythonからは次の入口を使います。

```python
from dev_tools.catalog_tools.minecraft_knowledge import (
    get_minecraft_knowledge,
    load_minecraft_manifest,
    search_minecraft_knowledge,
)

items = search_minecraft_knowledge(
    "ダイヤモンドの剣",
    registry_ids=["minecraft:item"],
)
changes = search_minecraft_knowledge(
    "ゲームルール",
    record_types=["official_change"],
)
record = get_minecraft_knowledge(items[0]["id"])
manifest = load_minecraft_manifest()
```

検索時にネットワーク通信、LLM呼び出し、Minecraftクライアントへの操作は行いません。索引を一行ずつ走査し、該当レコードだけをJSON Lines上のバイト位置から取得します。

## 再生成

現在のローカルDBは生成済みです。再生成する場合は、`source_lock.json` に記録された公式URLから、version manifest、対象版version JSON、server JAR、asset index、`ja_jp.json` を用意し、明示的に各パスを渡します。

```bash
python scripts/build_minecraft_technical_data.py \
  --version-manifest /path/to/version_manifest_v2.json \
  --version-json /path/to/1.21.11.json \
  --server-jar /path/to/server.jar \
  --asset-index /path/to/29.json \
  --locale-json /path/to/ja_jp.json

python scripts/validate_minecraft_technical_data.py
python -m pytest tests/test_minecraft_knowledge.py -q
./dogido-rust/cargo.sh test --locked knowledge::
```

生成器は入力ファイルのサイズとSHA-1、manifestからversion JSON、version JSONからserver JAR・asset index、asset indexから日本語言語ファイルまでの連鎖を検査します。さらに、FabricアダプターのMinecraft・Yarn・Fabric Loader・Fabric API各版、server JAR内のMinecraft版・Java版、正規化スキーマ・変更事項・生成器のSHA-256も照合します。

`snapshot_id` はMinecraft版、主要アーティファクト、正規化版を表します。同じIDで異なる内容を上書きしません。Minecraftの対象版を変える場合だけでなく、正規化スキーマ・要約・生成器を変更する場合も `normalization_revision` と `snapshot_id` を更新します。version manifestの可変な `latest` 値は再現対象へ含めません。

## ドギドでの利用境界

- 正式な照合キーは名前空間付きIDです。日本語名は公式言語ファイル由来の表示・検索補助であり、実行キーにはしません。
- プロトコルIDは版に依存する観測値です。世界操作やパケット送信の許可根拠には使いません。
- タグ所属だけから、敵対性、安全性、道具の適否、プレイヤーの意図を推定しません。
- 公式変更事項の `experimental: true` は、公式リリースノートが実験的機能と明記した場合だけ付けます。
- Mod、サーバー独自レジストリ、追加データパックはバニラ1.21.11の資料外です。資料外であることを不正または危険とみなしません。
- 既存の手整備カタログや安全方針を上書きしません。必要なレコードをコード側から明示取得し、LLMへ全量を自動注入しません。
- 実際のゲーム操作は従来どおり、型付きcommand、現在snapshot、capability、確認、期限、実行結果のコード検証を通します。このDBは操作権限を与えません。
- 現在ターンの明示質問をRustの `dogido-rust/src/knowledge/query.rs` が分類し、通常会話またはworkshopの専用経路で一回だけ検索します。危険中に受けた知識質問は安全になるまで保留し、通常tick・戦闘判断・支援操作へ検索を混ぜません。
- 返答は最大3事実と出典名に限定し、Java Edition 1.21.11、識別子、公式ウェブページ／公式配布物の別をコードで保持します。明示された別のJava Edition版はfail-closedにし、Data Pack 94.1、Resource Pack 75.0、管理プロトコル2.0.0を本体版と誤認しません。
- 縮約レシピは質問対象と `entry_id` が完全一致するレコードの種別と参照素材だけを答えます。参照IDには入力・出力の方向がないため、素材側からの逆引きや未保存の配置は補作しません。0件・データ欠落・検証失敗時は推測しません。詳細は [出典付き知識質問の統合](../../docs/knowledge-query-integration.md) を参照してください。

## 出典

- [Minecraft Java Edition 1.21.11 公式リリースノート](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-11)
- [Minecraft EULA](https://www.minecraft.net/en-us/eula)
- [Minecraft Usage Guidelines](https://www.minecraft.net/en-us/usage-guidelines)
- Mojang公式配布基盤のversion manifest、version JSON、server JAR、asset index、言語資産（個々のURLとハッシュは `source_lock.json`）
