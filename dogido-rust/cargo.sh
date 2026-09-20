#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(dirname -- "$script_dir")

# 通常のRust環境、またはこの移植用に用意した局所toolchainを使う。
if ! command -v cargo >/dev/null 2>&1; then
    for candidate in "${DOGIDO_RUST_TOOLCHAIN_DIR:-}" "$repo_dir/.dogido_tools/rust" "$repo_dir/../rust"; do
        if [ -n "$candidate" ] && [ -x "$candidate/cargo/bin/cargo" ]; then
            export CARGO_HOME="$candidate/cargo"
            export RUSTUP_HOME="$candidate/rustup"
            export PATH="$CARGO_HOME/bin:$PATH"
            break
        fi
    done
fi
if ! command -v cargo >/dev/null 2>&1; then
    echo 'Rustが必要です。rustupで導入するか、DOGIDO_RUST_TOOLCHAIN_DIRを指定してください。' >&2
    exit 1
fi
cd "$script_dir"
exec cargo "$@"
