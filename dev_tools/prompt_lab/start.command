#!/bin/zsh
set -eu
cd "${0:A:h}/../.."
export PYTHONDONTWRITEBYTECODE=1
exec ./dogido-llm/bin/python -m dev_tools.prompt_lab serve
