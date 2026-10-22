#!/bin/zsh
# 本棚専用。Minecraft・LLM・音声入力は起動しない。
set -eu
SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
PYTHON_BIN="${PROJECT_ROOT}/dogido-llm/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  print -u2 -- "dogido-llm/bin/python が見つかりません。"
  exit 1
fi
cd "${PROJECT_ROOT}"
exec "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/start_dogido_library.py" "$@"
