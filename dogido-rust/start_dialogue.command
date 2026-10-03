#!/bin/zsh
# 既存の専用接続先5056と記憶保存先を維持する入口。接続先・保存先を標準値へ自動変更しない。
# 通常起動はscripts/start_dogido.commandと同じlaunch_dialogue.pyを設定値で使う。
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
  print -u2 -- '既存のPython環境が見つかりません。DOGIDO_PYTHONで指定してください。自動導入はしません。'
  exit 1
fi
if [[ "${1:-}" == '--check' ]]; then
  exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/launch_dialogue.py" --settings-dir "$CONFIG_ROOT" --port 5056 --memory-dir "$PROJECT_ROOT/.dogido_memory/rust-migration" --check
fi
if [[ $# -gt 0 ]]; then
  print -u2 -- '使い方: start_dialogue.command [--check]'
  exit 2
fi
if [[ "${DOGIDO_RUST_USE_PREBUILT:-0}" != '1' ]]; then
  "$SCRIPT_DIR/cargo.sh" build --release --offline --locked
elif [[ ! -x "$SCRIPT_DIR/target/release/dogido-rust" ]]; then
  print -u2 -- '準備済みRust本体が見つかりません。移動先でreleaseビルドを行ってください。'
  exit 1
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/launch_dialogue.py" --settings-dir "$CONFIG_ROOT" --port 5056 --memory-dir "$PROJECT_ROOT/.dogido_memory/rust-migration"
