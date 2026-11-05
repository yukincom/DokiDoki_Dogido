# 子機の仕様と運用

2026-11-05時点の実装を対象にする。子機はMinecraftを遊ぶMac、親機は生成モデルと共有窓口を提供するMacを指す。利用者の家族関係には依存しない。

## 役割と起動

| 場所 | 主な役割 | 起動するアプリ |
|---|---|---|
| 親機 | 共有LLMへの接続、登録端末管理、伝言、共通読み辞書の配信 | ドギド共有 |
| 子機 | Minecraft・Fabric、Rust本体、分類器、Whisper、VOICEVOX、端末ごとの記憶 | ドギド |
| 子機の本棚 | 保存済みの川柳・写真・旅の閲覧と後編集 | ドギドの本棚 |

Apple Silicon Mac向け。Minecraft Java、対応Fabric、VOICEVOXと、準備済みの実行セットを使う。[初回導入手順](../../scripts/lan/遊ぶMacの導入.md)に従ってセットの場所を決め、初期設定と両端末でのペアリングを行う。以後は親機の共有アプリを開き、子機で「ドギド」を起動する。

本棚だけ使う場合は「ドギドの本棚」を開く。ゲーム・音声入力・生成モデルは起動しない。ローカルに保存済みの川柳と写真は、親機が停止していても閲覧・編集できる。

## 接続

- Bonjourで親機を発見し、初回に両画面の確認番号を照合して承認する。登録済みの相手とはHTTPSで通信する。
- 同一LANのWi-Fi・有線を利用できる。IPが変わっても登録済みの相手を確認して再接続する。発見した名前だけで別の相手へ切り替えない。
- 子機のMinecraftとRust本体はloopbackで通信する。重い生成LLMは親機へ依頼する。親機のモデルや接続が使えない間は、新しいモデル応答を得られない。
- 親機から子機の本棚を開くには、親機の「ドギド共有」と子機の「ドギドの本棚」を開いておく。本棚操作ごとの承認は不要。詳細は[本棚README](../bookshelf/README.md)。

## 保存場所

以下は実行セットを基準とした相対パス。既存環境では `.env` と `.env.shared` の設定を正とし、更新時に上書き・自動移動しない。

| 内容 | 保存場所・設定 |
|---|---|
| 川柳・会話・旅・本棚の正本 | `DOGIDO_MEMORY_DIR`。配布既定は `.dogido_memory/` |
| 元の川柳 | `.dogido_memory/sessions/<session>/long_term/haiku_entries.jsonl` など |
| 川柳の後編集 | 同じ保存元の `haiku_revisions.jsonl`。原句は保持 |
| 写真の貼付け・本棚の状態 | `.dogido_memory/bookshelf/state.json` |
| 撮影と川柳の帰属 | `.dogido_memory/artifact-jobs/` |
| Minecraftの写真 | `DOGIDO_MINECRAFT_SCREENSHOTS_DIR`。配布既定は `minecraft/screenshots/` |
| 共通読み辞書の取得済みデータ | `.dogido_memory/long_term/shared_readings.json` |
| 接続登録 | macOSユーザーのApplication Support内の `Dogido LAN/client/` |

たとえばセットを `/Applications/ドギドLAN遊ぶMac-20261103` に置いた場合、写真フォルダは `/Applications/ドギドLAN遊ぶMac-20261103/minecraft/screenshots`。独自gameDirの撮影先と、本体・本棚の撮影先設定を一致させる。本棚用に写真や川柳を別フォルダへ複製しない。

2026-11-05に確認したドギド写真3枚は854×480px、1枚約260〜335KB、合計約0.88MB。同程度なら1,000枚で約300MBが目安であり、サイズ上限や将来の容量保証ではない。手動のMinecraftスクリーンショットは解像度・内容によって大きくなる。容量対策として原画像の自動縮小や、子機本棚による期限削除は行わない。

## 音声・辞書・伝言

マイクが使えない場合も、文字入力 `/dogido <文章>` と読み上げは別経路で利用する。`voice on` で音声を有効にし、`voice off` はマイク入力・読み上げを休止する。音声入力workerが終了した場合の再開は、機器を確認して「ドギド」を開き直す。

共通読み辞書は親機で編集する。子機の接続係が約10秒ごとに取得し、切断中は最後の有効な辞書を使う。子機本棚には辞書編集画面を出さない。[読み辞書共有の詳細](../2026-11-05-shared-reading-dictionary.md)を参照。

親機の共有画面からの伝言は登録PC全員が対象。表示・再生の確認と未達の扱いは[伝言仕様](../2026-11-05-family-messages.md)に従う。

## 更新と確認範囲

更新するのは検証済みのアプリ・プログラムのみ。`.env`、`.env.shared`、記憶、画像、ワールド、接続登録をセットごと置き換えない。設定修正が必要な場合は対象項目を限定する。

今回の実機確認は、子機本棚の起動・3句と3枚の表示、親機本棚からの取得、原句と画像の内容一致。実モデル・新しいMinecraft撮影・実マイク・スピーカー・スリープ復帰を、この本棚変更の検証済み範囲へ含めない。

実装入口は [family-client](../../scripts/family-client/)、[LAN接続](../../scripts/lan/)、[配布セット作成](../../scripts/build_family_client.py)、[Rust起動](../../dogido-rust/scripts/launch_dialogue.py)。長期計画は[LAN運用計画](../2026-11-03-remote-mac-runtime-and-multiplayer-plan.md)を参照し、古い閲覧専用コピー案は現行本棚の仕様に使わない。
