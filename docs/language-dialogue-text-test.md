# 国語対話の独立テキスト試験

**実マイク・STT・ドギド音声の手動試験は [独立音声テスト](language-dialogue-voice-test.md)。このページのランチャーは文字入力専用。**

**文字だけを手動で試す場合:** [入力台本と実Chrome用ランチャー](language-dialogue-manual-test.md)（2026-09-09）。
`zsh dev_tools/language_dialogue/start_language_web_test.command` で対話待ちへ入り、台本は1行ずつ入力する。会話の自動実行はしない。

状態：独立コンポーネントを維持しつつ、2026-09-12に本体serviceへ国語・語句の限定対話、非同期worker、再生完了台帳、同意済みの専用Chrome検索まで接続。workshopへの統合、統合後の実Minecraft／TTS／Chromeを重ねた本体E2Eは別途確認。Minecraft自動ポーズと川柳カウント停止は2026-09-14にユーザー確認済み。

2026-09-09本体接続：一般雑談はMinecraft観測を持つ既存 `player_chat`、国語・語句の明示質問と学習中の続きだけを有界な `MainLanguageRuntime` へ分けた。戦況・assist・正本DBの明示知識回答・workshopは従来の状態機械が所有する。正本DB回答と通常雑談の同じID付き履歴を解釈器にも共有し、assistant本文は実再生 `completed` 後だけ確定する。workerが一般話題を返しても独立試験用の仮想文脈では答えず、元turnを本体へ戻して現在のMinecraft観測で一度だけ答える。学習中の突然の別話題は、2分以上の間・明示名指し・明示転換なら即時移管し、それ未満の宛先不明入力は5分だけ無言保留する。呼び直し後の固定確認が実再生完了してから、肯定で同じ元turnを一度だけ移管する。戦闘中は旧生成をepochで捨て、一件の話題を10 player turnだけ保留する一方、危険前5往復は危険後3通常turn目まで別枠保護する。純粋な音声叫声は通常履歴へ入れず、コード観測の状況メモに置き換える。

2026-09-12本体Web接続：利用前提が揃うMacでは、同じruntimeへ可視Chrome providerを休眠状態で渡す。Web同意→「ほな一緒にいこか！」の実再生 `completed`→有界workerで専用Chromeへ一度だけ検索、という順序を本体音声台帳へ接続した。調査中は `foreground=web` とWeb用30分期限でambientと自動川柳を止め、明示復帰後は調べた話題一件だけを本体の短期文脈へ渡す。失敗・取消・開始前の戦闘・古いepoch・満杯では開かず、既に読書中の調査文脈は敵対警告後も保持する。**Minecraft自動ポーズと川柳カウント停止は2026-09-14にユーザー確認済み。OS前面復帰と統合後の本体の実Minecraft／TTS／Chrome E2Eは別途確認。** 詳細は [本体会話統合](main-dialogue-integration.md)。

2026-09-09更新：解釈契約に `information_request / casual / other` を追加し、挨拶・感謝・質問を求めない雑談を資料不足の質問としてWebへ送らない。代表的な挨拶はコード固定で短く返す。一般的な雑談は本体ですでに使っている `player_chat` leafへ渡し、国語解釈に失敗した普通の陳述も質問用の聞き返しへ固定しない。この独立経路には現在のMinecraft観測を渡さず、見ていない世界状態を事実として足さないよう明示する。実Google概要は15秒後の1回だけ読み、未完成でも同じタブを再取得しない。研究中に別質問と推定しただけでは文脈を捨てず、「今の続き／別の質問」を確認する。これはSTT誤変換そのものの修正ではない。

独立音声hostには、最初の入力を必ず受理し、受理後に次の意味上の発話型を5件予測する限定参加契約を追加した。明示名指し・話題転換語・質問・Minecraft話題はコードで通し、それ以外も高信頼の大きな断絶だけを通常履歴外へ保留する。失敗・迷いは受理側へ倒し、「待たせたね」等は誰宛かの断定ではなく「脇の会話として解決」と記録する。`handoff` は会話終了の証拠にせず、5分無活動による静音とは分離する。参加予測は返答を生成せず、再生完了した `player_chat` の次発話候補だけを扱う。音声hostは5往復・5分のターン台帳を持ち、assistant発話は発話IDの実再生 `completed` 後だけ履歴へ確定する。生成・参加分類・Googleは有界な直列workerで処理し、完了時にepochを再検証する。StackChanの未検証scene分類器は移植していない。関連380件＋87 subtests成功、変更後の実モデル・実家庭音声は未確認。[音声試験の境界](language-dialogue-voice-test.md)。

その後の16:34ユーザー実試験では通常会話・Web同意・単発取得・取得後対話まで通った。閲覧開始時の追加TTSだけを削除し、検索状態と取得結果はログに残す。無音化後の実音声再確認は未実施。

2026-09-08同意ゲート追加：Webを直接開かず、指定文で子どもの同意を確認→「ほな一緒にいこか！」→対応する発話IDの正常再生完了通知→Web起動の順にした。同意・生成完了・印字だけでは開かない。この時点の独立CLIは `/speech-completed` で明示的に完了を模擬し、本体音声へは未接続だった。関連202件＋32 subtests、Qwenの合成4会話で同意・拒否・撤回・条件つきを確認。[順序と実音声の当時の境界](research/language-overview-dialogue-2026-09-08.md#同意と案内音声の完了を待つweb起動)。

2026-09-08待ち時間更新（当時）：初回は8秒目の読み取り要求1回とし、途中なら復帰時に同じタブを一度再読していた。2026-09-09に上記の15秒・自動再取得なしへ置き換えた。[現在の読み取り契約](research/language-overview-dialogue-2026-09-08.md#2026-09-09の現在契約)。

2026-09-08追加：実検索は停止したまま `--virtual-web` と模擬Minecraft復帰を追加。初回に間に合わない概要の再読を、復帰時の「おかえり！ どうやった？」の配送後へ移した（前面イベント非対応時のみ従来の発話時fallback）。取得済み概要を一時文脈へ再生し、Qwenで4発話＋制御4件の1会話、本文引用一致2件を確認。関連168件＋30 subtests。制御・取得・生成の時間を分離し、未計測値を0秒扱いしない。**実OS前面検知・本体・音声には未接続**。実検索の完成時間の確定とは別の検証。[手順と境界](research/language-overview-dialogue-2026-09-08.md#追加仮想webとminecraft復帰の独立試験)。

2026-09-08前段の記録：通常の `--web` は、子どもの問いをユーザー提案の関西弁の依頼文でGoogle通常検索へ渡す。AI概要・紹介文・取得失敗を分離し、子どもの報告や同じ話題の質問へ概要を入力する。当初は次の報告・質問時の再読を実装し、関連自動テストはドギド側144件＋30 subtests、Chrome側120件。実検索では初回の金床概要を取得し後続会話へ渡せたが、**新しい依頼文と再読を通した実概要取得は未確認**。ユーザーからロボット判定の報告があり実検索を停止した。詳細と残る問題は [今回の記録](research/language-overview-dialogue-2026-09-08.md)。以下の教材選定・裏の資料取得は旧試作で、現在は `--known-sources-only` の診断経路に残す。本体・workshop・音声への未接続は維持。

2026-09-07追記：任意の `chrome-web` 接続を追加。資料不足・説明の不確実さ・明示依頼によるWeb検索、公式ページ本文の取得、子どもの報告との比較、先生への相談提案、冒険への復帰を独立経路で試せる。本体への接続は引き続き行っていない。GoogleのCAPTCHAと、既知URLからの本文取得成功を分けて記録する。[今回の試験記録](research/language-web-dialogue-evaluation-2026-09-07.md)。

同日追加（当時の記録）：MCP単体では、通常Google検索のAI概要を約5秒・最大3回の確認で取得し、概要がなくても同じページの検索結果を返せるようにした。[取得方法と実測](research/chrome-web-ai-overview-2026-09-07.md)。独立対話経路への切替は上記2026-09-08更新を参照。

同日・比較後の修正：検索済みレコードの `rules` と `machine_use` を定義と同じID内に保持。確定かなの音数は既存の川柳用計数関数を1回だけ使用し、一字の配当学年は検索済みの表の値から回答する。どちらも回答用のLLM再生成を挟まない。漢字の読みを推測して数えず、熟語全体の学年へも広げない。回答段階の不足は `missing_kind=evidence/context/none` に分け、本人の文脈不足は確認へ進める。旧形式の `partial + missing` だけではWebを発火しない。明示検索依頼とローカル資料ゼロの扱いは維持する。

修正後も **本体・workshop・音声には未接続**。Qwenで54ターンを再確認し、数値11回答は全件正答。一方、文脈の誤分類・不要検索・意味説明の誤りは残る。[修正内容・再試験・残る問題](research/language-retrieval-comparison-2026-09-07.md#比較後の修正と再試験)。下記の84件は初回の記録であり、今回の関連自動テストは115件＋30 subtests。

関連自動テスト84件が通過。実モデルで全21会話と修正後の対象3会話を確認したが、説明の付け足しなど品質上の問題が残る。[試験結果と未解決事項](research/language-dialogue-text-evaluation-2026-09-06.md)を参照。

## 何を変えたか

現在の会話モデルに、発話と直近の対話から対象・観点・曖昧さを抽出させる。コードがその結果から、通常 `player_chat`、確認、資料検索、本体への返却を選ぶ。確定した質問に対して、公式のローカル資料を検索し、同じ会話モデルが短い関西弁で説明する。

Minecraftの単語の有無だけで振り分けず、「その言葉について何を尋ねているか」を扱う。確認する必要があることと、資料が足りないことは別に記録する。

| 部分 | 担当と記録 |
|---|---|
| 原文 | 入力文字列とtext/voiceの区別を保存。音声認識自体はこの試験では行わない |
| 解釈 | 現在の発話・過去発話の引用とともに、会話種別・対象・観点・解釈候補を提案 |
| 確認 | 回答を変える曖昧さがある場合だけ一問。実際に返した確認文を次の解釈に渡す |
| 検索 | 既存の国語DBと出典付きの短い補足資料。検索語・取得事実・欠損を記録 |
| Web起動 | 明示同意と案内音声のID付き正常終了通知まで待つ。独立CLIは手動模擬、本体は実audio callbackを次のgame eventで回収 |
| 説明 | 資料の説明と、子どもの例への適用を区別。根拠IDと未確認部分を返す |
| 通常会話 | 既存 `player_chat` leaf。音声hostでは再生完了した直近5往復だけを使い、Minecraft観測は未接続と明示 |
| 状態 | 通常／国語優先、未解決の問い、5往復までの対話。理解済みの学習記録は作らない |

研究との対応は [文献調査](research/educational-dialogue-and-topic-switching-2026-09-06.md) を参照。古い対話フレームワークや汎用エージェント基盤は導入していない。

## 起動方法

リポジトリのルートで、通常使っているPython環境を有効にして実行する。サーバー、Minecraft、マイク、VOICEVOXは不要。

```bash
python -m dogido_server.language_dialogue --list
python -m dogido_server.language_dialogue --output logs/language-dialogue/run-01
```

既存の `chat` 設定にあるMLXモデルを使う。モデルはキャッシュ済みのものだけとし、新しいモデルや依存を取得しない。設定がMLX以外の場合は起動せず通知する。設定ファイルを書き換えたり、クラウドAPIに切り替えたりしない。GPUが使えない実行環境では、モデル利用不可として終了する。

個別の確認：

```bash
python -m dogido_server.language_dialogue --case boundary_13 --case boundary_14 --output logs/language-dialogue/run-02
```

結果フォルダは新しい名前を使う。既存の結果は上書きしない。

- `run.json`：モデル・実行日時・対象会話。
- `turns.jsonl`：入力原文、解釈、検索語、取得した資料、返答、参照した資料、状態、処理時間。
- `transcript.md`：入力と返答を対にした引用文。資料へのリンクは発言と分ける。

端末にも入力と返答を順次表示する。特別な画面やtmuxは不要。

## 試験の構成

[試験データ](../tests/fixtures/language_dialogue/cases.json) に、ユーザーが提示した境界例15件と、人為的に作成した短い会話6件を収録した。後者は小学2〜3年生程度の言い方を想定した例であり、児童データや児童モデルによる検証ではない。

境界例の期待する観点、合否条件、ケースID、次の発話をモデルに渡さない。一般の対話方針と検索資料は公開の実装データであり、試験専用の回答文をモデルへ渡す仕組みはない。ただし、これらの問題を見て設計・資料整備をした開発用試験である。未知の問題への汎化性能の測定には、別途未使用の問題が必要。

自動検査は、確認の前に検索しないこと、文脈の受け渡し、出典ID、重複入力、中断、期限などを確認する。固定応答によるユニットテストの成功は、モデルが質問を理解できた証拠ではない。実モデルのログも、IDや状態が正しいだけでは回答の内容が正しいとはしない。語義・自然さ・対象・根拠の適合は別に読む。

```bash
python -m pytest tests/test_language_dialogue.py tests/test_structured_contracts.py -q
```

## 中断と範囲

- `interrupt` は模擬的な優先割込み。生成中の返答を無効化し、保留中の問いは残す。ただしWeb起動の未実行の同意・音声待ちは撤回し、古い完了通知で後から開かない。
- 中断中の入力には `paused` を返し、モデルを呼ばない。隠れた待ち列は作らない。
- `release` だけでは発話せず、次の本人の入力を待つ。
- 生成処理の同時受付は `busy` として返却する。呼出元が再入力の扱いを決める。
- 解釈・説明の生成は同期処理。割込みは結果の配送を止めるもので、GPU上の計算自体を強制停止するものではない。
- 一時文脈は無活動5分で失効する試験用の既定値。子どもに適切な時間と検証した値ではなく、コンストラクタで変更可能。中断が長時間になった場合も無期限には保持しない。
- Web資料を読んでいる間と報告中の文脈は、別の既定値で無活動30分保持する。読書中に通常対話の5分期限で消さないための試験値で、`research_ttl_seconds` で変更できる。理解済みの学習記録には変換しない。
- 一般的な雑談は `player_chat`。明示的なMinecraft操作や本体が所有すべき経路は `handoff` のまま。この試験コンポーネントはゲーム状態を知らないため、観測に基づくゲーム返答や持ち替えを実行しない。

本体へ接続する前に、既存の警告優先処理・入力保留・ワークショップとの入口の使い分けを確認する必要がある。本試験の模擬割込みだけで、実際の戦闘中断や音声配送が検証済みとはしない。

## 資料の範囲

既存の常用漢字表・学年別漢字配当表・国語知識レコードを利用する。補足は [source_cards.json](../dogido_server/language_dialogue/source_cards.json) の8件で、検索語・内容・出典・適用範囲を持つ。別のDB全体の再構築や既存索引の置換はしていない。

擬音語等の分類は [国立国語研究所が紹介する分類](https://www2.ninjal.ac.jp/Onomatope/column/nihongo_1.html)、英字の原稿用紙表記は [JICAの応募案内](https://www.jica.go.jp/cooperation/experience/essay/collect/n_files/00_qa.pdf) などに基づく。後者を全国の学校の絶対規則とはしない。

教育基本語彙の選定レベルは学年ではなく、語の存在は語義の証明ではない。そのため本コンポーネントでは、それらを辞書定義として説明へ投影していない。アンデッドの一意の対義語、特定辞書のエンダー掲載有無、交易・交換・買い物の語義比較は資料不足を含む試験として残す。

## Chromeで資料を読み、戻って話す（任意）

独立試験では `--web` がない限り、本体では利用前提か同意がない限り、ブラウザもMCPも起動しない。ユーザーの [chrome-web-mcp-macos](https://github.com/yukincom/chrome-web-mcp-macos) を専用の仮想環境に置き、公式MCP SDKのstdio接続で使う。普段のChromeのプロフィールや本体設定、既存の汎用エージェント設定は変更しない。

導入元の固定コミットと追加パッチは [chrome-web-source.json](../scripts/chrome-web-source.json)。設定を [裏で読む資料用](../scripts/chrome-web-config.json) と [子どもへの表示用](../scripts/chrome-web-child-config.json) に分ける。このチェックアウトには導入済み。他のチェックアウトで再現するときは、そのルートで通常のPython環境を有効にし、次を実行する。専用ディレクトリが既にある場合は上書きせず、導入元と版を確認する。

```bash
git clone https://github.com/yukincom/chrome-web-mcp-macos.git .dogido_tools/chrome-web-mcp-macos
git -C .dogido_tools/chrome-web-mcp-macos checkout --detach ec29738c9370aef0c6285c5cefdf09e5444edc70
git -C .dogido_tools/chrome-web-mcp-macos apply ../../scripts/patches/chrome-web-overview-reread.patch
git -C .dogido_tools/chrome-web-mcp-macos apply ../../scripts/patches/chrome-web-eight-second-single-read.patch
git -C .dogido_tools/chrome-web-mcp-macos apply ../../scripts/patches/chrome-web-fifteen-second-single-read.patch
(cd .dogido_tools/chrome-web-mcp-macos && uv sync --frozen --python python)
python -m pip install -e '.[web-research]'
```

Apple SiliconのMac、Google Chrome、既存のMLX会話モデルを想定する。モデルの自動取得はしない。通常の `--web` は表示用の専用ChromeにGoogle検索を開く。試験終了時に所有するMCP接続と専用Chromeを終了する。

本体は `DOGIDO_MAIN_LANGUAGE_DIALOGUE_ENABLED=true` と `DOGIDO_MAIN_LANGUAGE_WEB_ENABLED=true` のとき、起動時に専用MCP実行ファイル、MCP SDK、表示用設定、Google Chromeを副作用なしで確認する。揃わなければWebだけを無効にしてサーバーは続行する。揃っていても同意案内の実再生完了までMCP／Chromeを始めず、検索はgame-event worker外で行う。専用clientはsession終了時に閉じる。Minecraftのpause・再フォーカス・通常Chromeの操作はしない。

以下の非表示根拠・確認済み教材の仕組みは旧試作の診断用。

追加パッチは `fetch_url` の任意引数 `expected_url`。移動先と表示直前のURLが確認済み教材と一致するか検査し、不一致ならタブを前面に出さず閉じる。表示用クライアントはこの引数を持つ版だけに接続する。子ども自身の後続クリックや表示後の自動遷移を止める仕組みではなく、ブラウザ全体の閲覧制限ではない。

```bash
python -m dogido_server.language_dialogue --web --interactive --school-grade 3 --output logs/language-dialogue/web-interactive-01
python -m dogido_server.language_dialogue --web --cases tests/fixtures/language_dialogue/web_cases.json --output logs/language-dialogue/web-01
```

GoogleがCAPTCHAで止まっているとき、本文取得と対話だけを分けて確認するには `--known-sources-only` を併用する。この診断モードはGoogleを呼ばず、ローカル資料に記録済みの公式URLだけを読む。新しい出典を発見できたことにはならない。

### 旧試作：既知資料の診断と、戻った後の会話

1. 曖昧な問いには先に一問確認する。国語の言葉が出ただけでは検索しない。
2. ローカル資料がない、資料があってもモデルが説明を部分的／不明とする、または本人がWeb検索を頼んだ場合に「ちょっと調べてみよか！」を通知する。資料で説明できたときや、単なるJSON不正を理由には外部検索しない。
3. `google_search` で検索し、`fetch_url` で本文を取得する。検索結果の紹介文だけを読んだことにはしない。試験では `go.jp`、国立国語研究所、国立天文台のドメインに限定し、最終URLも確認する。個人塾や個人作成DBは対象外。
4. 裏で読む根拠と、子どもに表示する教材を区別する。表示先は [個別教材一覧](../dogido_server/language_dialogue/child_resources.json) の対象学年・質問対象・観点が合う確認済みページのみ。子ども用の本文もドギドに渡す。表示できたときは先回りして解説せず報告を待つ。裏の資料しか読めなかった場合は `reference_only` とし、「一人で読みやすいページはまだ見つけられてへん」と伝える。大人向け資料を代わりに表示しない。
5. 報告の意図を本文なしで抽出し、報告だった場合だけ本文を使って読み取りを返す。間違った説明を、本人が「分からない」と言ったことにしない。
6. 子どもがモヤっとすると表明したら先生への相談と冒険への復帰を提案する。肯定や明示的な復帰で通常モードへ戻す。「まだ考えたい」なら続ける。相槌だけで理解済みとは記録しない。
7. 通常モードへ戻るとWeb本文・読み取り・学習中の対話履歴を一時文脈から外す。戻り先に渡すのは `return_context.researched_topic` の直近の質問1件（最大160文字）のみ。取消・期限切れでも同じ。調べたあと別の問いへ移っても、その問いで新しい資料を取得するまでは前の調査話題を失わない。2026-09-12の本体接続では、この話題一件だけを本体の短期digestへ渡し、長期記憶へは保存しない。

現時点の自動表示先は国土地理院の小学3・4年向け「方位」「地図記号」の2ページ。小学2年向けとは確認していない。東京ベーシック・ドリルと国語研のこどもパンフレットは、ユーザーの指摘を受けて子どもへの表示候補から除外し、理由だけを `excluded_records` に残す。教師が子どもの教育に使う資料と、子ども本人が疑問を調べるために読む資料を区別し、名称の「こども」や対象学年だけで選ばない。文科省のリンク集には民間教材もあるため、掲載元だけでリンク先を一括採用しない。これらの公的資料があらゆる意味で中立と保証するのではなく、運営元・対象読者・対象学年・販売誘導を別々に確認する。

検索エンジンには対象語と短い検索語を送り、会話履歴・ゲーム状態・記憶一式を送らない。ただし検索語自体はローカルモデルの抽出結果であり、任意の固有名詞を完全に匿名化する機構ではない。試験入力は全て人為的な例文。

### 読めた証拠と、未確認のこと

- `WEB` ログは検索開始、検索失敗、ページ取得開始、本文取得を区別する。本文取得ではURL・文字数・ハッシュ・ページIDを残す。
- `turns.jsonl` の `web.pages` に実本文、`web.context_page_ids` に保持した本文ID、報告ターンの `context_page_ids` に実際に読解へ渡した本文IDを記録する。各ページの `use` は `background_reference`／`child_resource`。`child_status` は表示成功と未発見・取得失敗を区別する。試験ログの本文は検証用に残るが、通常会話へ読み戻さない。
- `research_reading` と `source_quotes` はモデルの読み取りと根拠引用。本文との引用一致は確認するが、意味の正しさを保証するものではない。出典は発言外の記録へ分ける。
- GoogleがCAPTCHAになったら同じ実行中の再検索を止める。既知URLは直接読める場合があるため、`search_status` と本文取得の `status` を別々に見る。
- PDFはこの経路では本文抽出せず、その理由を記録する。HTMLは根拠資料最大2ページと子ども用1ページ、各15,000文字まで。`coverage=extracted_text_only` とし、図・動画の内容を読めた扱いにしない。サイト全体や辞書全体を読んだわけではなく、取得した資料が問いを解決できるかは別の確認事項。
- 独立対話経路へのGoogle AI概要の受け渡しは2026-09-08に実装。Geminiアプリとの継続対話の開始・操作は未実装。子どもが別画面で何を読んだかは推測しない。
- 子どもが後から別のリンクへ進んだ際の追跡・読み直しも未実装。現在の「両方読める」は、この経路で取得した根拠資料と表示教材の本文を指す。
- 4件の擬音語等の試験では、まだ適した子ども用教材が登録されていないため初回は `reference_only`。後続の報告は外で学んできたことを想定して人為的に入力しており、児童に適切な教材を選べたという試験ではない。
- CLIの `/cancel` は対話と対話の間に読む。同期処理中に割り込む入力UIではない。処理中に終了する場合は `Ctrl+C`。呼出元からの `cancel()`／`interrupt()` は処理中でも結果を無効化し、Web待機も取消を監視して所有接続を終了する。GPU計算の強制停止とは別。

関連試験：

```bash
python -m pytest tests/test_language_dialogue.py tests/test_language_web_research.py tests/test_structured_contracts.py -q
python -m dogido_server.language_dialogue --web --known-sources-only --cases tests/fixtures/language_dialogue/web_routing_cases.json --output logs/language-dialogue/web-routing-01
```
