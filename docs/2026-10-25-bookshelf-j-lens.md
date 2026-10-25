# 本棚のJ-lens読出し・手動翻訳・データ保管（2026-10-25）

## 接続した範囲

既存の採取・句との紐付け・研究用保全・本棚の要求別APIを再利用する。保存済みGGUF活性をCPUで後処理する `dev_tools/haiku_internal_probe/gguf_jlens.py` を追加した。生成モデルへの要求、プロンプト、採取範囲、句本文、修正履歴は変えない。MLXでの独立実験 `measure.py` はそのまま残す。

本棚の「内部表現」内の前・中・後を開くと、各時点の上位16トークン候補を表示する。層は1〜30から選択し、最初は15層を表示する。候補は単語だけでなく文字、空白、記号、特殊トークンを含む。言語や意味で候補を削らず、GGUFのtoken pieceを保持する。数字はsoftcap後・sampling前のlogitで、確率表示ではない。全語彙で正規化したlogprobもJSONへ保存する。

- 前: 応答の最初のトークンを予測した位置。
- 中: 採取された生成位置の中央。JSONの項目名や空白も位置に含む。
- 後: 採取完了の場合のみ、最後に採用されたトークンを予測した位置。EOSとは限らず、生成上限で終了した記録にも末尾がある。生成終了後に追加forwardはしない。
- 生の数値: 各パネルの下の折りたたみへ移した。従来の位置・層・次元の指定、64次元ずつの表示、元ファイルのdownloadを維持する。

途中1〜29層は以前の公開J-lens行列を `h @ J.T` で適用し、同じGGUFの最終RMSNorm・出力head・softcapで読む。30層はレンズを通さずnative headを使う。GGUFではRMSNormの係数にHF側の `+1` が既に反映されているので、再加算しない。出力headは固定GGUF内の共有 `token_embd.weight`。全語彙のfloat32 headを常駐させず、4096語ずつ復号する。

## 適合性と記録

公開レンズは `google/gemma-4-26B-A4B-it` のBF16側で学習されたもの。本体のQAT GGUFに対する専用レンズ学習は未実施であり、途中層は `exploratory_transfer` として表示する。最終層の数値照合を、途中層の意味やレンズ精度の検証と読み替えない。

固定条件は [gguf_jlens_binding.json](../dev_tools/haiku_internal_probe/gguf_jlens_binding.json)。モデルSHA-256、行列SHA-256、公式GGUF readerの各ソースhashを確認してから計算する。別モデル・境界・次元は拒否し、別モデルへ暗黙に転用しない。

実モデルによる短い生成で8位置の最上位tokenが全て採用tokenと一致した。native APIが上位16候補を返した2位置ではIDと順位が一致し、logprobの最大差は約0.0000214。MTP採用位置ではnative APIのtop_logprobsが空のため、残り6位置を上位16候補まで比較済みとはしない。入力は既存の保存済み見どころ生成要求を使用した。

候補JSONには応答ID、manifest/raw/model/lens/processor/bindingのhash、各位置の採用token、使用方式、NumPyとGGUFソースの識別情報を保存する。キャッシュは 一般設定の `memory_dir/bookshelf/j-lens/` に置く。句と要求とmanifestの照合後にだけ再利用する。元データが後日保管先へ移っても、manifestと紐付けが残る限り既存キャッシュを読める。現在の実装はrawの移動・削除をしない。

既存 `/api/poems/{id}/internal-capture/{index}/{kind}` の `kind=j-lens` を使う。GETは既存結果だけを返し、欠損時のみ画面が同じAPIへPOSTする。POSTは従来のOrigin/Host/CSRF検査を使い、同時1件、入力書込みを含む120秒の上限を設ける。キャッシュ改変・元manifest不一致を隠す再生成はしない。元配列を読むときは既存のSSD優先・全hash検証経路を使う。

## ローカル設定と配布物

`scripts/start_dogido_library.py` は `.dogido_tools/j-lens/assets.json` がある場合だけ読出しを有効にする。設定は次のローカルパスを持つ。

```json
{
  "python": "/path/to/python-with-numpy",
  "model": "/path/to/gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf",
  "lens": "/path/to/reference/lens.npz",
  "gguf_python": "/path/to/pinned-llama.cpp/gguf-py",
  "binding": "/path/to/DokiDoki-Dogido/dev_tools/haiku_internal_probe/gguf_jlens_binding.json"
}
```

この環境では既存の `dogido-llm` Python、既存レンズNPZ、採取器と同revisionの公式GGUF readerを再利用した。設定がなければ本棚・生の数値表示は使え、候補語には未設定と表示する。GGUF readerの外部依存はその公式packageの宣言に従う。今回の読出し実測はNumPy 2.4.6。

本体リポジトリに置くものは、小さな読出しコード・固定条件・テスト・導入説明。レンズ重み約877 MiBのNPZはGitへ追加しない。元の約439 MiBのPTと学習研究の説明は [既存研究](https://github.com/yukincom/AI-Curious-Community/tree/main/j-lens-study)、取得元は [公開レンズ](https://huggingface.co/solarkyle/jspace-lenses)。元PTのSHA-256は `2fbc8d2d11bf834557ec737d1772f6aec37adc00d329945850315005aee29e72`。Gemma由来レンズの利用・再配布条件とコードのApacheライセンスは区別する。

## サイズの実測

採取はfloat32で `位置数 × 30層 × 2816次元 × 4 byte`。1位置330 KiB、300位置96.68 MiB。保存済みの一例では見どころ116・情景192・句44位置、合計113.44 MiBだった。

前・中・後、全30層、各16候補のJSONは見どころ116位置の例で約130 KiB。初回の読出しはモデル・レンズhash照合を含め約16秒。キャッシュ後はモデルや配列を再計算しない。3段階とも同程度なら一句あたり約0.4 MiBの表示用データとなるが、生成段階数や文字列長で変わる。

## 手動翻訳

原文・日本語訳・スコアを同じ行へ並べる。候補一覧の2列、行高、本の外枠は維持し、長い原文と訳は省略表示してhoverで全文を読む。各パネル下部の焦茶色・白文字の「文」を押したときだけ、localhost:8080の現在のモデルへ翻訳を要求する。対話・材料・内部表現・前中後・数値・リポジトリの折りたたみは初期状態で閉じる。

送るのは選択した1時点・1層の上位16件のうち、翻訳対象の `[{"id":123,"piece":"word"}]` だけ。句、対話、材料、スコア、rawは送らない。日本語と記号・特殊トークンは原文を隣へ写す。かなと漢字の混在は文字種、漢字だけの語は既存の共有UniDicの読みを使って除外する。漢字を含むだけで中国語まで日本語扱いしない。ただし漢字の短い断片は言語が一意に決まらず、日本語辞書に載る同形語は複写される。

指示の正本は [j-lens-translation-prompt.txt](../tools/dogido-library/j-lens-translation-prompt.txt)。冒頭は本人指定の次の二文。

> あなたは表示用の短い日本語訳を付ける翻訳者です。
> 以下の単語をそれぞれ独立した単語として日本語に訳してください。カタカナ語で表現できるものは無理に漢字表記を当てはめず、カタカナ語にしてください。

既存要求別APIの `kind=j-lens-translation` を使う。GETは辞書による原文複写と保存済み訳だけを返す。POSTはOrigin/CSRFを検査し、同時1件・90秒上限・再試行なしで翻訳する。一般設定の本体ポートがlisten中ならモデル要求を送らず、本体を自動停止しない。訳は `memory_dir/bookshelf/j-lens-translations/` へ別保存し、元候補・rawを変更しない。readout、prompt、辞書補助のhashと、対象時点・層・複写判定を対応づける。層を切り替えた後に古い応答を別の層へ表示しない。

## 非公開データリポジトリ

保存先は [yukincom/DokiDoki-Dogido-data](https://github.com/yukincom/DokiDoki-Dogido-data)。本体READMEと本棚の句ページからリンクする。句ページ下部の「非公開リポジトリ」→「まとめてアップロード」で、保存済みの全句と対応するJ-lens候補・翻訳・内部表現をまとめて送る。表示中の句や検索条件による部分送信ではない。画面に未保存編集があるときは先に保存を案内する。

既存の研究保全を完了し、保存完了台帳の全句を固定して読み出す。削除済みでも保全された句は含める。`index.json` に句ID→最新版snapshot→要求→manifest/raw/J-lens/訳の対応とSHA-256を持たせる。

| 保存先 | 内容 |
|---|---|
| ドギド本体 | 本棚・読出し・翻訳・送信コード、データ側へのリンク |
| データ専用リポジトリ・通常Git | 句・対話・材料・改訂・メモ、J-lens候補、手動翻訳、照合情報 |
| 同じデータリポジトリ・Git LFS | 検証済みの `hidden.f32` |
| Mac・SSD | 従来の正本・保全データを維持。送信後も自動削除しない |

写真は参照情報のみ。画像、モデル本体、レンズ重み、認証情報は送らない。採取開始前の句へ内部表現を後付けしない。未作成のJ-lens候補は保存済みrawからCPUで読み出すだけで、LLMによる発句・採取をやり直さない。研究保存先の `YYYY/MMDDHHMM/snapshots/<hash>.json` と `internal-representations/request-<順>-<hash>/` を維持し、J-lensと訳は同じ句のフォルダに内容hash名で追加する。

設定はgitignore対象の `.dogido_tools/research-repository.json`。起動補助が既存の本棚設定へ渡す。

```json
{"python":"/absolute/path/to/python","checkout":"/absolute/path/to/DokiDoki-Dogido/.dogido_tools/research-repository"}
```

送信workerは [publish_bookshelf_data.py](../scripts/publish_bookshelf_data.py)。GitHub CLIの既存認証とGit LFSを使う。専用checkoutだけを更新し、本体の未送信コミットはpushしない。リポジトリが非公開であること、GitとLFSの送信先が指定先であることを送信前に再検査する。関係のない作業変更・未送信履歴があれば止め、処理自身の中断記録に一致する場合だけ再送する。期限切れ時は専用の子プロセス群を停止する。

送信後はremote SHAの一致に加え、別の一時cloneへLFS実体を取得し、全ファイルのbytesとSHA-256を照合してから成功表示する。アカウントのLFS契約・残容量は未確認。保存先を分けてもアカウントの利用枠が増えるわけではない。

## 接続後の確認

既存本棚テスト91件と追加の読出し・Origin/CSRF・cache/元配列分離・停止する子プロセスの回収テスト3件、Pythonの数値とbindingテスト2件、画面関連101件が通過した。releaseビルド成功。macOSの本棚で実際の保存句から読出し、15→25層の切替と候補表示を確認。別の画面で既存キャッシュを再表示し、前=位置1、中=58、後=116、後の最終層の先頭が採用停止tokenと一致すること、生の64次元表示も維持されていることを確認した。

数値の採取・意味の妥当性・表示の動作は別の検証範囲として扱う。今回の検証はJ-lensの途中層が正しい概念を表すことを証明しない。

## 手動翻訳・保管の接続確認

- 本棚の自動テストは98件中97件成功、既存の実音声1件はskip。うち翻訳の本体起動中抑止テストはsandboxのsocket制限があったため、その1件を通常のローカル環境で再実行して成功した。削除済みの採取済み句も既存保全から書き出す回帰テストを追加して成功。
- JavaScriptは102件、送信workerのPythonは3件が成功。release build成功。通常の `/Applications/ドギドの本棚.app` を再起動し、各三角の初期閉状態、原文・訳・数値、白文字の「文」を確認。
- 見どころ要求1の生成位置58/116・25層で、日本語4件の原文複写と外国語12件の翻訳を確認した。最初の実確認では旧指示が残っていたため訂正し、本人指定の指示で1回確認し直した。確認済みprompt SHA-256は `e7e971a6b963260e204597a63aea525cd96c1f412d745f58c55b5c832960f709`。指示を変えた履歴は別のhashを持つ記録として保持する。
- 非公開データリポジトリへ111句・採取6件を初回送信し、独立cloneから取得し直した全ファイルの照合が成功した。raw合計は240,599,040 bytes（約229.45 MiB）。本体READMEへのリンク追加もGitHub上で反映した。本体のその他の未送信コミットはpushしていない。
