"""本体国語対話へ、利用可能なときだけ専用Chrome検索を結ぶ。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
import json
import os
from pathlib import Path
import sys

from .chrome_web import ChromeWebClient, DEFAULT_COMMAND, ROOT
from .google_overview import GoogleOverviewResearch


DEFAULT_VISIBLE_CONFIG = ROOT / "scripts" / "chrome-web-child-config.json"


@dataclass(frozen=True, slots=True)
class MainWebAvailability:
    available: bool
    reason: str


def inspect_main_web_availability(
    *,
    command: str | Path = DEFAULT_COMMAND,
    config: str | Path = DEFAULT_VISIBLE_CONFIG,
    platform_name: str | None = None,
    mcp_available: bool | None = None,
    chrome_paths: tuple[Path, ...] | None = None,
) -> MainWebAvailability:
    """ブラウザーやMCPを起動せず、本体接続の既存前提だけを読む。"""

    platform_name = platform_name or sys.platform
    if platform_name != "darwin":
        return MainWebAvailability(False, "unsupported_platform")

    command_path = Path(command)
    if not command_path.is_file() or not os.access(command_path, os.X_OK):
        return MainWebAvailability(False, "chrome_web_not_installed")

    if mcp_available is None:
        try:
            mcp_available = find_spec("mcp") is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            mcp_available = False
    if not mcp_available:
        return MainWebAvailability(False, "mcp_sdk_not_installed")

    config_path = Path(config)
    try:
        config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return MainWebAvailability(False, "visible_config_invalid")
    if not isinstance(config_payload, dict) or config_payload.get("show_browser") is not True:
        return MainWebAvailability(False, "visible_browser_disabled")

    if chrome_paths is None:
        chrome_paths = tuple(
            base / "Google Chrome.app/Contents/MacOS/Google Chrome"
            for base in (Path("/Applications"), Path.home() / "Applications")
        )
    if not any(path.is_file() and os.access(path, os.X_OK) for path in chrome_paths):
        return MainWebAvailability(False, "google_chrome_not_found")
    return MainWebAvailability(True, "ready")


def build_main_web_research() -> tuple[GoogleOverviewResearch | None, MainWebAvailability]:
    """副作用なしの確認後、実呼出しまで休眠するWeb providerを作る。"""

    availability = inspect_main_web_availability()
    if not availability.available:
        return None, availability
    client = ChromeWebClient(
        command=DEFAULT_COMMAND,
        config=DEFAULT_VISIBLE_CONFIG,
        child_view=True,
        overview_search=True,
    )
    return GoogleOverviewResearch(client), availability
