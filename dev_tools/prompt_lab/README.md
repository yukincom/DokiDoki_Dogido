# Dogido Prompt Lab

Minecraftからの遭遇に対する本体の反応を確かめる場合は、`production_events.py` を使う。プレイヤー発話は送らず、Fabric形式のイベントを既存Rust本体へ渡す。本体の発話選択、プロンプト構築、生成結果の採否、固定文へのfallbackを通す。起動済み8080のモデルを使い、モデルの起動・切替・再起動はしない。

## 本番経路の確認

リポジトリルートで実行する。既存の `dogido-llm` とreleaseビルドを使用する。

```sh
# 模擬応答でイベント受理・経路・現在の埋め込みプロンプト一致を確認
./dogido-llm/bin/python dev_tools/prompt_lab/production_events.py --root . --output dev_tools/prompt_lab/production_runs/preflight-01

# アプリ起動済み8080で生成し、CPU・RSS・速度も記録
./dogido-llm/bin/python dev_tools/prompt_lab/production_events.py --root . --output dev_tools/prompt_lab/production_runs/live-01 --live
```

出力先には未作成のディレクトリを指定する。既存結果は上書きしない。設定は本体の `launch_dialogue.py` から解決し、生成上限・タイムアウト・プロンプトは変更しない。埋め込みプロンプトと現行ソースが一致しない場合はモデルへ転送する前に停止する。

各ケースは新規セッション。晴れた昼の平原、右3ブロックの1体という合成シナリオを使用する。実Minecraftの採取ログではない。分類・注意理由はFabricの実装に合わせ、歩行・視線などの独自説明やカタログ全文を追加しない。敵の固定警告や無言も本体の結果として扱い、LLM呼出を強制しない。

試験用の変更は一時ポート・分離した保存先・音声無効・要求の記録転送に限る。APIのuser roleには本体が作った観測JSONが入るが、これはプレイヤーの発言ではない。音声無効時に本体が記録する `playback_status=failed / error=audio_disabled` は、生成文の採否とは分けて読む。マイク・実ゲーム操作・実再生の検証にはならない。

`event-*.json` は入力イベント、`request-*.json` と `response-*.json` は実際のHTTP要求・応答、`results.json` は本体の結果。`model-calls.json` は各生成の時間・CPU・RSS・token数を含む。`manifest.json` にはバイナリ・関連ソースのhashと8080プロセスの識別情報を保存する。認証キーは保存しない。

CPUは累積CPU時間差を経過時間で割り、100%を1コアとする。RSSは約200ms間隔のプロセス常駐量で、Metalなどを含む全メモリ量ではない。必要なら試験後にmacOS標準 `footprint -p <8080のPID> --noCategories -f bytes --swapped -j <保存先>` を別途記録する。physical footprintはプロセス全体の一時点、peakはプロセス起動以来のピークなので、今回の生成中ピークとは呼ばない。

速度はcompletion token数を要求全体の時間で割るため入力処理も含む。非streamの本体設定を維持しており、TTFT・純粋なdecode速度・GPU使用率は未測定。キャッシュを維持した連続実行なので、cold時との比較には使わない。モデル単独起動の過去試験は8080経由と条件が異なる。

## 旧プロンプト単体試験

`start.command`、UI、従来の `python -m dev_tools.prompt_lab` は人工的に組んだplayer_chat入力の比較用。Minecraft遭遇の本番再現には使わない。過去の送信内容と結果は履歴として保持している。入力本文には現行Rustの `build-chat-prompt` を使い、比較候補のsystem指示だけを差し替えるため、利用前に現在のreleaseビルドを用意する。

旧 `mobs15-entity-context` は試験側がカタログ全文・個体の移動などを独自に追加したケースで、本体の実際の投影ではない。移動の注入はユーザーの指示を取り違えた試験設計によるもので、ユーザーが指定した前提ではない。旧 `normal-observed-presence` も本体へ未適用の試験候補。現行本体の正本と混同しない。

旧試験の `runs/`、本番経路の `production_runs/`、相談メモはGit対象外。プロンプト候補と試験結果を本体へ自動で書き戻さない。


## 本体への反映

候補と保存済み結果は比較資料として保持し、本体へ自動反映しない。採用する変更だけを共有プロンプト・状況文またはRust側の対話データへ明示的に反映し、対応するfixture・テストとreleaseビルドを更新する。旧Pythonの生成器・比較oracleは退役済みなので、再導入して同期しない。`canonical` は現在採用中の共有プロンプトを読む比較対象であり、旧候補の成績を現行本体の結果へ読み替えない。
