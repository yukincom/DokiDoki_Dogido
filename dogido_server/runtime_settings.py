"""RustとPython起動設定で共有する既定値。値の正本は隣のJSONだけ。"""
import json
import os
from pathlib import Path
import warnings

from dotenv import dotenv_values

DEFAULTS = json.loads(Path(__file__).with_name("runtime_defaults.json").read_text(encoding="utf-8"))
COMBAT_DEFAULTS = DEFAULTS["combat"]
SERVER_DEFAULTS = DEFAULTS["server"]

# 終了済み設定は値を表示せず、名前だけを知らせる。既存.envは書き換えない。
RETIRED_SETTINGS = frozenset({
    "DOGIDO_ALLOW_NON_LOCAL_BIND",
    "DOGIDO_AUDIO_MAX_PENDING_BATCHES",
    "DOGIDO_COMBAT_CHAT_ACK_COOLDOWN_MS",
    "DOGIDO_COMBAT_CLEAR_DISTANCE",
    "DOGIDO_CUE_BACKEND",
    "DOGIDO_DIAGNOSTIC_HISTORY_MAX_ENTRIES",
    "DOGIDO_DISPLAY_HISTORY_MAX_ENTRIES",
    "DOGIDO_MULTI_HOSTILE_DISTANCE",
    "DOGIDO_SAY_VOICE",
    "DOGIDO_SERVICE_NAME",
    "DOGIDO_SERVICE_VERSION",
    "DOGIDO_SLEEPING_NEIGHBOR_COMMENT_COOLDOWN_MS",
    "DOGIDO_SLEEP_PROMPT_COOLDOWN_MS",
    "DOGIDO_VOICEVOX_CACHE_MAX_AGE_DAYS",
    "DOGIDO_VOICEVOX_CACHE_MAX_MB",
    "DOGIDO_VOICEVOX_PREWARM_ENABLED",
    "DOGIDO_VOICEVOX_TEMP_DIR",
})

def warn_retired_settings(env_files):
    configured = {key.upper() for key in os.environ}
    for path in env_files:
        if Path(path).is_file():
            configured.update(key.upper() for key in dotenv_values(path))
    retired = sorted(configured & RETIRED_SETTINGS)
    if retired:
        warnings.warn("現行Rustでは廃止された設定です: " + ", ".join(retired), UserWarning, stacklevel=2)
