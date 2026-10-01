#!/bin/zsh
# User-operated text test. Never auto-runs fixtures, records audio, or starts Minecraft.
set -eu

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h:h}"
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

# Read-only local preflight. No model load, MCP start, browser, network, or audio.
"${PYTHON_BIN}" - <<'PY'
from importlib.util import find_spec
import json
import os
from pathlib import Path
import sys

root = Path.cwd()
missing = [name for name in ('mcp', 'mlx_lm') if find_spec(name) is None]
if missing:
    raise SystemExit('既存Pythonで依存が見つかりません: ' + ', '.join(missing))
from dogido_server.config import Settings
from dogido_server.language_dialogue.chrome_web import DEFAULT_COMMAND

if not DEFAULT_COMMAND.is_file() or not os.access(DEFAULT_COMMAND, os.X_OK):
    raise SystemExit('専用chrome-webが未導入です。docs/language-dialogue-text-test.mdを確認してください。')
config = json.loads((root / 'scripts/chrome-web-child-config.json').read_text())
if config.get('show_browser') is not True:
    raise SystemExit('Chromeの表示設定が無効です。設定は自動変更しません。')
chrome = [base / 'Google Chrome.app/Contents/MacOS/Google Chrome'
          for base in (Path('/Applications'), Path.home() / 'Applications')]
if not any(path.is_file() and os.access(path, os.X_OK) for path in chrome):
    raise SystemExit('Google Chromeが見つかりません。ブラウザーの自動導入はしません。')
settings = Settings().llm_route_settings('chat')
if settings.llm_backend != 'mlx':
    raise SystemExit('この独立試験は既存MLX会話設定が必要です。設定は自動変更しません。')
print('準備確認: 既存Python・MLX依存・専用chrome-web・可視Chrome設定 OK')
print('会話モデル: ' + settings.mlx_model_id)
print('モデル実ロード・Chrome実起動・検索成功は、この準備確認には含みません。')
PY

if [[ "${1:-}" == "--check" ]]; then
  print -- "準備確認のみで終了しました。会話・Chrome・音声は起動していません。"
  exit 0
fi

RUN_DIR="${PROJECT_ROOT}/logs/language-dialogue/manual-web-$(date +%Y%m%d-%H%M%S)-$$"
print -- "手動テスト台本: ${PROJECT_ROOT}/docs/language-dialogue-manual-test.md"
print -- "ログ保存先: ${RUN_DIR}"
print -- "実Chromeを使います。質問→同意→ /speech-completed の後にだけ検索します。"
print -- "1行ずつ入力してください。終了は /quit。自動会話・録音・読み上げはしません。"
print -- "Googleのロボット確認が出たら、その回のWeb試験は終了してください。"
exec "${PYTHON_BIN}" -m dogido_server.language_dialogue --web --interactive --output "${RUN_DIR}"
