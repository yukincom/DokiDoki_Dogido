# CA事故に伴う依存・導入経路の更新

2026-10-23。緊急メンテナンス。検査結果は末尾に記録する。

## 根拠と最低版

[Python公式の告知](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)に従い、certifi 2026.10.19、pip 26.2.2、uv 0.13.4を最低版とする。pipは独自のCAを追加して使うため、OS側の対応だけで旧pipを安全としない。初回更新には告知掲載のpip wheel URLとSHA-256を使う。TLS検証を無効にしない。

[webpki-roots 1.0.10](https://github.com/rustls/webpki-roots/releases/tag/v%2F1.0.10)はemSign ECC Root CA - G3を除去した版。Cargo.lockのwebpki-rootsとwebpki-root-certsを1.0.9から1.0.10へ更新する。0.26.11は1.xを再公開する依存で、参照先を1.0.10に更新し、古いCAへのfallbackには使わない。

## 実際のTLS経路

通常のLLM・VOICEVOX・音声配送はreqwest 0.13.5とrustls-platform-verifier 0.7.0を使う。macOSの検証実装はSecurity.frameworkのSecTrust。aarch64／x86_64 macOSの現行feature treeにはwebpki-roots／webpki-root-certsは入らない。root-certsはplatform-verifierのwasm32用依存。Rigのwebsocket featureは現在有効でなく、将来有効にするとtokio-tungstenite経由でrootsを使うためlockも更新する。

これは依存・ソース・feature treeの確認であり、OSの実際の不信頼設定や稼働中プロセスのTLS接続を試験した結果ではない。lockに旧版があることだけで、現在のMac本体が当該CAを信頼していたとは判定しない。

## 導入と既存環境の更新

リポジトリのルートで、対象の専用venvを使う。Python自体のOS／配布元更新は別途行う。

```bash
python -m venv .venv
source .venv/bin/activate
python -m dogido_server.tls_security --bootstrap
python -m pip install -e .
python -m dogido_server.tls_security
./dogido-rust/cargo.sh build --release --locked
```

既存venvでは作り直さず、そのPythonでbootstrapを実行する。bootstrapはvenv以外への書込みを拒否する。最初の取得はhash固定wheelのみで、index・依存取得・pipの版確認通信・cache利用を止める。pipがない場合はofflineのensurepipを先に実行する。対応済みの新しいpipは版を下げず、そのpipでcertifiを更新し、両方の版を検査する。失敗時に通常のpip installへ進むfallbackはない。版の確認は保守CLIと導入経路で行う。通常のRust起動に、使っていないpipの版による起動制限は加えない。

uvを使う場合はこの更新済みvenvから `python -m pip install 'uv>=0.13.4'` で導入し、`sh scripts/check_uv.sh /path/to/uv`でそのuvを確認する。グローバルのHomebrew uvを本体の更新処理が勝手に変更することはない。AECセットアップはPATH上のuvを通信・出力先作成前に検査する。

家庭用セットは配布前に指定uvとライセンスを検査し、manifestへ版を記録する。既存のHomebrew配置に加え、公式archiveとwheelのライセンス配置を同じ収集処理で扱う。wheelはmetadataの版と実行ファイルの所属も照合する。受け取り側でもPython取得前に同じ検査を行う。古い配布物へ接続設定だけを上書きせず、更新済みRust releaseとuvを含むセットを再作成する。設定・記憶・ワールドは移行先へ保ち、実配布・サービス再起動はこのメンテナンスに含めない。

BERT再学習用requirementsにも最低版を追加し、学習モデル・データ・比較条件を変更しない。Colab生成notebookはhash固定pipを専用フォルダへ入れ、そのpipで研究用依存とcertifiを別フォルダへ導入する。Colab標準Python環境を置き換えず、MLX／CUDA／モデルの固定版は維持する。作成済みnotebook・配布ZIPは自動で書き換わらないため、次回利用前に再生成が必要。

## Rust移行と未完了事項

本体の通信・状態・保存判断は既存Rust経路を維持する。今回追加した導入補助は、Rustビルド前のPython環境を更新する役割があり、その場でRustへ置換すると初回導入に循環依存を作る。

小規模候補として `dogido_server/voice_capture.py` の `echo_command()`／`capture_command()` を確認した。`launch_dialogue.py` がPydantic設定から録音コマンドを作る35行の接続で、実行・録音制御・取消は既に `dogido-rust/src/voice/mod.rs` が所有する。移してもPython依存やプロセスは減らず、別バイナリ化は新たな呼出契約と配布対象を増やすため、今回は見送る。共有既定値のRust読込みも `runtime_settings.rs` に既存の経路がある。

UniDic補助の `fugashi.Tagger`、依頼分類workerの `torch`／`transformers`、AEC補助のNumPy／`pywebrtc_audio` は残すSDK境界。置換には別の辞書・推論・音声実装と同等性検査が必要で、今回のアップデートでは変更しない。

[Pytra](https://github.com/yaneurao/Pytra)の共有checkoutはcommit `9f341e04fefd8eacac1081c59e80f4042ee80a6f`と一致することを確認した。重複取得・共有ソースの変更はしていない。互換仕様と `pytra.std` を読み、候補が使う `shutil.which`／`os.access` をそのまま変換できる対応がないことを確認した。依頼元からは最小add関数のRust packageが未生成moduleへのimportによりE0432となった報告もある。ドギド側では変換・生成物採用は行わず、生成コードの同等性を検証済みとはしない。

状況要約の保持案、静止画像での発話品質の実機確認は[静止時の視覚仕様](2026-10-22-stop-vision.md)へ未実装・未確認として記録した。

## 検査結果

- `cargo test --all-targets --locked`：34 target、874件成功、実音声1件は既存skip。`cargo build --release --locked`も成功。
- `pytest -q tests dogido-rust/scripts/test_*.py`：196件成功、11件skip、22 subtest成功。最低版・更新失敗時の停止・旧uv配布拒否・3種類のライセンス配置・生成notebookのhash固定導入と構文、既存の設定・家庭用ランチャー等を確認した。
- 更新済みの本体venv：pip 26.2.2、certifi 2026.10.19、uv 0.13.5。実際のuv版検査とwheelライセンス取得に成功。グローバルのHomebrew uvはこの作業では変更していない。
- 本リポジトリ内4 venvを点検。現役のAEC・依頼分類器venvにはpip／certifi／uvがなく、その部品の更新対象なし。残っていた廃止Web補助venvのcertifiは2026.7.22から2026.10.19へ更新した。廃止したWeb経路は復活させていない。
- 一時フォルダにpipなしの新規venvを作り、offline ensurepip→hash固定pip→certifiの実導入に成功。Colabと同じ専用targetへpipを導入し、`python -S`で実行して別targetへcertifiを導入する正常系も成功した。Colab GPU上のモデル検証は実施していない。
- 更新したreleaseと実設定で、本体／音声の `launch_dialogue.py --check` がともに成功。サービス起動・録音・既存サービスの再起動は行っていない。
- 家庭用セットの完全な再作成・別Macへの配布・Minecraft実接続、OS証明書の不信頼状態と実TLS接続は未確認。作成済み配布物と稼働中プロセスには更新が自動反映されない。

## 公開用差分の検証とPR（2026-10-23）

[更新PR #46](https://github.com/yukincom/DokiDoki_Dogido/pull/46)を作成した。公開main `e2f808d`から、今回の更新17ファイルだけをcommit `baad89e`として切り出した。前段の分類器・身体・画像入力の変更と、進行中の本棚変更は含めていない。PR作成時点では未マージ。

公開用ブランチでもRust 828件（実音声1件skip）、Python 229件（11件skip、36 subtest）が成功し、専用releaseをビルドした。公開版の標準設定による本体checkと既存音声設定のcheckが成功。先行する23分類モデルの実設定は公開版のラベル契約と異なるため、その組合せでの本体checkは不成立として公開側docsにも記録した。

GitHub Actionsはユーザーの指示で停止。pushとPR作成の前後にリポジトリの `enabled=false` を確認し、CIの新規実行・再実行は行っていない。常駐サービスも再起動していない。
