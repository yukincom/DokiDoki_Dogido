# ドギドの本棚 — 仕様と開発入口

2026-11-05時点。川柳・写真・旅を閲覧し、原句を残して後編集するMacアプリ。ドギド本体と同じリポジトリ・Rust実行ファイル・記憶を使う。

## 本体コードの場所

| コード | 責務 |
|---|---|
| [dogido-rust/src/bookshelf/](../../dogido-rust/src/bookshelf/) | 読取り、HTTP API、権限、写真、削除・復元 |
| [tools/dogido-library/](../../tools/dogido-library/) | HTML・CSS・JavaScriptの画面 |
| [tools/dogido-library/macos/](../../tools/dogido-library/macos/) | Swiftの窓、入口、未保存確認、起動・終了 |
| [start_dogido_library.py](../../scripts/start_dogido_library.py) | 既存設定の読取りとRust本棚起動 |
| [bookshelf_remote.py](../../scripts/bookshelf_remote.py) | 親機の子機用画面とHTTP中継 |
| [bookshelf_bridge.py](../../scripts/lan/bookshelf_bridge.py) | 登録済み端末との本棚通信 |
| [haiku_artifacts.rs](../../dogido-rust/src/haiku_artifacts.rs) | 保存済み原句と撮影記録の照合・写真登録再開 |

開発の正本は書類側の `DokiDoki-Dogido` リポジトリ。アプリケーションフォルダの `.app` は起動用の窓で、`DogidoProjectRoot` に指定された本体の起動スクリプトと画面を利用する。ソースをアプリ内や別の開発worktreeへ分散して管理しない。

## 入口と権限

親機版は「私用／子機用」を選ぶ。「ファイル → 本棚を選ぶ」（⌘0）で戻れる。子機版はそのMacの本棚を直接開く。未保存編集・保存中・フォルダ選択中の切替は待つ。

| 利用画面 | 閲覧・川柳の後編集 | 川柳・写真の削除／復元 | 共通読み辞書 |
|---|---|---|---|
| 親機・私用 | 自分の記録 | 可 | 表示・編集 |
| 親機・子機用 | 接続先の記録 | 可 | 非表示 |
| 子機 | 自分の記録 | 不可 | 非表示。ドギド本体は取得済み共有辞書を利用 |

権限はボタン表示と保存APIの両方で確認する。子機本棚のエラーはドギドの固定文面で案内する。保存結果が確認できないときは、成功や未保存を断定しない。

## 正本・編集・削除

各Macの既存 `memory_dir` が正本。本棚を開くための複製や同期先は作らない。親機の子機用画面はメモリー内で応答を表示し、子機の川柳・写真・履歴を親機のディスクへ保存しない。

後編集は既存のrevision writerへ渡し、元句・版・操作UUIDを照合する。原句を上書きせず、同じ保存の再送で履歴を増やさない。未保存入力は通信失敗で消さない。

削除は既存のごみ箱処理で30日間復元できる。子機の本棚では起動時・定期の期限切れ物理削除をしない。親機自身の私用本棚の整理は既存仕様に従う。

## 写真と接続が切れたとき

写真の正本は、撮影したMacの設定済み `minecraft/screenshots/` など。詳しい配置と容量の実測例は[子機README](../family-client/README.md#保存場所)に記載する。

- 子機のローカル本棚は、親機への接続がなくても保存済みの写真を表示する。
- 親機から子機を見るには、親機の「ドギド共有」と子機の「ドギドの本棚」が必要。起動時に未接続なら接続案内を出す。
- 写真の取得が接続切れで失敗した場合は、その写真枠に「子機とつながっていません。本棚を開いて、写真を選び直してください。」を表示する。小さな一覧では「未接続」と表示する。
- 写真自体が見つからない場合は保存場所の案内に分ける。読込み済みの写真を接続切れだけで消さない。接続復帰後に写真を選び直すと再取得する。
- 撮影先の設定違いで登録が保留された写真は、子機本棚の起動時に保存済み台帳から登録を再開する。原句のhash・撮影先・PNGのhashを照合し、画像や原句を移動・複製・再生成しない。

## 起動とビルド

リポジトリルートに準備済みのPython環境とrelease版Rust本体が必要。通常は配置済み「ドギドの本棚.app」を使う。

```sh
# 保存先などを既存設定から読む。モデル・Minecraft・マイクは起動しない。
scripts/start_dogido_library.command --profile self
scripts/start_dogido_library.command --profile child
# 親機から登録済み子機を開く。複数台登録時は対象IDを指定する。
scripts/start_dogido_library.command --profile remote --device '<登録済み端末ID>'
```

Mac窓は [build_dogido_library_app.command](../../scripts/build_dogido_library_app.command) で作る。`--role parent|child`、`--project-root`、`--app-dir`、親機版の宛先指定 `--device` を受け取る。現行ビルドはApple Silicon／macOS 14.2以降を対象とする。本棚アプリだけではRust・Python補助環境・本体UI一式の代わりにならない。

## 検証と詳細資料

関連試験は [Rust本棚テスト](../../dogido-rust/src/bookshelf/tests.rs)、[本棚UIテスト](../../tools/dogido-library/)、[起動・中継](../../tests/test_bookshelf_family_launcher.py)、[実Rust・TLS往復](../../tests/test_family_bookshelf_end_to_end.py)、[LAN橋渡し](../../tests/test_lan_bookshelf_bridge.py)。自動試験は専用の一時保存先を使い、本番の句・画像で削除試験をしない。

両Macで3句・3枚の表示を確認し、写真配信と撮影hash、原句・PNGの保持を確認した。編集・削除・復元・再送・切断案内は隔離データ／模擬接続で確認し、実作品の編集・削除を検証目的で行っていない。

通信・保存の詳細は[家庭内本棚仕様](../2026-11-05-family-bookshelf.md)、従来の私用本棚は[本棚統合仕様](../2026-10-13-bookshelf-integration.md)、共通辞書は[読み辞書共有](../2026-11-05-shared-reading-dictionary.md)を参照。
