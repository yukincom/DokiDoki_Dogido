#!/bin/sh
# 履歴記録の13件を共通runnerで実行し、件数不一致や失敗を返す。
set -eu
suite_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$suite_dir/../run.py" --part 2 "$@"
