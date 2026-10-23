# ハエの神経回路・身体研究と派生実装の調査

調査日: 2026-09-16（日本時間）

状態: 原著・研究チームの説明・公開コード／検証記録を読んだ調査。導入、訓練、実行、ドローン実機、Minecraft内の再現は未実施。実装方針の確定ではない。

目的は、ドギドに独立した3D浮遊身体を追加する際の候補を集めること。既存2D版との分離、身体位置での感覚、ゆっくりした寄り道と帰還、川柳中のその場停止／緩い追従という構想を評価軸にする。

## 1. 今回分かったこと

- 「本家」は一つの完成ソフトではない。**接続地図、神経活動モデル、感覚モデル、身体と運動制御**を別々の研究が提供している。
- 2024年の雌の脳だけのFlyWire、2026年の雄の脳＋腹側神経索のMaleCNS、雌の脳＋腹側神経索のBANCは別の標本・データ系統。
- Minecraft派生は少なくとも三系統。最初に見た `blendi-remade/fly-brain-minecraft` はコード公開済み。`evnsnclr/neurocraft-fly-public` は調査時点では紹介・動画・公開計画。さらにFlyWireの一部回路と可塑性を使うNeoForge版 `wavendis/ruby-project` がある。
- **学習を伴う本格研究は存在する**。FlyVisは視覚モデルのパラメータ、FlyGMは接続構造を使った運動制御を学習する。一方、固定LIF回路を動かす派生も多い。「ハエ脳を入れた」だけでは学習方式は分からない。
- 実際に動いても、神経配線による効果か、出力変換・身体制御による効果かは別問題。派生作者が否定的な検証結果を残している事例は、設計上とくに参考になる。

## 2. 原典・研究チーム

以下の「検証」は論文・作者が報告した範囲であり、当地での再現結果ではない。

| 研究・公開時期 | 提供するもの／学習 | ドギドへの意味・確認すべき境界 | 一次資料 |
|---|---|---|---|
| **FlyWire / Dorkenwald et al.、2024** | 雌成虫の脳の電顕接続地図。139,255個の校正済みニューロン。 | 接続データの原点。行動プログラムや、経験を蓄積する学習規則そのものではない。 | [Nature原著](https://www.nature.com/articles/s41586-024-07558-y)、[公式](https://flywire.ai/) |
| **Shiu et al.、Nature 2024** | 接続図と推定神経伝達物質を使うLIF発火モデル。特定ニューロンの刺激・抑制から発火時刻／率を計算。 | 摂食・苦味による抑制・毛繕いなどを調べる基準。自由飛行やMinecraft学習済みモデルではない。論文の特定回路予測の一致率を、全行動の再現率に読み替えない。 | [原著](https://www.nature.com/articles/s41586-024-07763-9)、[著者コード](https://github.com/philshiu/Drosophila_brain_model) |
| **MaleCNS / Berg et al.、Cell 2026-09-03** | 雄の脳・視葉・腹側神経索をつなぐ地図。正式論文は166,700ニューロン。v1.0公開は2026-06-08。 | 近年のMinecraft等の派生で広く使用。感覚から運動への経路を同一標本内で追える。 | [公式・公開履歴](https://male-cns.janelia.org/)、[原著](https://doi.org/10.1016/j.cell.2026.08.015)、[Google Researchの説明](https://research.google/blog/a-connectomics-milestone-mapping-the-complete-male-fruit-fly-brain/) |
| **BANC、Nature 2026-06-08** | 雌のBrain-And-Nerve-Cordの接続地図と解析。MaleCNSとは別標本。 | 脳だけでなく腹側神経索との分散した制御を見る参考。完成した移動モデルの配布ではない。 | [原著](https://www.nature.com/articles/s41586-026-10735-w)、[著者コード・データ入口](https://github.com/htem/BANC-project/) |
| **NeuroMechFly v2 / FlyGym、Nature Methods 2024** | MuJoCo身体、感覚、接触、運動制御を接続する実験環境。嗅覚・視覚を使う探索や他個体追従などを研究。 | 身体の位置で感覚を取り、行動で次の感覚が変わる構成が近い。全脳が標準搭載される意味ではない。FlyGymは2026年3月に2.xへ改訂され、旧APIと現行APIを区別する。 | [原著](https://www.nature.com/articles/s41592-024-02497-y)、[コード](https://github.com/NeLy-EPFL/flygym)、[現行導入案内](https://neuromechfly.org/installation/) |
| **flybody / Vaxenburg et al.、Nature 2025** | 別系統のMuJoCo身体。翼の空力や脚を含み、模倣・強化学習による歩行／飛行制御を提供。 | 滑らかに飛ぶ身体と、接続地図由来の脳を分けて考える参考。身体のMLP制御は、ハエの神経接続図そのものではない。 | [原著](https://www.nature.com/articles/s41586-025-09029-4)、[コード](https://github.com/TuragaLab/flybody) |
| **FlyVis / Lappalainen et al.、Nature 2024** | 接続構造を拘束に使い、視野の動き（optic flow）の課題で神経・シナプス特性を学ぶ視覚モデル。既存の学習済み重みあり。 | 実際に身体から見える画像を入力したい場合の参考。全脳や、羊・ゾンビの種名を理解するモデルではない。 | [原著](https://www.nature.com/articles/s41586-024-07939-3)、[コード](https://github.com/TuragaLab/flyvis) |
| **FlyGM / Jin et al.、2026、現行arXiv v3は06-14** | 接続図をグラフ構造に使った学習可能な制御器。flybodyの既存制御を模倣し、PPOで調整。 | 「この身体をどう動かすかを学ぶ」という構想に近い。ただし生物物理的なLIF再現とは別方式。査読済み掲載は今回未確認。 | [v3本文](https://arxiv.org/html/2602.17997v3)、[現行研究室ページ](https://lnsgroup.cc/research/FlyGM/)、[別の著者ページ](https://sites.google.com/view/flygm) |
| **FLYNN / Wang & Chen、2026、arXiv v2は07-13** | FlyWire配線由来のRNNに、模倣学習DAggerで二輪ロボットの視覚ナビゲーションを学習。MuJoCo上で合成配線・CNNと比較。 | **ハエ以外の身体へ学習で適応させる参考**。著者は未知の見た目や感覚欠損への頑健性を報告。仮想ロボットでの結果であり、実機・ドギドでの成功を示すものではない。査読済み掲載は未確認。 | [論文](https://arxiv.org/abs/2607.00025)、[著者コード](https://github.com/ben-gitdev/fly-gym) |

### Eonの位置づけ

Eonは既存の脳・視覚・身体モデルを統合する系統。2026-03-10の技術説明では、身体側の既存・学習済み制御と手作りの入出力対応を利用し、視覚入力が行動に十分効いていないこと、身体としての逃避は未実装であること、学習・内的状態の不足を明記している。これはその説明時点の実装境界で、現在まで一切変わっていないと断定するものではない。[Eon技術説明](https://eon.systems/updates/embodied-brain-emulation)

2026-09-11には最近の派生を紹介する公式記事もある。研究全体の出発点をEon一社に置かず、FlyWire／Shiu／NeuroMechFly等へ戻って読むのがよい。[Eonの派生紹介](https://eon.systems/updates/more-flies-are-getting-uploaded)

## 3. 派生実装の比較

| 実装 | 実際の接続・学習 | 公開状態／見落としやすい限界 | ドギドへの参考 |
|---|---|---|---|
| [blendi-remade / fly-brain-minecraft](https://github.com/blendi-remade/fly-brain-minecraft) | MaleCNS派生のLIF、感覚変換、神経集団の活動から動作へ。 | Fabric 1.21.1、Javaコードと検証記録あり。匂いへの接近・飛行物理・行動調停は補助制御。視覚の一部は検出済み特徴を中間ニューロンへ入力。 | **Minecraft移植の第一候補**。神経・感覚・身体・表示の分離を参考にする。 |
| [NeuroCraft Fly / evnsnclr](https://github.com/evnsnclr/neurocraft-fly-public) | MaleCNSと選択した神経出力で、スクリプト化した身体動作を調整。 | 紹介ページ・実演・公開予定。実行可能コード／Modは未配布。作者は学習readoutが単純な直接制御より悪かった過去結果も記載。 | 別プロジェクトとして追う。最初のMinecraft repoと混同しない。 |
| [dylankainth / flybrain](https://github.com/dylankainth/flybrain) | FlyWire由来モデルをDJI Telloへ。カメラ→視覚の動き→神経活動→RC指令。学習版と固定回路版の実験を区別。 | PC側計算。上下の補正・旋回制限・機体内蔵ホバリング等あり。こちらで飛行動画・実機性能を再検証していない。 | **前進・旋回等の少数出力へ変換する発想**。身体側の安定制御を分ける参考。 |
| [Xenova / Fruit Fly Simulation](https://huggingface.co/spaces/Xenova/fruit-fly-simulation) | ブラウザのWebGPU／JSで神経活動を計算し、手作りの身体アニメーションを駆動。 | 身体の運動が感覚として脳へ戻る閉ループはない。接触・筋肉・空力シミュレーションもないと明記。 | 回路刺激と表示の試験台。自然な飛行が完成している根拠には使わない。 |
| [AbijahKaj / fruit-fly-brain-research](https://github.com/AbijahKaj/fruit-fly-brain-research) | MaleCNSの視覚～飛行経路、約65.8k unitsのrate model。パラメータを学習しWebGPUで動作。 | 身体の眼から画像を生成する閉ループ。学習はRTX 5090を使用との作者記録。身体は空力を持たない簡略モデル。 | 身体視点・方向反応・接近物への回避を研究する候補。 |
| [ZeroXClem / closed-loop-fly](https://github.com/ZeroXClem/closed-loop-fly) | 上記の視覚モデルとXenovaの全体回路をMaleCNSのbody IDで接続。 | 後日の追試で、一部の改善が静かな出力ペアを固定旋回に変える読み取り上のアーティファクトだったと訂正。 | **回路を通った効果の切り分け方**が参考。完成飛行モデルとして即採用しない。 |
| [alextitonis / fly.ai](https://github.com/alextitonis/fly.ai) | 固定した回路を共通の処理基盤にし、課題ごとに入出力を接続。手作りdecoderと学習するlinear readoutがある。 | 作者は眼の入力が途中で消えること、背景活動が視覚反応に見えること、匂い回路の飽和などを報告。査読済み結果ではない。 | 感覚入力・神経活動・出力を分離して調べる教材。 |
| [DOOMFLY / nftechie](https://github.com/nftechie/doomfly) | 固定の神経→ボタン対応に、ドーパミン信号で一部の既存接続を変える実験的可塑性を追加。 | READMEはv6の視覚・条件づけ・生存検証が不合格と明記。重みが変わることと有効な学習は別。 | **経験で何を更新し、何をもって学習成功とするか**の参考。 |
| [Fly64 / ornata](https://github.com/ornata/fly) | MaleCNS活動をMarioの前進・旋回・ジャンプへ対応づける。身体視点カメラあり。 | 訓練・報酬・星を集める目標はない。作者はM2／16GBで動作を報告するが、コード未レビューと明記。 | 少数の移動出力、ゲームと脳の分離、入力再生の参考。ゲーム攻略学習の成功例ではない。 |
| [FlyDrones / SpikeCalls](https://github.com/SpikeCalls/FlyDrones) | カメラ→神経モデル→前進・旋回・上下指令。機体の安定化は内蔵制御。 | ブラウザ実演は**850個の手作り人工回路MiniFly**。本物のMaleCNSを読む経路は別。READMEは実機アダプターを作者がまだ飛行検証していないと明記。 | Xで見たドローンの別候補。ブラウザ実演を全脳での実機飛行と同一視しない。 |
| [CyberFly](https://github.com/PtPavloTkachenko/fly-brain-spectacles) | MacのMetalでMaleCNSを計算し、Snap Spectacles側が部屋・手・身体位置から感覚を作る。可塑性はoff。 | 神経出力から飛行指令へ変換。着地、空腹、羽ばたき等には人工的な補助制御。作者のM1 Max計測では1個体の脳50ms分が約41ms。当地では未測定。 | **身体と脳の分離、身体位置での匂い、接近物からの逃避、Mac計算**が構想に近い。AR機器を採用する提案ではなく接続設計の参考。 |
| [DesktopFly](https://github.com/DenisSergeevitch/desktop-fly) | Macデスクトップ上の3Dハエ。FlyWire回路とMaleCNSの1,045ニューロンの運動回路をモデル化した接続部で結ぶ。 | 全脳一式ではない。脚の閉ループ検証あり。飛行・睡眠・毛繕い・状態選択には規則とアニメーションが残る。 | カーソルへの逃避、動作切替の連続性、神経制御から演出制御へ切り替える実装の参考。 |
| [fly-fpv / joey-david](https://github.com/joey-david/fly-fpv) | MaleCNSの接続の有無を制約に、重みと符号は学習。模倣→PPOで仮想3D輪くぐり飛行を訓練。 | 生理的なハエ脳モデルではないと明記。神経状態の時間的持越しもない。羽ばたき身体の15制御出力。配線の優位性は今回検証していない。 | 小さな3D訓練空間と比較条件を作る参考。完成した汎用飛行知能とは扱わない。 |
| [Ruby Project](https://github.com/wavendis/ruby-project) | FlyWireの部分回路、5個の人工入力ニューロン、KC→MBONのドーパミン依存可塑性。 | NeoForge 1.21.1。コード・実験用囲いコマンドあり。報酬源・係数は手作り。学習成功の再現は今回未確認。 | **Minecraft内で経験による反応変化を試す第三の系統**。Fabric版への直接導入はできず設計比較用。 |

Minecraft版の詳細は [VALIDATION](https://github.com/blendi-remade/fly-brain-minecraft/blob/main/docs/VALIDATION.md)、[SensoryEncoders](https://github.com/blendi-remade/fly-brain-minecraft/blob/main/src/main/java/com/fruitfly/brain/SensoryEncoders.java)、[FlyBody](https://github.com/blendi-remade/fly-brain-minecraft/blob/main/src/main/java/com/fruitfly/entity/FlyBody.java)を参照。ドローンの補助制御は [実機接続コード](https://github.com/dylankainth/flybrain/blob/evolutionary-behavior/flybrain_tello_real_brain.py)、閉ループ版の訂正は [followups](https://github.com/ZeroXClem/closed-loop-fly/blob/main/docs/followups.md)、Xenovaの境界は [README](https://huggingface.co/spaces/Xenova/fruit-fly-simulation/blob/main/README.md)に記載されている。

CyberFlyの人工的な補助・学習off・匂い方向は [設計判断](https://github.com/PtPavloTkachenko/fly-brain-spectacles/blob/main/docs/DECISIONS.md)、DesktopFlyの実装範囲と動作切替は [評価記録](https://github.com/DenisSergeevitch/desktop-fly/blob/master/EVALUATION.md)に詳しい。後者は別標本どうしの接続がモデル上の対応であることを明示しており、実測された標本間シナプスとは扱っていない。

### Xで見たものの元投稿候補

Grokによる公開Web／X投稿の調査で得た候補。こちらでXページを再取得すると403となったため、投稿本文と動画は独立に再確認できていない。公開コードがあるものは上表で別途確認した。日付・投稿の強い宣伝文句を研究成果の根拠には使わない。

- **美少女アバター**: [@yakshawanの投稿候補](https://x.com/yakshawan/status/2098413419204669909)、[学習後の経過候補](https://x.com/yakshawan/status/2099037040000278990)、[続報候補](https://x.com/yakshawan/status/2099376636974240137)。公開実装を今回特定できず、動けない原因が骨格差・感覚接続・学習設定のどれかは不明。
- **ドローン**: [@SpikeCallsの投稿候補](https://x.com/SpikeCalls/status/2099635431566377010)。ユーザーが見たものとの一致は未確定。コードの確認では前述のMiniFly／MaleCNS／実機未検証の区別がある。
- **Beat Saber**: [@_lyraaaa_の投稿候補](https://x.com/_lyraaaa_/status/2097527368919470162)。追加学習に関する投稿の調査結果はあるが、公開コードと独立再現を確認できず、画像だけで反応して攻略できる証拠には数えない。

追加探索の索引として [awesome-fruit-fly](https://github.com/townie/awesome-fruit-fly) もある。本表の追加候補の発見に使用し、技術的な説明はリンク先の作者資料へ戻って確認した。

## 4. 「学習させる」を三つに分ける

| 方式 | 更新するもの | 研究上の問い |
|---|---|---|
| 固定回路＋手作りの感覚／移動対応 | 神経の接続重みは固定。入出力の係数などを人が調整 | 元の配線は、刺激の競合や逃避にどんな反応を返すか。 |
| 接続構造を制約にし、課題の訓練でパラメータを学習 | readout、encoder、神経特性、結合重みなど。方式により範囲が異なる | この配線を使うと、浮遊体の制御を学びやすくなるか。FlyGM／FLYNN／fly-fpv等はこの設計を考える参考。 |
| 経験に応じて接続を変える可塑性 | 対象の結合、eligibility trace、報酬との対応等 | プレイヤーのそばへ帰る、特定の刺激を避けるといった行動が経験で変わるか。DOOMFLYは成功済みではなく、検証方法の参考。 |

これは比較のための整理で、各方式が同じ神経モデルを使うという意味ではない。IDに反応を割り当てること、モデルの係数を人が合わせること、経験から自動で変化することを別々に記録する。

### ドギドの構想への含意（ここからは提案）

最初の実験では、Minecraftの種類IDを感覚を生成する入口に使い、神経回路へは身体位置での明るさ、匂い、接近刺激などを渡す。プレイヤーの上空の上昇気流は仮想物理として定義できる。ただし、その場で浮きやすいことから、好んで滞在・旋回・帰還することまで自動で成立するかは未検証。

手足のない身体なら、前進・旋回・上下の少数出力へ変換する案を試せる。既存のハエ筋肉・翼の学習を丸ごと再現する必要はない。一方で、その変換を作れば回路が有用になるとも限らない。同一の感覚列で回路あり／回路を遮断した条件を比べ、身体の軌道だけでなく、どの出力が変わったかも見る。

身体側の敵観測は、プレイヤーが敵を見たこととは区別する。嗅覚の起点を変える場合も、既存のプレイヤー基準の匂い情報へ無条件に混ぜない。川柳では前に集合させず、出来上がった場所で停止またはゆっくり追従するという構想を維持する。

## 5. 最初に比較する候補と実行条件

| 優先 | 候補 | 理由・実行条件 |
|---|---|---|
| 1 | blendi-remade版の回路＋身体分離 | 既にMinecraft向けの境界がある。Java／CPU。作者の測定でも刺激によって実時間に追いつかない場合がある。ドギドの1.21.11との差分対応と、ローカル負荷の実測が必要。 |
| 2 | Shiuの公式モデル | 小さな刺激実験の比較基準。Brian2／Python、Mac案内あり。論文の設定・データ版を固定して扱う。 |
| 3 | AbijahKaj／closed-loop-fly | 画像から身体の動きまで追いたい場合。学習とブラウザ推論の計算条件を分ける。Macでの再現・速度は未確認。 |
| 4 | NeuroMechFly／flybody | 別空間で身体や感覚を研究する必要が出たとき。現行FlyGymはMacではNVIDIA用Warpなしの導入案内がある。既存policyの利用と大規模な再訓練は別。 |
| 5 | FlyGM | 制御学習に進む際の設計参考。v3実験はA100 80GB使用。研究室ページはコードをComing soonと表示し、別ページには匿名レビューrepoの案内がある。実行可能性・ライセンスを確認するまでは導入候補として確定しない。 |

追加で見つかったCyberFlyは、独立した感覚・身体とMac計算の接続例として優先して読む価値がある。学習方向では、公開コードのあるFLYNN／fly-fpvをFlyGMと比較できる。いずれもREADME・論文・限定的な実装確認の段階で、ドギドへの採用決定ではない。

本調査で、ユーザーのMacでの速度やMinecraft／LLMとの同時稼働は測定していない。既存のサービスや環境へ依存を追加していない。

## 6. 数字と資料の読み方

- **ニューロン数、細胞型数、ニューロン対のedge数、シナプス数は別**。1組のニューロン間に複数シナプスがありうる。
- 会話で引用した176,422はblendi-remade版が掲げる数で、正式MaleCNS論文の166,700と一致しない。この差の原因は今回データから再集計していないため、推測で埋めない。
- 「no training」は脳の配線が固定という意味なのか、身体側・視覚側・readoutも未学習なのかを確認する。
- 「trained」は重み更新を実装しただけか、未学習条件より改善したか、未知の条件でも改善したかを区別する。
- FlyGMの研究室ページのunweighted説明とv3本文のweighted説明には差がある。v3の神経伝達物質の符号割当もShiu型の説明と一致しない箇所があるため、生物学的忠実度を同等と扱わず、版固定して方法を確認する。
- 派生の作者による検証、査読論文の検証、当地での再現は三つの異なる証拠。今回は最初の二つを読んだ段階。

## 7. 再利用条件と調査時点の版

以下はREADME／LICENSEとGitHubメタデータの確認。第三者素材・データはコードと別条件を持つ。GitHubのライセンス自動判定が空であることは、再利用許可があることを意味しない。

| repo | 調査時HEAD | コード等の表示 |
|---|---|---|
| blendi-remade/fly-brain-minecraft | `6cfa30175003ef25da68a237d5eda958f8047b82` | LICENSEにMITとデータCC BY 4.0を分記。GitHub自動判定はNOASSERTION。 |
| dylankainth/flybrain | `97e02fb7c8d19fd5267c3fa9194f8001e08fb613` | default branchはevolutionary-behavior。明示ライセンスは今回未確認。 |
| ZeroXClem/closed-loop-fly | `0cbaa96cbac72cccad1fa04692b5f4d4a4e64673` | MIT、データ・上流別。 |
| alextitonis/fly.ai | `373a8092c8c3ba9719a9b55f92a370252b88367e` | MIT、データ別。 |
| nftechie/doomfly | `71ecf53d78eaffaf1a57ed7b0ccf5d458abc9f33` | オリジナルコードMIT、第三者部分の別記あり。 |
| ornata/fly | `f2f4114e53eaa326e54129f27a5383f93c6957af` | 明示ライセンスは今回未確認。ROM資産は別途必要。 |
| AbijahKaj/fruit-fly-brain-research | `c28066a5b9eff03efdb002f6779980d29f70634c` | MIT、データCC BY 4.0。 |
| evnsnclr/neurocraft-fly-public | `d121466b3ba2f11498e6da498c74acb061bb00eb` | 現時点の公開文書MIT。将来配布ソフトの許可を先取りしない。 |
| SpikeCalls/FlyDrones | `6e273a9eec704319fe2468e1d26b09bf06084328` | MIT、データ等は別途確認。 |

Shiu／FlyVisはMIT、NeuroMechFly／flybodyはApache-2.0のコード公開。MaleCNSデータはCC BY。XenovaはSpaceメタデータとアプリ／データ／身体の各ライセンスが分かれるため、README末尾と同梱NOTICEを確認する。
