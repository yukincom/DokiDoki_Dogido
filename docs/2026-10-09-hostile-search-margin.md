# 敵の索敵境界に外側5ブロックの余白を追加

状態: 実装・自動検証・Modビルド済み。Fabric全112テスト、模擬通信16項目が成功。Minecraftの起動、導入先Modの差し替え、実機確認は行っていない。

## 観測と変更範囲

実ログでは、17:00:20の離脱安堵を17:00:23から再生し、17:00:26のクリーパー発見で中断していた。これは警告の正常な割り込みだが、安堵と警戒が短時間で続く。ログだけでは、クリーパーの距離や視界の変化が原因かは確定できない。

既存の終了確認はFabricの `combat_ended_quiet_ticks=100`。敵の視認・音・被弾が途切れた後、通常20tick/秒で約5秒待つ。待ち時間を追加する別経路は作らず、今回は通常敵の索敵境界に余白を設ける。

- 新しい敵の発見範囲は従来どおり。既定16ブロック、実装上は設定 `max_threat_distance` と `visible_threat_distance` の大きい方。
- 確認済みの同じ個体は、外側5ブロック（既定21ブロック）まで追跡する。別個体や未確認の敵を外周で新規発見扱いにしない。
- 既存 `ThreatObserver` の個体ID・視認確認台帳を使う。周辺の種類表に使う `NearbyComposition.RangeTracker` は種類単位なので、そのまま敵へ共有しない。
- 遮蔽時は既存の `visual_hold_ticks` の範囲だけ保持する。外周から消えた個体や実際の走査から消えた個体の位置は補完せず、再発見を内側からやり直す。
- 敵を保持している間は従来の戦闘継続処理が働く。見えなくなった後の約5秒の終了確認、近距離・複数敵の即時確認、危険警告の割り込みは維持する。
- 敵数の照会範囲 `hostile_scan_distance` と `hostiles_within_scan_ground` は従来の内側（既定16ブロック）のまま。外周では未確認個体を発見扱いにしないため、21ブロック全体の在否や総数を断定しない。保持中の個体は `visual_threats` に実距離つきで送る。
- 追跡resetは既存の切断・world切替・次元変更・大きな位置ジャンプへ従う。サーバーへ新しい経路や安全判断用フィールドを追加しない。

## テスト設計

### 合格条件

1. 確認済みの同じ敵が通常範囲の境界を行き来しても、追跡と戦闘継続が途切れず、境界の出入りを原因とする `combat_ended` と `aftermath` が出ない。
2. 外周・遮蔽保持の期限・観測の消失で追跡を正しく解除する。追跡していない個体へ保持状態を渡さない。
3. 敵の実距離と個体IDが送信から本体まで保たれ、近い危険・新しい敵の警告が余白のために遅れない。
4. 別の敵が本当に新しく現れた場合は、安堵中でも警告へ割り込める。今回の実ログと同じ「安堵の中断」がすべてなくなることは合格条件にしない。

### 1. Fabricの単体テスト

既存の [ThreatObserverTest](../adapter/minecraft-fabric/src/test/java/dogido/fabric/observation/mobs/ThreatObserverTest.java) を拡張する。時計は `AtomicLong` のtickを進め、実時間のsleepを使わない。敵A・Bは固定の別UUIDとし、同種でも別個体として扱う。距離は3次元の実距離を入力する。

基本条件は発見16、外周21、視認確認6tick、遮蔽保持16tick、終了待ち100tick。基本6ケースに加え、既定値・反復・再確認・設定変更等の7ケースを追加した。既存5ケースを含む `ThreatObserverTest` 全18ケースが成功。

| ID | 入力・手順 | 期待する結果 | 準備状況 |
|---|---|---|---|
| U1 新規発見 | 未確認のAを15.99・16.00・16.01にそれぞれ置く。近距離・複数敵条件は使わない | 16.00以内だけ、確認6tickが連続した時点で採用。16.01では時間が経っても採用しない | 実装・成功 |
| U2 境界の往復 | 内側で確認したAを15.9↔16.1で100tick超往復させ、その後21.00まで遠ざける | 毎観測で同じUUIDを保持。`combatActive` は継続し、終了候補が出ない | 実装・成功 |
| U3 離脱と再発見 | 確認済みAを21.00→21.01→20.00→16.00へ移す | 21.00まで保持。21.01で解除。20.00へ戻っただけでは復活せず、16.00で改めて視認確認する | 実装・成功 |
| U4 個体の取り違え | 確認済みAと同種の未確認Bを外周に置く。内側の未確認A＋外側の未確認Bも試す | Aの保持をBへ渡さない。外側の未確認Bを複数敵の即時確認へ数えない | 実装・成功 |
| U5 遮蔽と消失 | 外周のAで見通しが切れてから16・17tickを比較する。走査一覧からの消失、reset後も確認する | 既存の遮蔽保持は16tickまで。17tickで解除。走査から消えた個体やreset前の個体は補完しない | 実装・成功 |
| U6 確認の中断 | 新規Aの確認6tickが満了する前に、一覧から消す／範囲外へ出す。その後内側へ戻す | 中断前の時間を合算せず、連続した6tickを確認し直す | 実装・成功 |
| U7 警告の速さ | 近距離のA、近距離閾値の外だが背後警告条件を満たすA、内側で見えるA＋B | 既存の即時確認条件が同じ観測で成立する。保持のための追加待ちが入らない | 実装・成功 |
| U8 終了の時刻 | 最後にAを保持したtickをTとし、以後は敵音・被弾もなし。T+99／T+100を比較し、`endCombat`後も再照合する。別ケースでは期限直前に内側で新規敵を確認する | T+99では終了不可、T+100で終了可。終了後に繰り返さない。新しい敵が来ればその観測から終了待ちをやり直す | 実装・成功 |
| U9 設定への追従 | 設定を `(max,visible)=(24,16)` と `(16,24)` に替える | 両方とも発見24・保持29。16／21の固定値へ戻らない | 実装・成功 |

### 2. 送信とRust本体の接続テスト

既存の [EventJsonBuilderTest](../adapter/minecraft-fabric/src/test/java/dogido/fabric/protocol/EventJsonBuilderTest.java) と [check_combat_runtime.py](../dogido-rust/scripts/check_combat_runtime.py) を使う。LLM・TTS・再生は既存の模擬実装、HTTPは独立ポート、保存先は一時ディレクトリとする。常駐セッションや実記憶へ試験入力を送らない。

| ID | 条件 | 期待する結果 |
|---|---|---|
| C1 外周の送信 | Javaの保持結果から、距離20のAを `visual_threats` に入れてJSON化。Rustへ同じ観測を送る | UUIDと距離20を保持して受信する。内側敵数が0でも視認敵Aを無視せず、安堵へ移らない |
| C2 敵数の範囲 | 距離20のA、`hostile_scan_distance=16`、内側敵数0で「敵は何体？」を入力 | 回答の数・範囲は16ブロックの集計を使う。21ブロック全域が無人とは答えない。追跡中のAは消さない |
| C3 離脱と実死亡 | Aの外周離脱・静穏後に結果なしの `combat_ended` を送る。実死亡イベントのケースと分ける | 離脱は `disengaged`。消失から死亡・プレイヤー撃破を捏造しない。実死亡への既存の一度だけの反応は維持する |
| C4 安堵中の新規敵 | 模擬再生を長くして安堵が `started` になった後、別UUIDのクリーパーを内側で送る | 安堵が `cancelled` となり、新しい警告を開始する。近距離や導火の警告が古い安堵の完了を待たない |

C1〜C4は自動検証成功。C1ではJavaの実フィルタ・実serializerが出力したJSONをRustへ読み込ませ、時刻とsequenceだけ配送時の値へ更新した。UUIDを発話のscopeへ、距離を20ブロックのコード回答へ照合した。敵数の集計はJava fixtureで与えており、実worldでの集計確認は実機へ残す。Rustへ手書き観測を送るだけの検査では、Fabricからの接続確認を済ませた扱いにしない。既存のイベントgoldenは新機能に合わせて一括再生成せず、従来の入力に対する送信形式を守る回帰として残す。

### 3. 実Minecraftでの確認

単体テストへ手で渡した `ThreatObservation` だけでは、`ClientWorld` の走査箱が外周の個体を実際に拾うこと、見通し判定、イベント送信頻度を証明できない。更新Modを適用した確認用ワールドで次を確認する。通常のプレイワールドへ試験用の敵を生成しない。

| ID | 操作 | 合格条件 |
|---|---|---|
| M1 境界往復 | 見通しのある同じ敵Aを内側で発見後、15.9↔16.1を10秒以上往復し、20付近でも観測する | Aの実観測と送信が続き、境界移動を原因とする離脱安堵が出ない。通常の警告間隔による発話と「再発見」を区別する |
| M2 遮蔽・離脱 | Aの見通しを切るケースと、21より外へ離れるケースを分ける。その後20、16以内へ戻る | 保持期限・外周・再確認がU3/U5どおり。未視認の位置を作らない。敵音が続くケースでは終了を急がない |
| M3 安堵からの割り込み | Aが離れた後、安堵中に別の敵Bを内側で視認する | Bへの警告が優先される。安堵中断だけを不具合と判定せず、直前に本当にBが未観測だったかも記録する |
| M4 追跡の初期化 | 追跡中に切断・world切替・次元変更・大きな位置ジャンプをそれぞれ行う | 前の追跡が新しい観測へ残らず、外周だけからの再発見扱いが起きない |

実機では適用Modの版／SHA、設定、UUID、実距離、見通し、送信sequence、`combat_ended`、安堵・警告のturn IDと再生結果を対応づけて残す。通常ログだけで分からない観測は、試験時の観測採取が必要と記録する。採取できていない項目を推測で合格にしない。

### 実施結果

| 検査 | 結果 |
|---|---|
| `ThreatObserverTest` / `EventJsonBuilderTest` の限定実行 | 22テスト成功（18＋4） |
| Fabric `./gradlew --offline test build` | 19クラス・112テスト成功、失敗・エラー・skip 0。Modとsources jarを生成 |
| Rust `./cargo.sh build --locked --offline` | 最新ソースの模擬試験用debugバイナリを生成 |
| Rust `./cargo.sh build --release --locked --offline` | 通常起動用releaseを更新。ソース更新による起動前チェックの停止を解消 |
| 通常設定で本体 `--check`・音声入力 `--voice --check` | 両方exit 0。録音・通信・モデル生成・サーバー起動なしで、必要ファイルと設定を確認 |
| `check_combat_runtime.py --adapter-wire-fixture ...` | 新規4＋既存12の16項目成功。LLM・TTS・再生は模擬。試験所有プロセスの終了も確認 |

再実行時は、まず `adapter/minecraft-fabric` で `./gradlew --offline test build` を行う。その出力 `build/test-results/hostile-margin-wire.json` を、リポジトリ直下から次へ渡す。

```bash
dogido-llm/bin/python dogido-rust/scripts/check_combat_runtime.py \
  --adapter-wire-fixture adapter/minecraft-fabric/build/test-results/hostile-margin-wire.json
```

模擬試験用のRustバイナリは `./dogido-rust/cargo.sh build --locked --offline` で先に更新する。実ゲーム・実音声を起動する手順ではない。検証結果は `dogido-rust/reports/combat-runtime-check.json`、Java側は `adapter/minecraft-fabric/build/test-results/test/` に出力する。

### 実機を起動する前の準備

通常起動はreleaseバイナリを使うため、debugの模擬試験だけでは準備完了としない。Rustソースや埋込資料を変更した後は `./dogido-rust/cargo.sh build --release --locked --offline` を行い、リポジトリ直下から次の二つを確認する。`scripts/start_dogido.command` は追加引数を渡さないため、事前確認は共通launcherへ直接 `--check` を指定する。

```bash
DOGIDO_ENV_PROFILE=standalone dogido-llm/bin/python dogido-rust/scripts/launch_dialogue.py --settings-dir . --check
DOGIDO_ENV_PROFILE=standalone dogido-llm/bin/python dogido-rust/scripts/launch_dialogue.py --settings-dir . --voice --check
```

今回、両方の事前確認が成功した。廃止済みの `DOGIDO_CUE_BACKEND` 設定警告は残るが、この警告で停止していない。実サーバー・録音・Minecraftの起動確認とは区別する。

- 確認に使うModは `adapter/minecraft-fabric/build/libs/dogido-fabric-client-0.1.0.jar`。SHA-256は `f9142cc7ae2ca6ffb5806d3f1aed8b3835e3c448bdfb034b1161f84c62c66b63`。同じ版番号の古いjarと取り違えない。
- 導入済みModは読み取りだけで確認した。旧SHA-256は `2ec3830399c6cd3e41ce55cfd56a319df170000c8ceb8973ec4d1461e8290fae`。ゲームのJavaプロセスが存在したため、Modを差し替えていない。導入・起動はユーザーが実機確認へ進む時点で行う。
- 現在の設定ファイルでは通常範囲16、終了待ち100tick、snapshot間隔20tickを確認した。視認確認・遮蔽保持は省略時の既定値6／16tickを使う。試験時には実際に読み込まれた設定と適用ModのSHAを記録する。
- 確認用ワールドはゲームを開いてから用意する。既存ワールドの変更や試験用敵の生成はまだ行っていない。M1/M2は同じ敵Aを見失わない条件で行い、移動距離だけで敵との距離を推定せず、記録された実距離を使う。
- M1の実操作は、内側でAを確認→15.5〜16.5付近を往復→20付近で維持→21.5より外へ離脱→20へ戻る→16以内へ戻る順とする。16／21ちょうどの厳密な境界は自動テストで検査済み。
- M1/M2→M3→M4の順で行う。各ケースの開始・終了時刻とsession IDを残す。Minecraftを閉じるM4は最後に回し、起動し直しを何度も要求しない。

### 実機で採取する記録

新しい記録経路は追加せず、次の既存出力を使う。

| 記録 | 確認する内容 |
|---|---|
| 起動時に表示される記憶保存先の `eval/episodes.jsonl` | `session_id`、`trigger.sequence`、`trigger.observed_at`、イベント名、`observation.visual_threats.items` の個体ID・距離、敵音、敵数、戦闘状態、選ばれた発話 |
| 本体の `/api/v1/rust-dialogue/snapshot` | 安堵・警告のturn ID、本文、`started / cancelled / completed`。開始前と各ケースの直後に採取する |
| ゲーム画面と操作メモ | 遮蔽物を入れた時点、画面上の視認、切断・次元移動等の操作。評価ログには直接の見通し真偽がないため、記録済みとは扱わない |

評価ログは判断時点の記録であり、実再生成功を表さない。再生結果は発話一覧と時刻・sessionを合わせて確認する。必要な記録が増えていない場合は試験を始めず、採取設定を先に整える。合否はケースIDごとに成功・失敗・未実施として残し、自動テスト成功を実機成功へ置き換えない。

今回のソース差分、変更前ファイル、旧Mod、新Mod、Java試験結果、接続fixture、模擬通信結果は、リポジトリ内のGit管理外 `.dogido_tools/checkpoints/20261009-hostile-search-margin/` へ保存する。
