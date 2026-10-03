#!/bin/zsh
set -eu
cd "${0:A:h}"
task_stage="開始前"
trap 'print -u2 -- "停止した段階: ${task_stage}。画面のエラーを確認してください。案内は Minecraft設定.txt と はじめに.md にあります。"' ZERR
if [[ "$(uname -m)" != arm64 ]]; then
  print -u2 -- "このセットは Apple Silicon の Mac 用です。"
  exit 1
fi
print -- "息子さん用ドギドの準備をします。"
print -- "Minecraft と Minecraft Launcher を、それぞれ ⌘Q で終了してください。"
read -r "reply?終了したらこの画面へ戻り、Enter で初期設定を開始します（中止は Ctrl+C）: "
export UV_PYTHON_INSTALL_DIR="$PWD/.dogido_tools/python"
export UV_CACHE_DIR="$PWD/.dogido_tools/uv-cache"
task_stage="1/3 ドギド専用Python環境と依存部品"
print -- "$task_stage"
if [[ ! -x dogido-llm/bin/python ]]; then
  print -- "ドギド専用のPython環境を用意します。Python 3.11があれば利用し、なければ取得します。"
  ./.dogido_tools/uv --no-config venv --python 3.11 --allow-existing dogido-llm
else
  print -- "準備済みのドギド専用Python環境を使います。"
fi
./.dogido_tools/uv --no-config pip install --python dogido-llm/bin/python -r requirements.txt
export PYTHONDONTWRITEBYTECODE=1
./dogido-llm/bin/python client_tools/install_fabric.py --instructions-only
task_stage="2/3 ドギドの部品確認（録音・起動なし）"
print -- "$task_stage"
./dogido-llm/bin/python client_tools/check.py
task_stage="3/3 Fabricの確認・配置"
print -- "$task_stage"
./dogido-llm/bin/python client_tools/install_fabric.py
task_stage="完了"
print -- "このMacの導入準備が完了しました。親MacへのSSH接続・Minecraft・実音声は次の手順で確認します。"
print -- "このフォルダの『Minecraft設定.txt』を開きます。"
/usr/bin/open -t "$PWD/Minecraft設定.txt" || print -- "Minecraft設定.txt を手動で開いてください。"
read -r "reply?Enter で終了: "
