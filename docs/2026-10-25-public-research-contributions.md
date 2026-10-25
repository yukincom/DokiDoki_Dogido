# 本棚からの公開研究データ提供（2026-10-25）

**公開は本人の判断で保留。** リポジトリの改名・公開・新規作成・公開先へのpushは行っていない。既存の非公開保管と明示設定を維持し、公開モードは既定では有効にしない。以下はローカルに準備した処理の記録であり、公開開始の案内ではない。

本人のGitHubアカウントで公開用のデータをforkへ送り、`yukincom/DokiDoki-Dogido-data` へのPRを作成・更新する。受付サーバーは置かない。本棚の既存 `/api/research-storage` POST、研究保全と検証済み書出しを共用し、新しい採取経路は増設しない。

## 公開する範囲

- 元句・現在句・改訂の本文と読み。
- 採取要求の順序と種類、形状、層・生成位置、出力token ID、実モデルのファイル名・hash、採取実装のhashと数値設定。
- `hidden.f32` の元配列。bytes・SHA-256を維持する。
- J-lensの候補・順位・スコア・数値処理情報、保存済みの手動翻訳。

会話、プレイヤーメモ、材料本文、写真、入力token列、描画済み生成本文、端末の絶対パス、起動引数は許可フィールドに含めない。将来追加される項目も自動では公開しない。句のIDはhashへ置き換え、保管庫・GitHubの数値IDの下へ分離する。

会話欄を除外しても、句や候補語、内部表現には入力の影響が残る。匿名化を保証するものではない。公開モードの初回クリック時に対象範囲を確認し、明示操作時だけ送信する。forkへのpush時点で公開され、PRの採用待ちだから非公開という扱いにはしない。

元snapshot・manifestを編集せず、公開用に別のschemaと内容hashを持つJSONを作る。J-lensは公開manifestのhashへ結び直し、元manifestのhashは `source_manifest_sha256` と区別する。翻訳も公開J-lensのhashを参照する。候補と数値は変更せず、翻訳・LLM・採取をやり直さない。

## GitHubへの接続

初回はGit・Git LFS・GitHub CLIを用意し、公式の `gh auth login --hostname github.com --web --git-protocol https` でログインする。現在の実装はこのログインを前提にする。本棚へトークンや秘密鍵を埋め込まない。未ログイン時は送信せず、ツールチップに必要な操作を返す。

明示設定がない環境では提供機能を有効にしない。既存の `.dogido_tools/research-repository.json` に `mode` がない場合は従来のprivateを維持し、無断で公開範囲を変更しない。公開を改めて承認した場合にだけ使う設定例:

```json
{"mode":"public","checkout":"/absolute/path/to/DokiDoki-Dogido/.dogido_tools/public-research-contributions"}
```

GitHubの本人ID、受付リポジトリの数値ID・公開状態・main、公開形式のmarker、forkのowner/source、PRのbase/headを照合する。privateの保存先には公開モードから送らない。

`contributors/<GitHub numeric ID>/<archive UUID>/` だけを変更する。提供者別のindexなので他人の一覧を上書きしない。同じ保管庫は専用branchと既存のopen PRを再利用し、変更がなければpushしない。Git作者はGitHubのnoreplyアドレスを使う。

毎回独立した作業コピーを使い、既存の提供branchは本棚が生成してローカルに記録したcommit SHAだけ取り込む。末尾の差分だけで、途中に秘密を追加・削除した未知の履歴を信用しない。通常のpushで競合を拒否し、force pushはしない。push前に生成済みSHAを保存するので、通信中断後も自身の変更だけ再送できる。再取得したJSONとLFS実体の照合後にPRを作成する。mergeは自動で行わない。

Git LFSのforkへの保存・転送は親リポジトリの利用枠に算入される。外部利用者がrawを提供できるよう、公開repoの初期データにもLFS実体を用意する。公式資料: [GitHub CLIログイン](https://cli.github.com/manual/gh_auth_login)、[fork作成](https://docs.github.com/en/rest/repos/forks#create-a-fork)、[LFSでの共同作業](https://docs.github.com/en/repositories/working-with-files/managing-large-files/collaboration-with-git-large-file-storage)。

## 確認と公開準備

- Python: 公開投影、元配列の不変、hashの参照、非公開項目の除外、実Gitでのfork相当・同一PR更新・無変更送信・通信中断再送・未知の履歴拒否を検査。GitHub APIとLFS通信は模擬。
- 本棚のUI検査104件、Rustのrepository検査2件とrelease buildが成功。
- 実データ111句・採取6件・J-lens6件・手動翻訳2件を公開用に書き出した。元配列240,599,040 bytesを含む137ファイルを照合。明示除外キーと端末パス断片は公開JSONに残っていない。

現在の非公開repoには過去commitの対話・メモ・端末パス・Git作者情報がある。別名で非公開の履歴を保全し、同名に公開用の新repoを作る案を提示したが、本人は内部表現に個人的な内容が含まれる可能性を踏まえて公開を保留した。公開repoへの切替、別人の実アカウントでの投稿、本体コードの公開は実施していない。
