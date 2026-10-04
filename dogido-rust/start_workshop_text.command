#!/bin/zsh
# 通常本体と同じ設定の記憶保存先を使う。相談室のHTTP窓口は既定5057。
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
  print -u2 -- '既存のPython環境をDOGIDO_PYTHONで指定してください。'
  exit 1
fi
if [[ "${1:-}" == '--check' ]]; then
  exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/launch_workshop_text.py" --settings-dir "$CONFIG_ROOT" --check
fi
if [[ $# -gt 0 ]]; then
  print -u2 -- '使い方: start_workshop_text.command [--check]'
  exit 2
fi
"$SCRIPT_DIR/cargo.sh" build --release --offline --locked --example workshop_text
exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/launch_workshop_text.py" --settings-dir "$CONFIG_ROOT"
