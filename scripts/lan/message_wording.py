"""共有Macの固定モデルで、宛先と送り主を指定した伝言を一度だけ整える。"""
from __future__ import annotations

import http.client
import json
import unicodedata


def render_message(port: int, sender: str, player: str, text: str) -> str:
    """受付済みの一行を読み上げ文にする。モデル失敗時は送り主を添えて原文をすべて返す。"""
    sender = (sender or "").strip() or "送り主"
    player = (player or "").strip()
    original = f"{sender}から伝言や。「{text}」"
    if not player:
        return original
    instruction = (
        f"{sender}から{player}へ伝言や！４０字くらいで{player}に伝えてや。"
        "メッセージの内容の意味は変えたらあかんで！"
    )
    request = {
        "model": "default_model",
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": text},
        ],
        "max_tokens": 512,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    # 共有アプリが開いたloopbackだけに送る。家庭の伝言をHTTPプロキシやログへ渡さない。
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        connection.request("POST", "/v1/chat/completions", json.dumps(request),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            return original
        raw = response.read(32769)
        if len(raw) > 32768:
            return original
        choice = json.loads(raw)["choices"][0]
        message = choice["message"]
        if (choice.get("finish_reason") != "stop" or message.get("role") != "assistant"
                or message.get("tool_calls") or not isinstance(message.get("content"), str)):
            return original
        # 短い読み上げ文として扱える完結した返答だけを採用する。原文は長くても切らない。
        result = " ".join(message["content"].splitlines()).strip()
        if (not result or len(result) > 1000 or "<think>" in result or "</think>" in result
                or any(unicodedata.category(c) in {"Cc", "Cs"} for c in result)):
            return original
        # 送り主を言わないモデル出力でも、家庭からの伝言だと音声と画面の両方で分かるようにする。
        return f"{sender}からの伝言やで。{result}"
    except (OSError, http.client.HTTPException, ValueError, KeyError, IndexError, TypeError,
            AttributeError, RecursionError):
        return original
    finally:
        connection.close()
