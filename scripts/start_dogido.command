#!/bin/zsh

# macOS Terminalの通常タブから実行する。
# タブ作成・tmux・ブラウザー起動は行わず、指定プロセスを現在のシェルで動かす。

set -eu

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
PYTHON_BIN="${PROJECT_ROOT}/dogido-llm/bin/python"
profile="${DOGIDO_ENV_PROFILE:-standalone}"

if [[ "${1:-}" == "--profile" ]]; then
  if [[ $# -lt 2 ]]; then
    print -u2 -- "--profile には standalone または shared を指定してください。"
    exit 2
  fi
  profile="$2"
  shift 2
fi

case "${profile}" in
  standalone|shared)
    export DOGIDO_ENV_PROFILE="${profile}"
    ;;
  *)
    print -u2 -- "profile は standalone または shared を指定してください: ${profile}"
    exit 2
    ;;
esac

if [[ "${profile}" == "shared" && ! -f "${PROJECT_ROOT}/.env.shared" ]]; then
  print -u2 -- ".env.shared がありません。cp .env.shared.example .env.shared で作成してください。"
  exit 1
fi

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
    print -- "プロファイル: ${profile}"
    if [[ "${profile}" == "shared" ]]; then
      print -- "設定: .env + .env.shared"
    else
      print -- "設定: .env"
    fi
    print -- "dry-run: プロセスは起動しません。"
    exit 0
    ;;
  *)
    print -u2 -- "使い方: ${0:t} [--profile standalone|shared] server|voice|voice-aec|--dry-run"
    exit 2
    ;;
esac

print -- "========== ${label} =========="

cd "${PROJECT_ROOT}"
"${PYTHON_BIN}" -m "${module}"
