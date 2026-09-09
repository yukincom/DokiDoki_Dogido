#!/bin/zsh
# User-operated real voice test. No fixture autoplay or environment installation.
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
  print -u2 -- "既存の dogido-llm/bin/python が見つかりません。環境の自動導入はしません。"
  exit 1
fi
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--check" ) ]]; then
  print -u2 -- "使い方: zsh ${0:t} [--check]"
  exit 2
fi
cd "${PROJECT_ROOT}"
if [[ "${1:-}" == "--check" ]]; then
  exec "${PYTHON_BIN}" -m dogido_server.language_dialogue.voice_test --check
fi
RUN_DIR="${PROJECT_ROOT}/logs/language-dialogue/manual-voice-$(date +%Y%m%d-%H%M%S)-$$"
print -- "音声テスト台本: ${PROJECT_ROOT}/docs/language-dialogue-voice-test.md"
print -- "ログ保存先: ${RUN_DIR}"
print -- "実マイク・STT・ドギドの実音声・同意後の実Chromeを使います。"
print -- "音声再生中もマイクは止まりません。自己音テストは /listen → /say。"
print -- "終了: /quit または Ctrl+C。旧テキスト試験が残っていれば先に終了してください。"
exec "${PYTHON_BIN}" -m dogido_server.language_dialogue.voice_test --output "${RUN_DIR}"
