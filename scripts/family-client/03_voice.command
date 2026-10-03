#!/bin/zsh
set -eu
cd "${0:A:h}"
export DOGIDO_ENV_PROFILE=shared DOGIDO_VOICE_ECHO_CANCELLATION=webrtc PYTHONDONTWRITEBYTECODE=1
if [[ ! -x dogido-llm/bin/python ]]; then
  print -u2 -- "先に 00_setup.command を実行してください。"
  exit 1
fi
print -- "息子さんのMacのマイクを使います。Macの再生音もエコー除去の参照に使います。"
print -- "参照音は保存・送信しません。この画面で Ctrl+C を押すとマイクを終了します。"
exec ./dogido-llm/bin/python -u client_tools/log_relay.py voice
