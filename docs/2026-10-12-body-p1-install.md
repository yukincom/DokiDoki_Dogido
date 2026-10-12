# 3D身体P1の本線導入記録 — 2026-10-12

状態: **mainへの統合、対応Modの配置、普段のサーバーappとMinecraftの起動・接続を確認済み。身体の位置実測と実モデルの位置理解は、後続の実機テストで確認する。**

## 統合した内容

- 書類側`DokiDoki-Dogido`のmainを`f186d1c`から`7adb2f1`へ進めた。P1の開発は隣の`DokiDoki-Dogido-body-integration`で行い、通常の起動・編集先をmainへ揃えた。
- P1実装`5e9ffaa`に、本線の乗り物14種の呼び戻し`8dc5781`、川柳相談修正`40fa04f`、ライセンス更新`f186d1c`までを統合した。`observe.rs`の競合では身体観測と乗り物観測の双方を、新しいイベントの処理内に残した。
- 身体Mod 0.4.0 / Body API v3が同時刻の身体とowner位置を採り、既存adapterのHTTPイベントからRustの現在／前回の状態へ渡す。通常会話の一回生成で位置材料を利用する。
- モデル・テクスチャ・エクスポート元データは配置前のバイト列を維持。既存の文書推敲・設定・記憶も保持した。身体観測以外の一般センサー移行とモデルによる行動選択は後続。

## 自動検査

本線の`40fa04f`までを含む`5d5a7e6`で全体検査を実行した。その後の`7adb2f1`への差分はライセンスとREADME等の文書だけで、実行ソースは同じ。

| 対象 | 実行・結果 |
|---|---|
| Rust | `cargo test --all-targets --locked --offline`：677成功、33 target、失敗・ignore 0 |
| Rust静的検査 | `cargo clippy --all-targets --locked --offline -- -D warnings`：指摘0 |
| Rust成果物 | `cargo build --release --locked --offline`：成功 |
| adapter | `./gradlew --offline test build`：139成功、24 suite、失敗・error・skip 0 |
| 身体Mod | 同上：39成功、6 suite、失敗・error・skip 0 |

これらはコード・模擬通信・ビルドの検査。実際の位置理解や身体の描画・音・移動を確認した結果とは分ける。

## 配置と起動確認

Minecraft終了とRust本体の停止を確認してから配置した。使用中のMinecraft起動構成の`mods`にある旧`cube-dogido-lab-0.1.0.jar`を退避し、次の2本へ揃えた。API専用JARはゲームへ入れていない。

| Mod | SHA-256 |
|---|---|
| `cube-dogido-lab-0.4.0.jar` | `bbd1a2f8cb7e46b4b6ce6a482a578522b797143ac9cb06a251d5a2bcefadd6e1` |
| `dogido-fabric-client-0.1.0.jar` | `72f61eb6f98ec96fc7fe7ba87fc96b4fe12d96407308e9e2085460cb294ccd13` |

adapterのログ上の版は`0.1.0+20261011body1`、build識別子は`2026-10-11-body-observation-p1`。ファイル名・識別子に加え、導入時のハッシュを上表で固定した。

- 普段の`apps/ドギド サーバー.app`から本体を起動。20:42:37 JSTの`server_ready`、main内のrelease実行ファイル・作業場所、起動前検査の成功を確認した。
- `/healthz`は`ok=true / runtime=rust / dialogue_ready=true`を返した。
- ユーザーのMinecraft起動後、20:53:36の新adapter読込み、身体Mod `dogido_experiment 0.4.0`の読込み、20:53:43のHTTP session成立を確認した。
- 20:54以降、ゲームログへ既存の危険警告・安堵の発言が届いた。これは通常接続の証拠で、身体位置の配送・モデル理解の合格とは数えない。

## 後続の実機テスト

今回はユーザーの「あとで実機テストする」に合わせ、身体の操作と位置理解の評価を後日に残した。次回は同じmain・Minecraft起動構成を使う。LAN公開は不要。

1. 開けた同一次元で、既存3Dドギドの8ブロック以内へ移動する。身体がない場合だけ`/summon dogido_experiment:dogido ~ ~1 ~2`で一体を作る（これはワールドZ方向への配置）。
2. 以下で対象と結び付け、水平移動を止めて状態を確認する。

   ```mcfunction
   /dogidobody bind @e[type=dogido_experiment:dogido,sort=nearest,limit=1]
   /dogidobody direct
   /dogidobody wait
   /dogidobody status
   ```

3. プレイヤーだけを身体の前→右、近→遠、遠→近へ動かし、位置や距離変化を尋ねる。現在／前回は隣り合う観測なので、距離変化の問いは移動中に行う。
4. 同じ場所でプレイヤーだけ振り向く対照例と、未ロード・次元変更・再接続時の古い位置の失効を確認する。
5. 問い・モデル生成に使った現在／前回の値・返答を対応付け、[計画の受け入れ条件](2026-10-11-body-integration-plan.md#判定と受け入れ条件)で判定する。`server_pose`を視認の証拠に読み替えない。

確認待ち: 身体の描画・C2S/S2C位置配送・位置理解、被弾・食事・保存再開・復活・次元転送、会話・戦闘・川柳の一巡。自律行動や一般身体センサーは未実装のため、このP1の合格条件には含めない。

## 復旧点

配置前ファイル、旧Rust・旧Mod、元のGit差分・index、配置ハッシュ、検査ログは`.dogido_tools/checkpoints/2026-10-12-body-p1-install/`に保存した。`installed.json`が導入内容の一覧。

戻す場合はMinecraftとドギド本体を終了し、その後の編集・記憶・ワールドを別途保存してから、バックアップ中の必要なファイルを選んで戻す。フォルダ全体や古いGit indexを現在の作業へ一括上書きしない。このバックアップにワールド保存は含まれておらず、旧Modへ戻した際のワールド互換性は未確認。
