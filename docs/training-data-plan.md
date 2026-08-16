# ドギド学習データ準備

状態: **↑/↓一次振り分け・候補エクスポート・人間レビュー・承認済みgroup splitまで実装済み。学習ジョブとSTT音声収録は未実装。**

## 1. 目的と境界

既存の私的ログから、将来のLoRA／QLoRA・評価に使える候補を取り出す。ただし、ドギドの出力やruntime分類を「保存されているから正解」とはみなさない。

- 実データの出力先は `.dogido_training/`（Git除外）
- Gitへ残すのは評価・レビュー・再生成パイプラインのコードと本設計だけ。実データは残さない
- 候補は必ず人間が `approved / rejected / edited` を付ける
- 生の `session_id` は出力せず、安定ハッシュの `group_id` にする
- プレイヤー識別子・座標は評価snapshotの構造化フィールドへ保存しない。ただし発話本文に呼び名や個人情報が含まれうるため目視確認する
- 個人名・私生活の発話は自動匿名化できないため、クラウド投入前に目視する
- 句・revision・講評は同じ `group_id` のまま分割し、trainと評価へまたがらせない

## 2. 現在の初回エクスポート

`.dogido_memory/` から次の確認待ち候補を生成した。

| task | 候補数 | 現状 |
|---|---:|---|
| `conversation_reply` | 1,270 | 同一session/sequenceに1入力・1返答だけある組。品質未確認 |
| `workshop_feedback_classification` | 164 | 保存された講評kind。発話全体との整合は未確認 |
| `haiku_quality` | 94 | 句・材料・講評・revisionを同じ候補へ束ねたもの |
| `haiku_revision_preference` | 4 | プレイヤーが採用した局所revision。STT事故を含まないか要確認 |
| `response_quality_review` | 実機評価ごとに増加 | `↑`／`↓`で選んだ直前応答。押し直し後の最後のlabelだけを候補化 |
| `stt_transcription` | 0 | 元音声を保存していないため、音響学習には使えない |

この件数は「学習可能な正解数」ではなく、レビュー開始地点である。

## 3. ローカル構成

```text
.dogido_training/
  inbox/
    evaluation_flags.jsonl # ↑/↓の追記履歴
  manifest.json
  README.txt
  candidates/
    conversation_reply.jsonl
    workshop_feedback_classification.jsonl
    haiku_quality.jsonl
    haiku_revision_preference.jsonl
    response_quality_review.jsonl
    stt_transcription.jsonl
  reviews/
    annotations.jsonl       # 再エクスポートで上書きしない
  approved/
    all.jsonl               # approved / editedだけ
    manifest.json
  splits/
    train.jsonl
    validation.jsonl
    test.jsonl
  audio/                    # 将来、明示同意したSTT用音声だけ
```

再生成:

```bash
python scripts/export_training_dataset.py
```

候補ファイルと `manifest.json` は再生成するが、`reviews/annotations.jsonl` は保持する。

## 3.1 実機での一次振り分け

Minecraftのゲーム画面で、ドギドの直前応答を聞いた直後に押す。

| キー | label | 意味 |
|---|---|---|
| `↑` | `good_example` | 良い返答・良い句として後で確認したい |
| `↓` | `needs_review` | 誤答・不自然・要修正として後で確認したい |

- チャット、コマンド、看板、本、インベントリなど、画面が開いている間は評価しない
- 同じ応答で反対キーを押すと、前の行を削除せず新しい評価で上書きする
- エクスポーターは `target_id` ごとの最後の評価だけを候補へ出す
- 直前応答の有効時間は既定180秒。古い内容への誤評価を避ける
- 通常時は直前応答をRAMにだけ置き、キーを押さなければ `.dogido_training` へ書かない
- `↑`も自動承認ではない。誤操作、個人情報、学習taskとの適合を人が最終確認する

## 4. 候補とレビューの形式

候補はtaskが違っても共通の外枠を持つ。

```json
{
  "schema_version": "dogido-training-candidate-v1",
  "candidate_id": "conv_...",
  "task": "conversation_reply",
  "group_id": "session_...",
  "input": {},
  "candidate_output": {},
  "signals": {
    "quality_confirmed": false
  },
  "source": {},
  "review_requirements": []
}
```

人間の判断は候補を書き換えず、別JSONLへ追記する。

```json
{
  "candidate_id": "conv_...",
  "status": "approved",
  "corrected_output": null,
  "tags": ["natural", "minecraft_grounded"],
  "notes": null,
  "reviewed_at": "2026-08-16T12:00:00+09:00"
}
```

`edited` の場合だけ `corrected_output` を必須にする。同じ候補へ複数レビューがある場合は最新を正とする。

### レビュー操作

```bash
# 未レビュー候補を20件表示
python scripts/review_training_dataset.py list

# 候補の入力・出力・現在のreviewを確認
python scripts/review_training_dataset.py show human_eval_...

# そのまま承認／不採用
python scripts/review_training_dataset.py mark human_eval_... approved --tag natural
python scripts/review_training_dataset.py mark human_eval_... rejected --notes "STT誤変換"

# 正解を手で直して承認
python scripts/review_training_dataset.py mark conv_... edited \
  --corrected-output-json '{"assistant_text":"丸石やな。何を作るん？","layer":"speech"}'

# approved / editedだけを昇格し、group単位で80/10/10に安定分割
python scripts/review_training_dataset.py promote
```

`mark` は候補ファイルを変更せず `reviews/annotations.jsonl` へ追記する。`rejected` は履歴に残るが、`approved/all.jsonl` とsplitには入らない。

## 5. 最終的な学習形式

### 会話・workshopのSFT

レビュー後に一般的なmessages形式へ変換する。

```json
{
  "messages": [
    {"role": "system", "content": "task=conversation_reply"},
    {"role": "user", "content": "丸石を持ってる"},
    {"role": "assistant", "content": "丸石やな。何を作るん？"}
  ],
  "group_id": "session_..."
}
```

学習時のsystem全文をログへ複製せず、`task` から学習設定側で注入する。プロンプト変更で全データが古くなるのを避けるためである。

### 川柳の選好学習・評価

```json
{
  "prompt": {
    "base_text": "元の三行",
    "comment": "プレイヤーの指摘",
    "context": {}
  },
  "chosen": "採用された三行",
  "rejected": "元の三行",
  "group_id": "hk_..."
}
```

採用済みrevisionでも、滑舌・STT誤変換・意図しない了承がありうるため自動承認しない。

### STT

```json
{
  "audio_path": "audio/opaque-id.wav",
  "transcript": "下五を草地かなに変えて",
  "stt_raw": "下後を草地かなに変えて",
  "mode": "haiku_workshop",
  "noise_tags": ["minecraft_game_audio"],
  "consent": true,
  "group_id": "recording_session_..."
}
```

文字ログだけではSTTの音響学習はできない。収録する場合は、明示同意・保存ONのときだけ16kHz mono音声と確定文字列を対にし、常時録音にはしない。

## 6. 量の目安

以下は開始判断のための実務的な目安であり、モデルやデータの多様性で変わる。

| 目的 | 承認済みデータの目安 |
|---|---:|
| パイプライン動作確認 | taskごとに100〜300件 |
| 小規模LoRAのA/B比較 | 合計1,000〜3,000件＋独立評価200件以上 |
| 会話・workshopの安定した専門化 | 5,000〜10,000件を目標。少数classを各100件以上 |
| 川柳生成・修正 | 評価済み句500〜1,000句、採用revision 200組以上を最初の目標 |
| 一人・一環境向けSTT試験 | 確定音声5〜10時間程度から比較 |
| 複数ユーザー向けSTT | 話者・マイク・雑音を増やし、数十〜100時間以上を別途検討 |

現状は会話候補数だけなら試験圏内だが、未レビューであり、workshopの `off_context` など少数classとrevisionが不足している。35B-A3Bの本番LoRAをクラウドで回す前に、まず200〜500件をレビューして「小さな承認済み評価セット」を作る。

## 7. 分割と評価

- 個々の行をランダム分割せず、`group_id` 単位で `train / validation / test` を分ける
- 同じ句の元句・講評・revisionは必ず同じsplit
- 同じ会話sessionも同じsplit
- 最終10〜20%は学習に一度も見せない
- 現行の `promote` は `group_id` のハッシュで `train 80% / validation 10% / test 10%` に安定分割する。件数が少ない段階では各splitが空になることがある
- 新しい実機ログを時系列holdoutとして追加し、古い言い回しの暗記だけで合格しないようにする

主な評価項目:

- structured JSONのschema適合率
- intent・対象行・評価極性の正解率
- Minecraftにない事実の混入率
- workshopの保存／closeを誤誘導する分類率
- 日本語の自然さとドギドらしさ
- 川柳の5-7-5、読み、出典、プレイヤー評価
- STTは文字誤り率に加え、Minecraft固有語・上五／中七／下五の完全一致率

## 8. クラウドへ出す境界

クラウドへアップロードするのは `approved/` または確定した `splits/` と、学習に必要な最小コンテキストだけにする。`.dogido_memory/`、`inbox/`、未確認の `candidates/`、生のsession ID、不要な時刻、個人的な会話は送らない。35B-A3BのLoRA後はadapterまたは統合済み重みをMac用MLXへ変換し、runtimeでは単一モデルとしてchat／haiku両routeから共有する。
