"""Rustから届く原文と正規化面を、残る読み取り用parserへ渡す。"""
import logging
from dogido_server.player_input.routing import route_prepared_player_input


def prepared_context(data):
    prepared = data["prepared_input"]
    if (not isinstance(prepared, dict) or prepared.get("raw_text") != data["text"]
            or not isinstance(prepared.get("normalized_text"), str)):
        raise ValueError("prepared input does not match current text")
    # 既知誤変換の診断を維持。再び補正を実行して適用一覧を作らない。
    if prepared.get("applied_fixes"):
        logging.getLogger("uvicorn.error").warning(
            "asr_fix applied=%s original=%s fixed=%s",
            ",".join(f"{a}->{b}" for a,b in prepared["applied_fixes"]),
            data["text"][:80], prepared["normalized_text"][:80])
    return route_prepared_player_input(data["text"], prepared["normalized_text"])
