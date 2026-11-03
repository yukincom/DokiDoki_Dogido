#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ ! -x dogido-llm/bin/python ]]; then
  print -u2 -- "先に 00_setup.command を実行してください。"
  exit 1
fi
./dogido-llm/bin/python client_tools/configure_call_name.py
read -r "reply?Enter で終了: "
