"""Chrome接続補助の副作用なしpreflight。会話状態・同意・再生の統合はRustで検査する。"""
from pathlib import Path
import json

from dogido_server.language_dialogue.main_web import inspect_main_web_availability


def test_main_web_availability_check_is_side_effect_free_and_requires_visible_profile(
    tmp_path: Path,
) -> None:
    command = tmp_path / "chrome-web-mcp"
    command.write_text("#!/bin/sh\n", encoding="utf-8")
    command.chmod(0o700)
    chrome = tmp_path / "Google Chrome"
    chrome.write_text("binary", encoding="utf-8")
    chrome.chmod(0o700)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"show_browser": True}), encoding="utf-8")

    ready = inspect_main_web_availability(
        command=command,
        config=config,
        platform_name="darwin",
        mcp_available=True,
        chrome_paths=(chrome,),
    )

    assert ready.available and ready.reason == "ready"
    config.write_text(json.dumps({"show_browser": False}), encoding="utf-8")
    hidden = inspect_main_web_availability(
        command=command,
        config=config,
        platform_name="darwin",
        mcp_available=True,
        chrome_paths=(chrome,),
    )
    assert not hidden.available and hidden.reason == "visible_browser_disabled"
