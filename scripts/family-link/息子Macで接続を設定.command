#!/bin/zsh
set -u
export PYTHONDONTWRITEBYTECODE=1
cd "${0:A:h}"
print -- "息子MacのRust版ドギドの専用接続を更新します。旧Pythonセットへの更新は行いません。"
print -- "次の画面で、00_setup.command があるドギドのフォルダを選んでください。"
kit_dir=$(/usr/bin/osascript -e 'POSIX path of (choose folder with prompt "00_setup.command があるドギドのフォルダを選んでください")') || exit 0
kit_python="${kit_dir}dogido-llm/bin/python"
if [[ ! -x "$kit_python" ]]; then
  kit_python=$(command -v python3) || {
    print -u2 -- "Pythonが見つかりません。00_setup.commandで準備してください。"
    read -r "reply?Enter で閉じます: "
    exit 1
  }
fi
"$kit_python" -u apply_client.py "$kit_dir"
result=$?
read -r "reply?結果を確認したら Enter で閉じます: "
exit "$result"
