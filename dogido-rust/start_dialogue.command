#!/bin/zsh
set -eu
SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
COMMON_DIR="$(git -C "$PROJECT_ROOT" rev-parse --path-format=absolute --git-common-dir)"
CONFIG_ROOT="${DOGIDO_RUST_SETTINGS_DIR:-${COMMON_DIR:h}}"
PYTHON_BIN="${DOGIDO_PYTHON:-$CONFIG_ROOT/dogido-llm/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  print -u2 -- '既存のPython環境が見つかりません。DOGIDO_PYTHONで指定してください。自動導入はしません。'
  exit 1
fi
if [[ "${1:-}" == '--check' ]]; then
  exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/launch_dialogue.py" --settings-dir "$CONFIG_ROOT" --check
fi
if [[ $# -gt 0 ]]; then
  print -u2 -- '使い方: start_dialogue.command [--check]'
  exit 2
fi
"$SCRIPT_DIR/cargo.sh" build --release --offline
exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/launch_dialogue.py" --settings-dir "$CONFIG_ROOT"
