# 国語知識・詩形データベース

ドギドからローカルに検索できる国語知識データベースです。出典の一覧だけではありません。公式配布データの原ファイルと正規化データ、根拠を照合した規則・分類レコード、検索索引、利用APIを一つのフォルダに収めています。確認日は **2026-09-01** です。

日本関係の情報源は、国・地方公共団体、国立研究機関、国立国会図書館、国公立大学などの公的機関、またはそれらが公的責任の下で公開する資料に限定しています。個人塾、個人サイト、共同編集型百科事典、個人作成データベースは収集していません。

世界の詩形分類では、各国の文化・教育機関、図書館、博物館、大学・研究機関が公開する資料に加え、詩の教育・普及を継続的に担う非営利機関の用語集を用いています。こちらも個人サイトと共同編集型百科事典は根拠にしていません。

## 収録内容

### 根拠照合済みの国語知識

| ファイル | 件数 | 内容 |
|---|---:|---|
| `japanese_grammar.json` | 22 | 主語・述語、修飾、語順、活用、助詞・助動詞、送り仮名、公用文の表記 |
| `historical_kana_and_scripts.json` | 17 | 歴史的仮名遣い、旧仮名から現代仮名への検索候補、変体仮名、くずし字 |
| `makurakotoba.json` | 10 | 枕詞の定義と、代表的な枕詞・被枕詞の対応 |
| `japanese_poetry_forms.json` | 16 | モーラの数え方、和歌・短歌・長歌・旋頭歌・片歌・仏足石歌体・連歌・俳諧・発句・連句・俳句・川柳・狂歌・都々逸・琉歌 |
| `world_poetry.json` | 33 | 世界の主要な詩種・詩形・詩歌の伝統を、定型・韻律・伝承・作詩形態などで分類 |

各国語知識レコードには、定義または作業上の説明、規則、例外または適用範囲、機械判定の限界、必要な入力、人による確認が必要な場合、出典箇所を記録しています。学習指導要領が学習事項名だけを示す場合は、作業上の説明を `definition_status: editorial_synthesis`、誤判定を避けるための注意を `claim_status: editorial_guardrail` と明示し、文部科学省がその説明文まで定義したようには扱いません。世界詩の33レコードも、複数の分類軸を本データベース用に組み合わせているため、レコード全体の `classification_status` はすべて `editorial_synthesis` です。詩形・韻律など個々の事実は、それぞれの `source_refs` で典拠を示しています。分類名は国語学・国文学・韻律論で通用する日本語を優先し、内部IDの直訳を表示名に使いません。

### 公式配布データの正規化版

| データセットID | 件数 | 内容 | 利用条件 |
|---|---:|---|---|
| `grammar_patterns` | 800 | 国立国語研究所「日本語文型データベース」：文型、接続、意味、用例、ルビ情報 | CC BY 4.0 |
| `education_basic_vocabulary` | 27,234 | 国立国語研究所「教育基本語彙データベース」2009A・2009B統合版 | CC BY 4.0 |
| `japanese_education_basic_vocabulary` | 11,826 | 国立国語研究所「日本語教育基本語彙データベース」 | CC BY 4.0 |
| `historical_hiragana_unicode` | 287 | Unicode 17.0.0の変体仮名285字と歴史的平仮名2字：符号位置、公式文字名由来の `romanized_name_components`、編集上の `modern_search_candidates` | Unicode License v3 |
| `curriculum_japanese` | 1,859 | 文部科学省「学習指導要領コード」から抽出した国語科の項目 | 文部科学省ウェブサイト利用規約 |
| `joyo_kanji` | 2,136 | 文化庁「常用漢字表」：字種、符号位置、音訓4,388件、例欄、備考欄 | 文部科学省ウェブサイト利用規約 |
| `grade_level_kanji_allocation` | 1,026 | 文部科学省「学年別漢字配当表」：文字と小学校の配当学年 | 文部科学省ウェブサイト利用規約 |

合計 **45,168件**です。`data/raw/` に取得した公式原データ、`data/normalized/` にUTF-8のJSON Linesと索引、`data/manifest.json` に版・件数・SHA-256・帰属表示を保存しています。文部科学省の原CSVは国語以外も含む公式の全教科版をそのまま保存し、正規化時に国語科の1,859件だけを抽出しています。実行時に外部通信は行いません。

ドギドの明示質問では、日本語文型バンクの正式名800件をすべて検索できます。波線で始まる749件は文型表記そのものから国語領域と判定し、それ以外の51件は生成データと同期した完全一致語彙で判定します。正式名の末尾に疑問符や「について」「に関して」を含む文型も文字を落としません。同じ正式名で意味・用法が異なるレコードは、最大3件の範囲で別々に返します。用例全文は返答へ載せません。

常用漢字は、文化庁の公式音訓索引HTMLを構造化し、公式PDFで字種を照合しました。音訓は **音読み2,352件・訓読み2,036件**です。音訓欄と例欄は行数が一致しない字があるため、資料にない個別対応を推定せず、別配列で保持しています。学年別漢字配当表は公式PDF内で文字が画像化されているため、`data/editorial/` に目視照合済みの転記を置き、第1学年から順に **80・160・200・202・193・191字**であること、重複がないこと、全1,026字が常用漢字表に含まれることを生成時と検証時の両方で確認します。

## ファイルの役割

| パス | 役割 |
|---|---|
| `data/raw/` | 公式配布元から取得した原ファイル |
| `data/editorial/` | 公式原資料が機械抽出できない箇所の目視照合済み転記。出典箇所・方法・ハッシュを記録 |
| `data/normalized/*.jsonl` | 一行一件の正規化データ |
| `data/normalized/index.json` | データセットID、件数、データ本体と索引のファイル名を示す小さな索引マニフェスト |
| `data/normalized/*.index.jsonl` | データセットごとのID、種別、明示検索語、JSONL上のバイト位置を持つ索引。検索時は必要な索引をストリーム走査する |
| `data/manifest.json` | 原データと正規化データの版、件数、ハッシュ、利用条件 |
| `data/LICENSES.md` | 保存・改変・帰属表示の扱い |
| `sources.json` | 71件の出典と利用条件の台帳 |
| `japanese_language_education.json` | 25件の公的資料・標準データセットの利用方針と除外記録 |
| `schema.json` | Draft 2020-12 JSON Schema |
| `index.json` | 国語知識、世界詩、出典台帳を横断する主索引 |

JSON Linesを採用したのは、大きなデータ本体や索引全体をメモリーに読み込まず、データセット別の索引をストリーム走査し、得られたバイト位置から該当レコードだけを取得するためです。小規模な規則・分類は、人が確認しやすい通常のJSONにしています。

## 検索

国語知識と公式配布データは、同じ入口から検索できます。

```bash
python -m dogido_server.language_knowledge ゐ
python -m dogido_server.language_knowledge 枕詞
python -m dogido_server.language_knowledge 川柳
python -m dogido_server.language_knowledge 主語と述語
python -m dogido_server.language_knowledge "〜あげく"
python -m dogido_server.language_knowledge 𛀂 --dataset historical_hiragana_unicode
python -m dogido_server.language_knowledge 敬語 --dataset curriculum_japanese
python -m dogido_server.language_knowledge せんりゅう --dataset education_basic_vocabulary
python -m dogido_server.language_knowledge 亜 --dataset joyo_kanji
python -m dogido_server.language_knowledge 第4学年 --dataset grade_level_kanji_allocation
python -m dogido_server.language_knowledge 一 --kanji-profile
```

Pythonからは次のように使います。

```python
from dogido_server.language_knowledge import (
    get_bulk_knowledge,
    get_kanji_profile,
    search_bulk_knowledge,
    search_japanese_knowledge,
)

records = search_japanese_knowledge("川柳")
patterns = search_bulk_knowledge("〜あげく", dataset_ids=["grammar_patterns"])
record = get_bulk_knowledge(patterns[0]["id"])
kanji = get_kanji_profile("一")
```

世界詩の分類や出典台帳は、分類検索用の入口から検索できます。

```bash
python -m dogido_server.reference_catalog 川柳 --full
python -m dogido_server.reference_catalog 共同制作 --dataset world_poetry
python -m dogido_server.reference_catalog --dataset world_poetry --region jp --limit 50
python -m dogido_server.reference_catalog --dataset world_poetry --entity-kind form --prosodic-basis mora
python -m dogido_server.reference_catalog --dataset world_poetry --composition-mode 共同制作
```

世界詩の分類軸は、内部IDだけでなく「詩形」「規則形式」「共同制作」などの日本語分類名からも検索・絞り込みできます。分類の説明文全体は検索対象にせず、明示した分類名だけを索引へ登録します。

## 権利上の区分

保存方法を一律には扱っていません。

- 国立国語研究所の文型・語彙データは、明示された **CC BY 4.0** に従い、原データと正規化版を保存します。
- Unicodeの文字データは **Unicode License v3** に従い、版を固定した原データ、利用許諾、歴史的平仮名・変体仮名の正規化版を保存します。
- 文部科学省の学習指導要領コードは、同省ウェブサイト利用規約に従い、出典と編集内容を明示して全教科版の原CSVと国語科の正規化行を保存します。
- 文化庁「常用漢字表」と文部科学省「学年別漢字配当表」は、同規約に従い、出典と加工内容を明示して公式HTML・PDF、目視照合済み転記、正規化版を保存します。ロゴ・ページ画像・書体見本はデータとして切り出しません。
- 文化庁『送り仮名の付け方』は、通則1〜7の本則・例外・許容、適用範囲および掲出例を構造化した抜粋として保存します。地方公共団体、国立国会図書館、国公立大学等の解説は、規則・形式・分類などの事実を照合する根拠として用い、解説本文、詩作品、歌詞、画像、音源は転載しません。
- 出典に第三者の権利が含まれる項目は、個別確認なしに本文を保存しません。

`sources.json` の `publication_role` は、そのページが「機関自身の発行物」「機関が刊行する学術的資料」「公的機関が公式基盤上で公開する回答・解説」「書誌・典拠レコード」のどれに当たるかを記録します。`rights.stored_content_scope` は本フォルダに保存できる範囲を示し、明示ライセンスのある配布データ以外は原則として `metadata_links_and_factual_summary`（書誌情報、リンク、事実の短い要約）に限定します。公開主体と保存範囲を分けて記録することで、公的な掲載場所を、そのまま本文の転載許諾とはみなしません。

現代詩・現代文の作品本文を集めるデータベースではありません。著作権の有無を推測せず、作品本文を必要としない言語規則・形式知と、明示的に再利用できる公式データを中心にしています。

## ドギドでの判定境界

- 国語知識は助言用です。表記・語彙・詩形の一般則を、川柳生成の一律な禁止条件へ変えません。
- 日本語の五音・七音は、確定した読みをモーラで数えます。漢字から未知語の読みを推測しません。
- 歴史的仮名から現代仮名への対応は検索候補です。古典本文の原表記を上書きしません。
- 枕詞は既知の対応から候補を示します。語源、原義、表現効果を機械だけで断定しません。
- 「季語がないから川柳」「季語があるから俳句」とは判定しません。音数だけで詩形の帰属を決めません。
- 常用漢字外であることや、ある学年に配当されていないことだけを誤り・使用禁止の根拠にしません。配当学年から個々の音訓の学習学年を推定しません。
- 琉歌や口承詩は、地域の読み、歌唱、伝承の文脈を標準日本語の辞書だけで置き換えません。
- このデータをプロンプトへ自動注入せず、必要な項目だけをコード側で取得します。
- 現在ターンの明示質問だけを `dogido_server.knowledge_query` が分類し、player chatまたはworkshopの発話枝へ到達した時点で一回だけ検索します。手整備の5データセットは正式名と全別名、日本語文型バンクは正式名800件をそれぞれ完全一致で同期し、通常tick、警戒・戦闘・死亡時、支援操作、保存判断では検索しません。
- 返答は最大3事実と出典名に限定し、事実本文をLLMに書き換えさせません。差替えproviderの事実一式は正本ローカルDBの再構成結果と照合し、0件・曖昧語・データ欠落・検証失敗時は推測しない固定文を返します。詳細は [出典付き知識質問の統合](../../docs/knowledge-query-integration.md) を参照してください。

## 再生成と検証

原データからの正規化と索引生成は決定的です。

```bash
python scripts/build_language_knowledge_data.py
python scripts/build_reference_index.py
python scripts/validate_reference_catalog.py
python scripts/build_language_knowledge_data.py --check
python scripts/build_reference_index.py --check
python -m pytest tests/test_reference_catalog.py tests/test_language_knowledge.py tests/test_knowledge_query.py tests/test_knowledge_chat.py -q
```

検証では、JSON Schema、出典機関、URLの公式ドメイン、利用条件、禁止された本文・歌詞キー、全知識レコードの `source_refs` にある出典ID・根拠内容・出典箇所、全ファイルのSHA-256と件数、JSONL全45,168件のバイト位置と索引内容、索引の再現性を確認します。さらに、常用漢字・音訓・学年別件数と包含関係、原データ・編集入力・正規化データ・索引の未登録ファイル、シンボリックリンク、許可されたディレクトリ外を指すパスも拒否します。主索引の検索時にも各データセットのSHA-256を照合します。

トップレベルの `reference/` は現在のwheelには同梱しません。リポジトリまたはeditable installでの利用を前提とします。
