# 収録データの利用条件と出典表示

確認日: 2026-09-01

## 国立国語研究所の配布データ

次の原データと正規化版は **クリエイティブ・コモンズ 表示 4.0 国際（CC BY 4.0）** の対象です。

- 『日本語文型データベース（バージョン2026.01）』
  - 著者: パルデシ・プラシャント、砂川有里子
  - 提供者: 国立国語研究所 研究系
  - DOI: `10.15084/0002000610`
  - 公式ページ: https://repository.ninjal.ac.jp/records/2000610
  - 利用許諾: https://creativecommons.org/licenses/by/4.0/
  - 本フォルダでの加工: XMLをJSON Linesへ変換し、ルビ相当の注記を表記と読みに分離し、接続欄の削除記号を構造化した。
- 『教育基本語彙データベース（2009年版A/B）』『日本語教育基本語彙データベース』
  - 提供者: 国立国語研究所
  - 公式ページ: https://mmsrv.ninjal.ac.jp/brfvep/
  - 本フォルダでの加工: 2009年版A/Bを通し番号で結合し、列名を機械処理用のキーへ対応させてJSON Linesへ変換した。

利用時は、国立国語研究所、データ名、版、上記URL、CC BY 4.0、および加工した旨を表示してください。

## Unicodeの文字データ

- データ名: 『Unicode Character Database 17.0.0』`UnicodeData.txt`
- 提供者: Unicode Consortium
- 公式ページ: https://www.unicode.org/Public/17.0.0/ucd/UnicodeData.txt
- 利用許諾: https://www.unicode.org/license.txt （Unicode License v3）
- 本フォルダでの加工: 公式文字名が `HENTAIGANA LETTER` の285字と、`HIRAGANA LETTER ARCHAIC YE`・`HIRAGANA LETTER ARCHAIC WU` の2字を抽出した。公式文字名から `romanized_name_components`（ローマ字構成要素）を抽出し、それを手掛かりに `modern_search_candidates`（編集上の現代仮名検索候補）を付した。後者はUnicode Consortiumによる字源・翻字の認定ではない。

字形画像は収録していません。仮名候補は古文書画像の字形同定結果ではなく、検索候補としてだけ用います。再利用時はUnicode Consortium、Unicode Character Databaseの版、Unicode License v3、および加工した旨を表示してください。

## 文部科学省の学習指導要領コード

- データ名: 『学習指導要領コードのコード表（全体版）』
- 提供者: 文部科学省
- 公式ページ: https://www.mext.go.jp/a_menu/other/data_00002.htm
- 利用規約: https://www.mext.go.jp/b_menu/1351168.htm
- 本フォルダでの保存と加工: 小学校82V12、中学校83V11、高等学校84V10の公式CSVは、国語以外も含む全教科版の原ファイルをそのまま保存した。正規化データでは、そこから教科等が「国語」の1,859件だけを抽出してJSON Linesへ変換した。

文部科学省ウェブサイト利用規約は公共データ利用規約（第1.0版）に準拠し、出典表示と、編集・加工した場合の明示を求めています。第三者が権利を有する部分は別途確認が必要です。正規化データでは数値・コード・指導要領テキストの国語科該当行だけを扱い、教材作品・画像は収録していません。

## 常用漢字表と学年別漢字配当表

- データ名: 文化庁『常用漢字表（平成22年内閣告示第2号）』および『常用漢字表の音訓索引』
  - 公式ページ: https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/kijun/naikaku/kanji/
  - 本フォルダでの加工: 公式HTMLとPDFを版固定して保存し、2,136字と音訓4,388件（音読み2,352件・訓読み2,036件）を一字一件のJSON Linesへ変換した。音訓欄と例欄の個別対応は推定せず、別配列として保存した。
- データ名: 文部科学省『小学校学習指導要領（平成29年告示）』別表「学年別漢字配当表」
  - 公式PDF: https://www.mext.go.jp/content/20230120-mxt_kyoiku02-100002604_01.pdf
  - 本フォルダでの加工: 公式PDFで画像化された文字を日本語OCRの補助と目視で照合して転記し、第1学年から順に80・160・200・202・193・191字、合計1,026字を一字一件のJSON Linesへ変換した。配当は文字単位だけであり、個々の音訓の学習学年は付加していない。
- 利用規約: https://www.mext.go.jp/b_menu/1351168.htm

再利用時は文化庁または文部科学省、資料名、告示番号、公式URL、およびHTML・PDFから正規化または転記した旨を表示してください。ロゴ、ページ画像、書体見本、第三者の著作物は正規化データへ収録していません。

## 手整備の知識レコード

`historical_kana_and_scripts.json`、`makurakotoba.json`、`japanese_poetry_forms.json`、`japanese_grammar.json` は、各レコードの `source_refs` が示す国・地方公共団体、国立研究機関、国立国会図書館、国公立大学などの公的機関の資料をもとに、規則・例外・判定限界を短く構造化したものです。文化庁『送り仮名の付け方』は、通則1〜7の本則・例外・許容、適用範囲および掲出例を構造化した抜粋として保存します。それ以外の参照元の論文本文、作品本文、画像、字形表は転載していません。

`world_poetry.json` は、各国の文化・教育機関、図書館、博物館、大学・研究機関の資料と、詩の教育・普及を継続的に担う非営利機関の用語集を根拠にしています。個人サイトと共同編集型百科事典は根拠にしていません。複数の分類軸を本データベース用に組み合わせた33レコードは、レコード全体をすべて `classification_status: editorial_synthesis` とし、個々の事実の典拠は `source_refs` に記録しています。

## 出典台帳の役割と保存範囲

`sources.json` の各出典には `publication_role` を付け、機関自身の発行物、機関が刊行する学術的資料、公的機関が公式基盤上で公開する回答・解説、書誌・典拠レコードを区別しています。これは公開主体の性質を表すもので、本文の転載許諾を表す項目ではありません。

実際に保存できる範囲は `rights.stored_content_scope` に記録しています。CC BY 4.0、Unicode License v3、文部科学省ウェブサイト利用規約に基づいて保存する配布データを除き、原則として `metadata_links_and_factual_summary`、すなわち書誌情報、リンク、規則・形式に関する事実の短い要約だけを収録します。再利用時は各レコードの `source_refs` と `sources.json` の出典表示を引き継いでください。
