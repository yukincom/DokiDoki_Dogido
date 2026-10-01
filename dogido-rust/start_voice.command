#!/bin/zsh
set -eu
SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
if [[ -n "${DOGIDO_RUST_SETTINGS_DIR:-}" ]]; then
  CONFIG_ROOT="$DOGIDO_RUST_SETTINGS_DIR"
elif COMMON_DIR="$(git -C "$PROJECT_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
  CONFIG_ROOT="${COMMON_DIR:h}"
else
  CONFIG_ROOT="$PROJECT_ROOT"
fi
PYTHON_BIN="${DOGIDO_PYTHON:-$CONFIG_ROOT/dogido-llm/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  print -u2 -- '既存のPython環境が見つかりません。DOGIDO_PYTHONで指定してください。'
  exit 1
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/launch_dialogue.py" --settings-dir "$CONFIG_ROOT" --voice "$@"
