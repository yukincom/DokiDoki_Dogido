# 川柳前の内部表現の調査とJ-lens実測

2026-10-16調査開始、2026-10-17実測追記。状態は**同じQAT重みでの独立生成・隠れ状態採取・公開J-lensの転用読出しまで**。感情の同定、同一重みでのレンズ学習、川柳への感情反映は未実施。

ユーザーが観測したいものは、モデル内部の数値（隠れ状態・活性）。生成された説明文やJSONを、その代わりにはしない。差が確認できた後に、キャラクターとしての「ドギドの感情」を川柳へ反映できるか検討する。

調査開始時のmain HEADは `b3aca1fafdc016cc0819034255e2779ecc8c18e4`。身体・観測・分類器・本棚等の既存変更がある作業ツリーを読んだ。本体コード、プロンプト、モデル、常駐サービスはこの調査では変更していない。

## 1. 現行経路と、今回観測したい場所

```text
GameEvent + 発句用の観測・カタログ材料
  → haiku_irony のモデル呼出し                ← 主な観測対象A
  → descriptionを前置きテキストへ整形・配送
  → haiku_scene の別のモデル呼出し
  → 出典atomへ結び直し、詩的解釈と材料を構成
  → haiku_draft の別のモデル呼出し            ← 主な観測対象B
  → 行ごとの検査・必要な再生成
  → 合格した句の保存・表示・読み上げ
```

- 入口と配送順は `dogido-rust/src/dialogue/haiku_runtime.rs` の `try_start_haiku` / `haiku_pipeline`。
- 材料の固定は `dogido-rust/src/haiku/preparation.rs` の `capture`、発句用の観測投影は `haiku/materials.rs` の `capture`。通常会話の `conversation_observation::Context` 全体をそのまま川柳へ渡す構造ではない。
- 前置きは `Preparation::inspiration` と `compose_inspiration_speech`。`irony.description` をほぼそのまま使い、必要なら「なんか浮かんできたわ。」を補う。
- `haiku_irony` は `found / kind / description / elements / focus / confidence` を生成する。このJSONもモデルの**出力**であり、隠れ状態ではない。confidenceも実測された感情強度ではない。
- `Preparation::materials` / `context::scene_for_spoken_irony` は、前置きの内容を一次atomへ結び直して `preface_clause` / `poetic_interpretation` を作る。`haiku_prompt/mod.rs` は説明文に加え、発話しない `focus` も後続プロンプトへ渡す。
- 各呼出しは `python_worker.rs::LiveBackend::generate` から独立した要求として送る。前の呼出しの隠れ状態を、次の呼出しへ転送する実装はない。渡しているものは明示的に組み立てた材料とテキスト。

**既存の類似経路:** 見どころ・焦点・詩的解釈を句へ渡す経路はすでにある。新しい感情分類器や別の内省文生成を追加する前に、この既存経路と測定した活性の関係を調べる。

## 2. 既存実ログから言えること

保存済み実モデルの例（JST 2026-10-13 00:02、`haiku_ca7756f151b14e21ac565051c0e93695`）:

| 段階 | 記録された内容 |
|---|---|
| `description` | 手元の黒い石の冷たさと、目の前のブタのピンク色の温かさが、なんか対照的で浮かんできたわ。 |
| 発話しない `focus` | 色、温度感 |
| 前置きテキスト | 上のdescriptionと同一 |
| 最終候補・保存句 | くろいいし / ぴんくのぬくもり / はるのなか |

根拠は `.dogido_tools/server-manager/logs/server.log` の31855行以降、および `.dogido_memory/rust-migration/sessions/ses_13209a27c29a/long_term/haiku_entries.jsonl` の2行目。返答モデル欄は `default_model` で、この文字列だけから当時の重みを特定しない。

前置きから句への表現変化は確認できる。ただし、温度感は実測温度ではなく生成された解釈で、「春」もこの例の出典では温帯バイオームに対応づけられている。検査のacceptedは、世界での事実や隠れた感情の証明ではない。scene応答には `finish_reason=length` もあり、途中出力と最終採用材料を区別する必要がある。

このジョブの前置き・句の配送状態は `audio_disabled`。実音声で聞こえたことは確認していない。既存ログは隠れ状態を保存しておらず、この例から「内側で別の感情を持っていた」とは判定できない。

## 3. J-spaceで見る位置の提案

J-lensは中間活性を語彙に対応する方向から読む方法で、最終層付近の次語予測と、中間層で保持される概念を比較できる。一方、読める情報は不完全で、単語の集合から誰の状態か・関係性まで自動確定できるわけではない。層の境界もモデルごとに測る。[原論文](https://www.transformer-circuits.pub/2026/workspace/index.html)

以下をドギド向けの測定位置とした。1〜3の実測は第6節に記載し、4の行ごとの比較は未実施。

| 優先 | 位置 | 比較するもの |
|---|---|---|
| 1 | 見どころ要求を読み終え、最初の応答tokenを出す直前 | 発話内容に条件づけられる前の活性と、後で実際に出たdescription |
| 2 | 見どころJSONのdescription開始直前と、その文字列の生成中 | JSON整形の表現、直前に自分で生成した文、内容に関係する表現を区別 |
| 3 | 川柳要求を読み終え、句本文の最初のtokenを出す直前 | 前置き側で読めた概念が、後続の句生成側でも現れるか |
| 4 | 上五・中七・下五を生成する直前 | どの表現が句へ使われ、どこで薄れたり別の表現に変わるか |

まず残差ストリームを複数の層で採取し、中間から後半の層を広く比較する。特定の層を最初から「感情層」と決めない。初期層と最終層も対照に残す。J-lensの順位・スコア、通常の次語logits、実際の生成token、元の活性を同じ位置へ対応づける。

特に、この生成は自由文ではなくJSONなので、応答開始だけでは括弧やキー名の処理が強く出る可能性がある。`description` の中身に入る位置も必ず見る。ただし、その時点では先行する生成tokenの影響を受けているため、応答開始前と混同しない。

## 4. 差を感情として扱う前の比較

最初の対象は、普段の既存プロンプトと実際の観測材料にする。「怖がれ」「内心だけ喜べ」等を追加して差を作らない。新規・変更プロンプトの試験は、文面を提示してユーザーのレビューを受けた後に行う。

- 入力に書かれた語の反映、実際に出す語の準備、どちらにも直接出ない表現を分ける。人格文の「怖がり」等の影響も確認する。
- 日本語、関西弁、英語に分かれて現れるtokenを原形のまま保存する。英語の感情語だけを探索して結論を出さない。
- J-lensで感情関連語が見えても、ドギド・プレイヤー・観測した対象のどれに関係するかは別に調べる。既存の感情表現研究も、同じ方向が異なる登場人物の感情を表す場合を報告している。[感情表現の研究](https://www.anthropic.com/research/emotion-concepts-function)
- まず場面をまたいだ再現性と発話・句との対応を見る。方向の抑制・注入などの因果試験は別段階にする。読み出しだけで川柳への因果的な影響を断定しない。
- 仮に川柳側へ何かを渡す場合も、観測事実・hard制約とは分離する。Rustによる検査・保存・取消の所有権は変えない。採取していない感情をラベルで補わない。

## 5. 計測手段の境界

ドギドの `GeneratedText` は本文、終了理由、token数だけを保持する。`reasoning_content` が返っても生成テキストであり、活性ベクトルではない。現在のクライアントにJ-space採取経路はない。

調査時には8080番のPythonプロセス（PID 9601）のLISTENを確認し、5055/5057番のLISTENは確認できなかった。その後、実行引数と該当起動ログを突合した。今回の対象は `lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit`、snapshot `aa5e2e50376f54254d503276ca2222ea8569b95e`。古い設定にあるQwen名やAPIの `default_model` だけでは判定していない。サーバーは要求でモデルを切り替え得るため、この確認を将来の稼働モデルの固定保証にはしない。

[公式参照実装](https://github.com/anthropics/jacobian-lens)は、モデル固有のJacobianを事前計算し、Hugging Faceモデルの活性へ適用する。事前計算にはbackwardが必要で、通常のChat Completionsリクエストの追加項目だけでは代用できない。導入時はこの参照実装を先に確認する。

[第三者公開レンズ](https://huggingface.co/solarkyle/jspace-lenses)にはGemma 4 26B-A4B用があるが、配布元の対象は `google/gemma-4-26B-A4B-it`。実際に使う重み・revision・量子化・tokenizer・層の定義との一致と適用精度は別途確認する。公開済みというだけで、使用先のMLXモデルに検証済みとは扱わない。

常駐サービスの停止・再起動・モデル変更や、観測のための別モデルへの置換は行わない。同じ重みでの独立再実行が可能でも、それは過去の実行時の活性そのものではない。実リクエスト・token列・生成設定・モデルrevisionを固定した再実行として記録する。

## 6. 2026-10-17の実測

### 条件と原データ

常駐プロセスを変更せず、同じローカルsnapshotを別プロセスへロードした。モデルは30層、残差幅2816、各MoE層128 expertから8個を選ぶ。テキスト要求と同じ `mlx_lm` 経路を使用し、別実装の `mlx_vlm` のcapture APIへ置き換えていない。PLEと層間KV共有はいずれも0。Macは64GiBで、独立実測のMLX最大使用量は約15.42GiBだった。

入力は既存 `haiku-preparation.jsonl` の0始まり17番と149番。**既存の合成場面の再実行であり、実プレイのその瞬間の活性ではない**。fixtureの `whole_poem` 設定、既定192token、seed 101を使用した。本体の `Preparation`、`haiku_prompt::messages`、`haiku_response::parse` と初回draft要求を再利用し、指示文は追加・変更していない。両場面のsceneは192tokenで途中終了し、本体の既存パーサーと材料構築を通した。パーサーが内側の一節を返しても、完全なscene契約の成立とは区別する。

採取器は `dev_tools/haiku_internal_probe/`、本体への読み取り用入口は `dogido-rust/examples/haiku_probe_requests.rs`。既存MLX Activation Labは `model.model` 前提の手動forwardなので、このGemmaの入れ子構造、tuple戻り値、embed scale、層スケールをそのまま処理できず、既存計算を呼ぶ独立hookを使った。

原データは `.dogido_tools/experiments/2026-10-16-haiku-jspace/` に保存した。開始日のフォルダ名を維持している。

- `validation/validation.json`: 通常計算・採取・VJPの小規模検査
- `run-101/`、`apple-101/`: 全要求、token列、実生成文、未加工活性、expert選択・重み・8位と9位のscore差、全層読出し
- `readouts.json`: ①応答開始、②description開始・途中・末尾、③川柳応答開始・本文開始の計6位置
- `identity.json`: 実際の全重みファイル、config、tokenizer、読出し行列のSHA-256と実行ライブラリ
- `reference/checkpoint-binding.json`: 今回の重みとレンズの明示的な組合せ

最初の`run-101`は採取後にidentity記録を拡充したため、採取時点のスクリプトhashが一部ない。元のmanifestと追記の来歴を保持しており、後から得たhashを採取時点の証拠にはしない。`apple-101`は各生成段階にも採取スクリプトhashを記録した。

### 文章と読出しの比較

下表の層番号は1始まり（コードindex 24 = 25層）。語は転用J-lensの実際の上位tokenから抜粋したもので、翻訳した感情ラベルではない。全上位語は `readouts.json` に残している。

| 場面 | 実生成の前置き | 実生成の川柳案（採否未検証） |
|---|---|---|
| ダイヤ・石・原木 | キラキラしたダイヤと、地味な石や原木が並んでるのが、なんか不思議な感じやな、なんか浮かんできたわ。 | ひかりさす / つちときのみの / ふしぎかな |
| リンゴ・石・晴れた空 | 青空の下でリンゴをかじったら、なんだか石の硬さまで思い出してしもたわ、なんか浮かんできたわ。 | あおぞらの / あまみのかじり / いしのかた |

前置き欄はモデルのdescriptionへ既存コードが末尾を補った`spoken_text`。配送・音声再生はしていない。

| 観測位置 | ダイヤ側 | リンゴ側 | 言える範囲 |
|---|---|---|---|
| ① 前置きの応答開始直前 | 25層にJSON関連token | 同じくJSON関連token | 内容より出力形式が強い |
| ② description本文開始直前 | 25層に `sparkly`、`glittering` | 25層に `Sunshine`、`sunshine`、`sunny` | 場面と対応する語の違いがある |
| ③ 川柳本文開始直前 | 25層に `sparkly`、`glittering`、`jewellery`。29層では `💎`、`diamond` | 25層に `sunshine`、`sunny`、`Sunshine`。29層では `あ`、`晴`、`blue` | 情景に関係する語と、直後に出す文字の準備が混ざる |

`splendour`、`marvellous` は両場面で高く、15層前後では `renderCamera`、`moustache` 等も両方へ繰り返し出た。したがって、関連しそうな英単語だけを選び「感動」「喜び」と名付ける読み方は採らない。また、リンゴを実際にかじったという行動は入力の実観測にない。生成された前置きと観測事実も別物として扱う。

### 採取位置とMoEの検査

1. native DecoderLayerの**MoE統合・残差加算・layer_scalar後、final norm前**を採取した。短い既存入力では全30層が有限値で、hookの有無による全logits差は0、最終残差へnative normと出力層を一回適用した差も0。
2. 実生成の各選択位置を、hookなし・同じKV cache手順・同じ確定token列で再計算した。2場面×6位置で、保存した上位5候補のlogit差は0、保存した最上位tokenは再計算の最大値と同点だった。argmaxの同点順は意味の差にしない。これは上位候補の照合であり、全tokenのlogits保存・全一致検査とは区別する。
3. cacheなしの全文再計算では最大差0.125〜1.5が出た。BF16の計算形状・cache経路の違いを、採取位置のずれと混同しない。原データは生成時の活性を正とし、全文再計算へ差し替えていない。
4. 未変更の量子化MoEのVJPは `[GatherQMM::vjp] cannot compute the gradient wrt the indices.` で失敗した。expertの**離散選択indexだけ**をstop-gradientにすると、通常forwardの差は0のまま入力VJPが通った。選択されたexpertと混合重みの勾配は残した。未選択expertへ切り替わる効果をこの微分が含むとは言わない。
5. 15・25・29層から末尾までの再計算は、batch=1を保つとnative最終残差との差0。4方向のVJPが有限で、30層の恒等対照は相対誤差0。batchを4へ増やした試行にはBF16由来の差があったため、最終診断は1方向ずつ行った。

### レンズの適合性は未確定

公開ファイルは100個のWikiText promptでfitされた `google/gemma-4-26B-A4B-it` 向け。29個の2816×2816行列を含み、SHA-256は `2fbc8d2d11bf834557ec737d1772f6aec37adc00d329945850315005aee29e72`。ローカルのQAT版でfitし直したものではない。配布元が量子化への転用を検証した例は12B/31BのNF4であり、今回の26B QAT MLXを直接保証しない。[公開レンズ](https://huggingface.co/solarkyle/jspace-lenses)

公式推定量に合わせ、先頭128tokenのうち16番から末尾の一つ前までのtargetを総和し、同じsource位置でVJPを平均した。各層4方向で、公開行列による予測とのcosineは約0.42〜0.68、相対L2誤差は約0.85〜1.76。恒等写像より方向は合うが、精密な一致とは言えない。2場面ともこの先頭部分はほぼ同じ人格指示なので、独立した2種類の内容検証にも数えない。

この数値診断は、形・向き・演算の確認を前進させる一方、量子化差、校正コーパス差、各promptのJacobianのばらつきを切り分けない。有限差分による微分精度の確認、同一QAT重みでのfit、独立データでの語彙readout検証は残る。今回の読出しを精度検証済みの感情計測へ昇格させない。

厳密には今回は **J-lensの語彙投影**。原論文のJ-space成分を求める疎な非負分解や介入・因果検証は行っていない。[原論文の定義](https://www.transformer-circuits.pub/2026/workspace/index.html)／[公式推定量](https://github.com/anthropics/jacobian-lens/blob/main/jlens/fitting.py)

## 7. モデル変更時の扱い

- 同じ層数・幅でも、モデル名・重みrevision・量子化・fine-tune/adapterが違えば別対象。tokenizer/chat template、native実装、MLXバージョン、hook境界、target層、レンズbytesも一緒に固定する。
- 採取済みデータの再開はidentity一致が条件。今回の重みと公開レンズの組合せも明示bindingで照合し、同じ2816×30だから別モデルへ適用できる、という扱いにしない。
- binding検査は一致した組合せを受理し、同形状の別重み、tokenizer、独立したchat template、generation config、レンズbytes、target層、実行ライブラリ、記録対象のnative実装・採取器の変更を拒否することを確認した。native計算・ロード・sampler・cacheと採取器のソースhashも照合する。
- この追加検査は2件の実測後に拡充した。旧bindingと各runのidentityは保存し、過去の記録を採取時から完全だったように書き換えない。旧identityでの再開は現行採取器が拒否する。再測定は新しいrunへ保存する。
- 新しいモデルはnative forwardの境界を調べ、hook有無、最終readout、cache位置対応を再検査する。MoEではrouterとexpert統合後を区別する。PLE・層間KV共有・SSM/GDN等がある場合、残差だけのsuffix再計算を流用しない。
- レンズは同一checkpointでのfitを基本とし、別量子化から転用する場合は明示的な実験として分離する。校正と評価データを分け、日本語・JSON・今回の発句promptへの適合性も確認する。
- 感情のラベルや強度は、単語が上位に出たことから作らない。入力語の保持、直後に出す語の準備、人物の帰属、場面をまたぐ再現性を分けて評価した後で判断する。

## 8. 現時点の判断

**MoEの隠れ状態は実際に採れた。情景と対応する語の変化も一部観測できた。ただし、ドギド自身の感情を同定したとは言えない。**

本体へ感情経路を追加する根拠はまだない。既存のdescription/focusから川柳へ渡る経路との比較を続け、まずこのQAT checkpointに対するレンズの適合性を確かめる。今回の川柳は初回draftで、音数・出典・自然さの採否検証、記憶保存、TTS、Minecraft表示は行っていない。

確認済みは独立GPU実行2件、native採取照合、同じcache手順での12位置照合、VJPの実行と恒等対照、binding一致と8種類の変更拒否、Rust計測入口のbuild、Python構文確認。通常本体の挙動変更はなく、本体全体の回帰テストや実プレイ試験はこの測定の証拠に含めない。8080番の常駐PID 9601は測定後も同じままLISTENしている。
