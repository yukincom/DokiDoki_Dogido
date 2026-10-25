# 本棚のJ-lens読出しとデータ保管案（2026-10-25）

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

## 別データリポジトリ案 — 未実施

ユーザーから、句集と内部表現を同じデータ専用リポジトリへ置き、本体からリンクする案、およびrawを非公開GitHub LFSへ保管する案が出た。次の分離を提案する。

| 保存先 | 内容 |
|---|---|
| 現在のドギド本体 | 本体・本棚・J-lens読出しコード、データ側への案内 |
| 非公開のデータ専用リポジトリ・通常Git | 句・対話・材料・改訂、J-lens結果、モデル/レンズ識別、句IDと各hashを結ぶ一覧 |
| 同じデータリポジトリ・LFS | `hidden.f32` 等の大きな原配列、必要な画像 |
| Mac | 実行中の記憶正本、軽い句集と候補JSON。rawはアップロード・取得照合完了後に整理可能 |

既存の研究保全形式 `YYYY/MMDDHHMM/snapshots/<hash>.json` と `internal-representations/request-<順>-<hash>/` を流用する。別の採取・保全経路は作らない。SSDと同じ構造を全部Gitへ入れる前に、Mac側の台帳に依存している「句ID→最新版snapshot」の一覧をデータ側へ持ち出せるようにする。公開物を作るなら非公開原本から明示選択する。現行書出しには対話・個人メモが含まれ、manifestには入力token列・生成文・ローカルパスもある。

GitHubの通常Gitは100 MiB超の単体ファイルを受け付けない。LFSの含有枠はFree/Proでstorage 10 GiB、download bandwidth 10 GiB/課金周期、Team/Enterprise Cloudで各250 GiB。別リポジトリを作るだけでアカウント枠が増えるわけではない。113.44 MiB/句なら10 GiBは約90句相当で、他のLFS使用分も消費する。ユーザーの契約・使用量は未確認。[大容量ファイル](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)、[LFS課金と含有枠](https://docs.github.com/en/billing/concepts/product-billing/git-lfs)。

ローカルrawの削除を追加する場合は、アップロード成功だけで完了にせず、別の作業領域へ再取得してhashと句への紐付けを照合し、復元方法を確定してから行う。今回、リポジトリ作成・アップロード・既存履歴の移動・raw削除はしていない。

## 接続後の確認

既存本棚テスト91件と追加の読出し・Origin/CSRF・cache/元配列分離・停止する子プロセスの回収テスト3件、Pythonの数値とbindingテスト2件、画面関連101件が通過した。releaseビルド成功。macOSの本棚で実際の保存句から読出し、15→25層の切替と候補表示を確認。別の画面で既存キャッシュを再表示し、前=位置1、中=58、後=116、後の最終層の先頭が採用停止tokenと一致すること、生の64次元表示も維持されていることを確認した。

数値の採取・意味の妥当性・表示の動作は別の検証範囲として扱う。今回の検証はJ-lensの途中層が正しい概念を表すことを証明しない。
