#!/usr/bin/env python3
"""Stdio MCP access to the already-running, local text workshop. No service launch."""
import os
from typing import Any
from mcp.server.fastmcp import FastMCP
from workshop_text_client import call, speak

BASE = os.environ.get('DOGIDO_WORKSHOP_URL', 'http://127.0.0.1:5057')
mcp = FastMCP('dogido-workshop', instructions=(
    '保存句を使うドギドのテキスト相談室。最初に句と接続一覧、現在プロンプトを確認する。'
    '独立検査は新しいworkshopを開き、人間が使っている接続を勝手に中断しない。'
    'プロンプトはこの相談室の全接続に共有される。変更は次の発言から適用される。'
    '変更前の設定を取得し、versionを指定して更新する。コードの採否・保存検査は変更できない。'
    '実送信プロンプトと応答を比較して調整する。ゲーム用サーバーと元の記憶には触れない。'))


@mcp.tool()
def list_poems() -> Any:
    """List saved poems with their keys, text, timestamps and recorded scene."""
    return call('poems', base=BASE)


@mcp.tool()
def list_workshops() -> Any:
    """List currently open text sessions and their conversation history."""
    return call('sessions', base=BASE)


@mcp.tool()
def open_workshop(poem_key: str) -> Any:
    """Open a new independent conversation for a key returned by list_poems."""
    return call('open', {'key': poem_key}, BASE)


@mcp.tool()
def talk_to_dogido(session_id: str, text: str) -> Any:
    """Send one player turn, wait for the result, and retain the delivered answer in context."""
    return speak(session_id, text, BASE)


@mcp.tool()
def inspect_workshop(session_id: str) -> Any:
    """Read the current verse, pending edit, transcript and delivery results."""
    return call('snapshot', {'session_id': session_id}, BASE)


@mcp.tool()
def close_workshop_session(session_id: str) -> Any:
    """Release a text session you opened for testing. Do not close the human's conversation."""
    return call('close', {'session_id': session_id}, BASE)


@mcp.tool()
def get_prompts() -> Any:
    """Read active prompt settings, defaults and concurrency version."""
    return call('prompts', base=BASE)


@mcp.tool()
def set_prompts(settings: dict[str, Any], expected_version: int) -> Any:
    """Apply and save text-window prompts. Preserve {{...}} placeholders. Affects all text sessions."""
    return call('prompts', {'settings': settings, 'expected_version': expected_version}, BASE)


@mcp.tool()
def inspect_last_prompt(session_id: str) -> Any:
    """Read the exact last workshop model request and the applied prompt version."""
    return call('last-prompt', {'session_id': session_id}, BASE)


@mcp.tool()
def interrupt_reply(session_id: str) -> Any:
    """Cancel only the specified text session's current reply."""
    return call('interrupt', {'session_id': session_id}, BASE)


if __name__ == '__main__':
    mcp.run(transport='stdio')
