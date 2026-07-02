# 実行時依存ライブラリ

この文書は、ドギドをまず PC 上で動かすための依存ライブラリ整理です。

対象環境は、2026-05-25 時点の macOS / Apple Silicon / Python 3.11 系を主に想定しています。

## 1. 結論

### 今すぐ必要なもの

PC からドギドの声を鳴らすだけなら、まず必要なのはこれです。

- `fastapi`
- `uvicorn`
- `pydantic`
- `pydantic-settings`
- `httpx`
- `orjson`
- `PyYAML`
- `mlx-lm`

### まだ不要なもの

- `whisper.cpp`
- `mlx-whisper`
- `ffmpeg`

これらは **マイク入力や音声認識を入れる段階になってから** で十分です。

## 2. `whisper.cpp` は今いるか

今の段階ではいりません。

理由:

- 今やりたいのは `PC からドギドの声を出すこと`
- `whisper.cpp` は主に音声認識用
- 先に必要なのは `TTS/音声再生` と `イベント処理`

### `whisper.cpp` を使う場合

- ネイティブ実装なので、基本的には環境ごとにビルドや導入が必要
- OS や CPU アーキテクチャ差の影響を受けやすい
- その代わりローカル STT としては軽量で扱いやすい

公式:

- [whisper.cpp](https://github.com/ggml-org/whisper.cpp)

## 3. Apple Silicon での STT 候補

macOS / Apple Silicon なら、`whisper.cpp` より先に `mlx-whisper` を検討したほうが自然です。

MLX 公式の Whisper 例では、`ffmpeg` を入れて `pip install mlx-whisper` で動かす前提になっています。

公式:

- [MLX Whisper example](https://github.com/ml-explore/mlx-examples/blob/main/whisper/README.md)

### 方針

- **今** は STT を入れない
- 音声入力を入れる段階で
  - macOS/Apple Silicon: `mlx-whisper`
  - クロスプラットフォーム寄り: `whisper.cpp`

のどちらかを選ぶ

## 4. LLM

ローカル LLM は `mlx-lm` 前提で良いです。

公式:

- [mlx-lm](https://github.com/ml-explore/mlx-lm)

### 役割

- 通常会話
- 短い状況説明
- 昼の mob 雑談

### 役割外

- 絶叫 cue
- 即時パニック割り込み
- 状態遷移判定

これらはコードでやる前提です。

## 5. 音声出力の選択肢

## A. まず最短で鳴らす

macOS 標準を使う。

- `say`
- `afplay`

### 長所

- 追加インストールがほぼ不要
- すぐ試せる

### 短所

- 声質の作り込みは弱い
- 将来のクロスプラットフォーム性は低い

### 用途

- 最初の疎通確認
- 反応速度確認
- state machine テスト

## B. クロスプラットフォーム寄りの音声再生

- `pygame`

`pygame.mixer` は音声再生停止や複数チャンネル制御がしやすいです。

公式:

- [pygame.mixer](https://www.pygame.org/docs/ref/mixer.html)
- [pygame.mixer.music](https://www.pygame.org/docs/ref/music.html)

### 長所

- cue の割り込み制御を作りやすい
- BGM/SE 的な扱いに寄せやすい

### 短所

- 依存は少し増える

## C. 波形ベースでシンプルに鳴らす

- `sounddevice`
- `soundfile`

`sounddevice` は NumPy 配列や音声データの再生が簡単です。

公式:

- [python-sounddevice](https://pypi.org/project/sounddevice/)

### 長所

- WAV を直接扱いやすい
- TTS の出力波形をすぐ鳴らせる

### 短所

- cue のチャンネル制御は `pygame` より弱い

## 6. TTS の選び方

## まずはこれ

- macOS の `say`

## 次段階

- ローカル TTS エンジン

候補はあるが、今はまだ固定しなくてよいです。

### 注意

過去によく使われた `Piper` は元リポジトリが archive 状態です。

公式:

- [rhasspy/piper](https://github.com/rhasspy/piper)

### 現時点のおすすめ方針

- まずは `say` で実装を進める
- あとで voice quality が必要になった段階で TTS を差し替える

## 7. FastAPI まわり

受信 API は `FastAPI + Uvicorn` で十分です。

公式:

- [FastAPI First Steps](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [FastAPI BackgroundTasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [Uvicorn Installation](https://uvicorn.dev/installation/)
- [Pydantic Settings](https://docs.pydantic.dev/latest/api/pydantic_settings/)

### 入れておきたいもの

- `fastapi`
- `uvicorn[standard]`
- `pydantic`
- `pydantic-settings`

## 8. adapter/server 通信

- `httpx`

用途:

- adapter 側クライアント
- server 側の疎通テスト

公式:

- [httpx](https://pypi.org/project/httpx/)

## 9. まず入れておく Python ライブラリ一覧

## 必須

```text
fastapi
uvicorn[standard]
pydantic
pydantic-settings
httpx
orjson
PyYAML
mlx-lm
```

## PC 再生用の追加候補

どちらか 1 系統でよいです。

### 最小

- 追加なし
  - `say`
  - `afplay`

### クロスプラットフォーム寄り

```text
pygame
```

### 波形ベース

```text
sounddevice
soundfile
numpy
```

## あとで追加

### STT

```text
mlx-whisper
```

追加で:

- `ffmpeg`

### あると便利

```text
tenacity
pytest
pytest-asyncio
```

## 10. macOS での最初のおすすめ構成

今の環境なら、最初はこれが一番軽いです。

### Python

```text
fastapi
uvicorn[standard]
pydantic
pydantic-settings
httpx
orjson
PyYAML
mlx-lm
```

### 音声

- TTS: `say`
- cue 再生: `afplay`

### まだ入れない

- `whisper.cpp`
- `mlx-whisper`
- `ffmpeg`
- 大きい TTS エンジン

## 11. 次の段階のおすすめ構成

マイク入力も入れたくなったらこうする。

### Python

```text
fastapi
uvicorn[standard]
pydantic
pydantic-settings
httpx
orjson
PyYAML
mlx-lm
mlx-whisper
```

### システム

- `ffmpeg`

### 音声

- 一旦 `say` 継続でもよい
- その後 TTS を差し替える

## 12. 実務上のおすすめ

- 今は `whisper.cpp` を入れない
- まずは `say` と `afplay` で PC から鳴らす
- STT は後で `mlx-whisper` を入れる
- 依存を増やしすぎない

この順で進めるのが最も詰まりにくいです。
