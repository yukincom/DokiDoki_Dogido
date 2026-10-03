#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ ! -f .dogido_tools/family-link/connection.json || ! -f client_tools/family_link.py ]]; then
  print -u2 -- "先に『息子Macへ渡す.zip』の接続更新を適用してください。親Macのパスワードは使いません。"
  exit 1
fi
if [[ ! -x dogido-llm/bin/python ]]; then
  print -u2 -- "先に 00_setup.command で専用Python環境を準備してください。"
  exit 1
fi
exec ./dogido-llm/bin/python -u client_tools/family_link.py "$@"
