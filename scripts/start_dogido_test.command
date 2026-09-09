#!/bin/zsh

# macOS Terminalの通常タブから実行する。
# タブ作成・tmux・ブラウザー起動は行わず、指定プロセスを現在のシェルで動かす。

set -eu

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
PYTHON_BIN="${PROJECT_ROOT}/dogido-llm/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  MAIN_ROOT="$({
    git -C "${PROJECT_ROOT}" worktree list --porcelain 2>/dev/null || true
  } | awk '
    $1 == "worktree" { path = substr($0, 10) }
    $1 == "branch" && $2 == "refs/heads/main" { print path; exit }
  ')"
  if [[ -n "${MAIN_ROOT}" && -x "${MAIN_ROOT}/dogido-llm/bin/python" ]]; then
    PYTHON_BIN="${MAIN_ROOT}/dogido-llm/bin/python"
  fi
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  print -u2 -- "dogido-llm/bin/python が見つかりません。"
  exit 1
fi

mode="${1:-}"
case "${mode}" in
  server)
    module="dogido_server"
    label="Dogido Server"
    ;;
  voice)
    module="dogido_server.voice_input"
    label="Dogido Voice Input"
    ;;
  voice-aec)
    export DOGIDO_VOICE_ECHO_CANCELLATION=webrtc
    module="dogido_server.voice_input"
    label="Dogido Voice Input (WebRTC AEC3)"
    print -- "Macの再生音全体をAEC参照として一時取得します（参照音の保存・送信なし）。"
    ;;
  --dry-run)
    print -- "実行元: ${PROJECT_ROOT}"
    print -- "Python: ${PYTHON_BIN}"
    print -- "dry-run: プロセスは起動しません。"
    exit 0
    ;;
  *)
    print -u2 -- "使い方: ${0:t} server|voice|voice-aec|--dry-run"
    exit 2
    ;;
esac

print -- "========== ${label} =========="
print -- "実行元: ${PROJECT_ROOT}"
print -- "Python: ${PYTHON_BIN}"
print -- "停止: Ctrl+C（停止後は同じシェルへ戻ります）"

cd "${PROJECT_ROOT}"
"${PYTHON_BIN}" -m "${module}"
