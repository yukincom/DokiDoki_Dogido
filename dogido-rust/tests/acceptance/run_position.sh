#!/bin/sh
# パート1のみ。LLM・TTS・Minecraftは起動しない。失敗・未配線は非0で返す。
set -eu
suite_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$suite_dir/../../.." && pwd)
count=$("$repo_dir/dogido-rust/cargo.sh" test --lib --locked --offline acceptance_position:: -- --list | awk '/^dialogue::acceptance_position::.*: test$/ { n++ } END { print n+0 }')
if [ "$count" -ne 12 ]; then
    echo "受入テストは12件必要です。検出数: $count。mod.rsの配線を確認してください。" >&2
    exit 1
fi
exec "$repo_dir/dogido-rust/cargo.sh" test --lib --locked --offline acceptance_position:: -- --test-threads=1
