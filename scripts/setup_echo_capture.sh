#!/bin/bash
# Explicit local install/build only. Does not record, play, change sound routes, or start Dogido.
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
task_runtime="$task_root/.dogido_tools/echo-cancel"
if [[ "$(uname -s)" != Darwin ]]; then
    echo "macOS専用の音声キャプチャです。" >&2
    exit 2
fi
if [[ -L "$task_runtime" ]]; then
    echo "既存echo-cancelがsymlinkです。上書きせず確認してください。" >&2
    exit 2
fi
mkdir -p "$task_runtime/bin"
if [[ ! -x "$task_runtime/.venv/bin/python" ]]; then
    uv venv --python 3.11 --no-python-downloads "$task_runtime/.venv"
fi
uv pip install --python "$task_runtime/.venv/bin/python" --only-binary :all: -r "$task_root/scripts/echo-requirements.txt"
xcrun clang++ -std=c++17 -O2 -fobjc-arc -mmacosx-version-min=14.2 \
    -framework Foundation -framework AVFoundation -framework CoreAudio -framework AudioToolbox \
    -Wl,-sectcreate,__TEXT,__info_plist,"$task_root/native/echo_capture/Info.plist" \
    "$task_root/native/echo_capture/main.mm" -o "$task_runtime/bin/dogido-audio-capture.new"
"$task_runtime/bin/dogido-audio-capture.new" --self-test
codesign --force --sign - "$task_runtime/bin/dogido-audio-capture.new"
cd "$task_root"
"$task_runtime/.venv/bin/python" -m dogido_server.echo_input --check --helper "$task_runtime/bin/dogido-audio-capture.new"
if [[ -f "$task_runtime/bin/dogido-audio-capture" ]]; then
    cp -p "$task_runtime/bin/dogido-audio-capture" "$task_runtime/bin/dogido-audio-capture.previous"
fi
mv "$task_runtime/bin/dogido-audio-capture.new" "$task_runtime/bin/dogido-audio-capture"
echo "導入と非録音の検査が完了しました。実機の音響効果はまだ未確認です。"
