# 画像取得と変化の区別（パート5）

2026-10-29。[採用計画](research/2026-10-29-image-change-handoff/PLAN.md)に沿う編集。実行・コンパイル・golden照合・モデル応答・実機は未確認。編集段階で止め、試験担当へ引き継ぐ。

## 計測と既存経路

`BodyViewChange` の旧32×18判定を、原画像のRGBを64×48へBOX縮小し、8×6マスを比較する方法へ置換した。一様なRGB差を除いた局所差と、補正前の符号付き輝度差を分ける。閾値は計画どおり、局所差の上位4点平均60・4近傍45以上が3点、明暗の中央値18・同符号12以上が40点。判定前に丸めない。

`BodyViewBox` は[Pillow 12.2のBOX係数](https://raw.githubusercontent.com/python-pillow/Pillow/12.2.0/src/libImaging/Resample.c)と[軸順](https://raw.githubusercontent.com/python-pillow/Pillow/12.2.0/src/PIL/Image.py)を参照して実装した。画素中心、22bit係数、軸ごとの8bit丸めを使う。数値一致は試験担当のgolden照合で確認する。

比較対象は既存どおり4枚前。同じ身体世代・地点・縦横比を必要とする。取得した現在の1画像を既存Attachmentへ載せ、未比較・無変化でも保持する。変化マスの枠は描かず、既存のプレイヤー緑枠は維持する。物体名・成長・設置などの意味は計測しない。

## 通信契約

`body_view.scene_comparison` と保存する `frame.json.scene_comparison` の形を統一した。旧 `visual_change / compared_to_four_before / pixel_mean_difference / pixel_changed_fraction` は送信・保存しない。内部計測値はJava `Cell` にあるが、モデル・HUDへ出さない。

```json
{
  "status": "changed",
  "reference": {
    "capture_id": "view_11111111-1111-4111-8111-111111111111",
    "captured_at": "2026-10-29T03:00:00Z",
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "cells": [
    {"row": 5, "column": 3, "local_change": true, "brightness": "none"},
    {"row": 6, "column": 2, "local_change": true, "brightness": "none"},
    {"row": 6, "column": 3, "local_change": true, "brightness": "none"}
  ]
}
```

これは形の例で、実撮影記録ではない。`reference` は実際に比較した4枚前の撮影ID・時刻・添付画像hash。直前に受信した画像hashで置換しない。現在画像の身体世代は従来の受信照合を維持する。

| status | reference | cells | 新規の画像変化notice |
|---|---|---|---|
| `not_compared` | null | 空 | 作らない |
| `unchanged` | 比較した元画像 | 空 | 作らない |
| `changed` | 比較した元画像 | 1〜48マス | 既存の一度消費・優先制御へ渡す |

Rustは型付きで受信し、状態・参照有無・行列範囲・重複・マスの判定・参照時刻の先後・IDとhashの形式を検査する。brightnessは `none / lighter / darker`。現在の画像と同じcapture IDを参照元にしない。通常撮影のcapture IDはcommand IDとは別に発行される既存契約を維持する。明示注視は従来どおりcommand IDとも照合する。

`Captured.change` の重複所有を外し、Attachmentの比較結果を正本にする。`visual_capture` は内部の撮影ID・時刻・motion sequenceだけを保持する。環境の `body_view` 変化は契機だけを示し、比較元IDや比較本文をモデルへ重ねない。

## モデル入力例

以下は `Attachment::metadata_text()` の形式に合わせた例で、実行して採取したpayloadではない。現在画像のPNG一枚に、この `image_observation` を一度だけ添える。無変化は `scene_comparison="変化がない"`、未比較は `"画像は取得したが、比較できていない"`。

変化マスが上の例なら、`scene_comparison` は次の文字列になる。

```text
画像を横8×縦6に分けた位置。行は上から、列は左から数える。
画面の左側、下部にある範囲。上から5行目は左から3列目、上から6行目は左から2〜3列目。
画像の明るさの変化は検出していない。
```

位置は4近傍で連結した領域ごとに行・連続列を列挙する。明暗が混在すれば「明るくなった領域と暗くなった領域をそれぞれ検出した」と両方残す。比較元の識別情報・差分の強度・割合はこの説明へ出さない。共通画像promptに比較結果の意味を追加し、既存の同内容反復を控える文は維持する。

## 変更ファイルと引継ぎ

- `adapter/minecraft-fabric/src/main/java/dogido/fabric/screenshot/BodyViewBox.java`（新規）、`BodyViewChange.java`: 原画像の縮小と比較。
- 同 `BodyViewCapture.java`: 撮影の実際の比較元と型付き結果を保存・送信。
- `dogido-rust/src/chat_image/comparison.rs`（新規）、`chat_image.rs`: 受信契約の検証と共通の位置・明暗説明。
- `dogido-rust/src/dialogue/visual_runtime.rs`: 三状態の受信、保持と通知。
- `dogido-rust/src/dialogue/environment_runtime.rs`、`conversation_observation.rs`: 環境入力との接続と比較本文の重複除去。
- `dogido_server/llm/companion_prompts.json`: 共通の画像比較の扱い。

試験担当はJavaの旧 `BodyViewChange.observe/Result` とRustの旧 `SceneComparison` 構築を新契約へ移行する必要がある。例として `body_interest_runtime/tests.rs` に旧二boolの直接構築がある。本編集ではテストを変更していない。計画のgolden全48マス・縮小RGB、未比較／無変化の保持、既存notice一度消費、通常／環境への一回提示、実際の1画像payloadを順に確認する。新しいpromptを含む実モデル試験はユーザー確認後の範囲。
