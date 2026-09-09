# マイクのエコー除去（macOS実機試験用）

2026-09-08: `voice_input` に任意のWebRTC AEC3経路を接続した。
ビルド・非録音自己検査・既存音声経路の自動試験・架空TTSのオフライン試験まで実施。
**実マイク／実スピーカー／Minecraftとの同時動作は未確認。既定は従来入力 `off` のまま。**
国語Web対話の本体接続とは別の変更で、そちらは引き続き未接続。

2026-09-09起動修正：ユーザーの独立音声試験が形式判定で二度停止した。録音なしの形式検査ではUSBマイク16kHz・tap48kHz・aggregate公称16kHz。一度目は同一rateを要求して停止。最初の修正はrate比で160対480フレームになると仮定したが、実コールバックは双方512フレームだったため停止した。Core Audioが同じaggregate I/O周期へ揃えて渡した実フレーム数を正とし、各bufferの同一フレーム位置を3chへ束ねて一つの常駐AudioConverterへ渡す方式に修正した。streamの表示rateは形式変更検知にだけ使い、機器設定は変更していない。同位置の合成インパルス・左右逆相・frame不一致停止を非録音自己検査。**二度目の修正後の実キャプチャ開始と音響品質は、ユーザーの再試験待ち。**

## 対象と境界

- ドギドのTTS・効果音とMinecraft音声を含む、**Macの再生音全体**をエコー推定の参照として一時取得する。
  他アプリの再生も参照に含む。アプリ限定取得は未実装。通話・機密な再生などは試験中に止める。
- 参照音と未処理マイクはメモリとローカルpipe内だけ。音声ファイル化・外部送信・診断ログへの波形記録はしない。
  通常起動時の**処理済みマイク**は従来どおり一時WAV→ローカルwhisper.cpp→認識テキストの既存配送へ進む。
  AECは完全消去を保証しないので、残った再生音がSTTに届く可能性はある。
- Appleの音声専用Core Audio process tapを使う。画面・動画を取得せず、既定入出力を変更せず、再生をミュートしない。
  BlackHole等の仮想ドライバーは導入しない。私有aggregate deviceはキャプチャ中だけ作成して停止時に破棄する。
- マイクとステレオ参照を同じaggregate clockに載せ、参照側のdrift compensationはCore Audioに任せる。
  streamの形式情報では別rateが表示されても、実際の `AudioDeviceIOProc` は現在の一つのI/O周期として各bufferを同じフレーム数で渡した。この実フレーム数と共通入力時刻を検査し、同じ位置のマイク＋参照左右を3chへ束ね、一つの常駐Apple AudioConverterで16kHzへ揃えてから、10msずつ単一AECインスタンスへ供給する。
  参照の左右もマイクと同じ変換器を通す。buffer間のフレーム数またはI/O時刻が合わなければ停止する。
  ラッパーのch数制約に合わせてmonoマイクを2chへ複製し、AEC後だけmonoへ戻す。
  ステレオ参照を事前に平均して逆相音を消してしまわない。
- AECはRMS／発話区切り／Silero／Whisperより前。発話区切り、STT待ち列、短い返事の扱いは変更しない。
  NS（ノイズ抑制）とAGC（自動音量調整）は追加しない。AEC内部のHPFはライブラリの動作どおり。
- 参照欠落・形式不一致・時計の不連続・250ms超の入力滞留・機器変更・子プロセス異常では入力を止める。
  無音PCMを作って異常を隠さず、生マイクへ自動復帰もしない。機器変更後は音声入力を再起動する。
  参照のデジタル無音自体は正常で、再生なしでもマイクを継続する設計（実機では未確認）。

## 独立導入と録音しない確認

macOS 14.2以降、Xcode/Command Line Toolsの対応SDK、`uv`、既存Python 3.11が必要。
この実施環境ではmacOS 26.6.1／SDK 26.5／arm64／Python 3.11.15でビルドした。
リポジトリルートから実行する。

```bash
bash scripts/setup_echo_capture.sh
.dogido_tools/echo-cancel/.venv/bin/python -m dogido_server.echo_input --check
.dogido_tools/echo-cancel/bin/dogido-audio-capture --list-devices
```

導入先はgit対象外の `.dogido_tools/echo-cancel/` のみ。既存のドギド・MLX・スタックちゃんの環境にはインストールしない。
`numpy==2.2.6` と `pywebrtc-audio==0.2.0` を固定し、native helperは署名と候補版の自己検査を通してから置き換える。
更新前のhelperは `bin/dogido-audio-capture.previous` に残す。venv全体の自動ロールバックではない。
`--check` は機器を開かず、`--list-devices` も機器メタデータの読み取りだけ。
`ready_for_device_test` は導入成功を示し、音響効果の合格を意味しない。

形式だけの追加検査は `bin/dogido-audio-capture --inspect-formats`（上記導入先配下）。私有tap/aggregateを一時作成し、streamの形式情報を取得して破棄する。音声コールバックの登録・機器の音声開始は行わないが、OSの権限確認が出る場合がある。`audio_devices_started=0` と各streamのrate・flags・bytesを出力する。
今回の更新前helperは `bin/dogido-audio-capture.before-mixed-rate-20260909` にも退避している。

## 実音声の短い確認（明示起動時だけキャプチャ）

**Mac全体の再生音を参照にすることを確認してから**、まず次を実行する。
マイクとシステム音声取得のOS権限が必要。拒否や開始待ちのタイムアウトで止まった場合は、
システム設定のプライバシーとセキュリティで起動元の権限を確認して再起動する。

```bash
.dogido_tools/echo-cancel/.venv/bin/python -m dogido_server.echo_input --probe-seconds 12
```

このモードは実音声を12秒処理するが、音声を保存・再生・STT送信しない。音量指標だけ表示して終了する。
参照に再生音が来るか、無音中も止まらないか、停止時にキャプチャが終わるかを確認する。
数値だけでは人の声の聞き取りやSTT精度は確認できない。

本体サーバーを別ターミナルで起動し、従来の音声入力を止めたうえでAEC入力を起動する。
**従来入力とAEC入力を同時起動しない。**

```bash
zsh scripts/start_dogido_test.command voice-aec
```

通常の開発環境なら次でも同じ。モデル・サーバー設定は既存の `.env` を使う。

```bash
DOGIDO_VOICE_ECHO_CANCELLATION=webrtc python -m dogido_server.voice_input
```

`DOGIDO_VOICE_ECHO_INPUT_UID` が空ならmacOSの既定マイク。
指定する場合は `--list-devices` のUIDを使う。従来の `DOGIDO_VOICE_INPUT_DEVICE=:0` とは別体系。
必要なら `DOGIDO_VOICE_ECHO_PYTHON` / `DOGIDO_VOICE_ECHO_HELPER` で独立環境の実行先を指定できる。
`DOGIDO_VOICE_ECHO_DELAY_MS=0` は既定の推定。変更は実測に基づく場合だけ。

試験順は「再生なしの人の声」「ドギドだけ再生して人は黙る」「Minecraftだけ再生して人は黙る」
「再生と重ねて話す」。最後は「石炭」「うん」「いいね」「ちょっと待って」の短い発話も確認する。
無言なのに配送されるか、話したのにSilero/Whisperで消えるかを `/dogido` の診断で分けて見る。
実際のスピーカー音量・部屋・マイク位置で確認し、RMSしきい値を先に変えて問題を隠さない。

## 診断と戻し方

`aec_started`、約5秒ごとの `aec_levels`（mic/reference/cleanのRMS）、`aec_failed` を既存の
`capture` 診断として表示する。PCM・認証情報は含まない。stderrは継続して読み、保持末尾は4KiBに制限する。
フレーム停止時の主なnative codeは `1=形式/参照欠落`、`2=時計不連続`、`3=入力滞留`、
`4=不正サンプル`、`5=入力停止`、`6=機器変更`、`7=出力pipe詰まり`。

Ctrl+CでAEC入力とその子プロセスを終了する。従来へ戻す場合は、ヘッドホン等の自己音対策をしたうえで
`DOGIDO_VOICE_ECHO_CANCELLATION=off` を明示して音声入力を起動する。既定機器を元へ戻す操作は不要。
本実装では `.env` を変更していない。

## 今回の検査結果と残る課題

- native自己検査: 機器を開かずリング境界・順序、時計不連続、stereo逆相維持、欠落/NaN/滞留停止、16/48kHz変換、表示rateが異なる場合でもaggregate I/O周期内の合成インパルス位置、buffer間frame不一致の停止を確認。
- 関連自動試験: `test_voice_echo` / `test_voice_input` / `test_app` / `test_player_chat` / `test_audio_segmented_speech` が88件＋49 subtests成功。
- `scripts/check_echo_offline.py`: 自前合成の4条件。漏れ音のみ -18.75dB、逆相stereo -17.95dB。
  **重ねた疑似音声の射影gainは -19.79dBで検査不合格のまま**。疑似信号が減衰する限界を残し、合格値へ緩めていない。
- `scripts/check_echo_speech_offline.py`: 架空の日本語TTS＋合成ゲーム音の3条件。漏れ音のみ -41.93dB。
  再生なしの近端TTSは全体RMS -0.82dB、近端成分射影 -4.38dB。重なり近端成分射影は -8.48dB。
  位置合わせは採点だけ（127〜128 samples）で、実処理に後付け補正していない。
  近端減衰は残る。これは人間の発話・STT正答率・室内での除去率を測った結果ではない。
- OSの実権限、実機のaggregate形式、再生なしの同期継続、長時間clock drift、別出力機器、音声認識と実会話は未確認。
  問題を観測してから方式や設定を見直す。汎用AIやLLMへ音声処理判断を委ねない。

再現コマンド（後者は架空TTSを一時ファイルに生成し、終了時に削除。録音・再生なし）:

```bash
.dogido_tools/echo-cancel/.venv/bin/python scripts/check_echo_offline.py
.dogido_tools/echo-cancel/.venv/bin/python scripts/check_echo_speech_offline.py
```

## OSSと参照

スタックちゃん側で試験したOSS・常駐インスタンスの作法を参照したが、ラボはオフライン比較であり本番接続済みではなかった。
共有venvや既存録音へ依存せず、音声同期・プロセス管理はドギド側の専用実装。

- [pywebrtc-audio 0.2.0](https://pypi.org/project/pywebrtc-audio/0.2.0/): ラッパーApache-2.0。
  [配布LICENSE](https://github.com/strands-labs/pywebrtc-audio/blob/main/LICENSE)にWebRTCのBSD-3-Clauseや同梱依存の条件、
  [WebRTC PATENTS](https://webrtc.googlesource.com/src/+/refs/heads/main/PATENTS)を含む。wheelのLICENSE/NOTICEを保持する。
- NumPy 2.2.6: BSD-3-Clauseほか、wheelの同梱依存ライセンスを保持する。
- [Apple: Core Audio taps](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps):
  音声参照のOS API。インストール済みSDKのtap/aggregate定義も確認した。

このrepoにwheel・バイナリ・合成音声はコミットしない。配布形態や版を変える際は同梱条件を再確認する。
