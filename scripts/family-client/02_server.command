#!/bin/zsh
set -eu
cd "${0:A:h}"
export DOGIDO_ENV_PROFILE=shared PYTHONDONTWRITEBYTECODE=1
if [[ ! -x dogido-llm/bin/python ]]; then
  print -u2 -- "先に 00_setup.command を実行してください。"
  exit 1
fi
print -- "息子さん専用のドギドを起動します。この画面で Ctrl+C を押すと終了します。"
exec ./dogido-llm/bin/python -u client_tools/log_relay.py server
