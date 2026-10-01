#!/usr/bin/env python3
"""OS SDKだけを保持する移行用worker。chat生成・状態・発声・保存はRust所有。"""
import json
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.config import Settings
from dogido_server.platform_ai import PlatformStructuredAIRouter
from dogido_server.llm.types import StructuredGenerationRequest

ACTIONS = ["resume_workshop", "workshop_input", "close_workshop", "unrelated", "uncertain"]


class ChatFallback:
    def generate_structured_json(self, request):
        return {"__needs_chat": True}




class Worker:
    def __init__(self):
        self.router = None

    def handle(self, frame):
        if frame["op"] == "tts_tokens":
            from tts_shared_tokens import handle
            return handle(frame)
        text = frame["text"]
        if frame["op"] == "classify":
            if self.router is None:
                self.router = PlatformStructuredAIRouter(Settings(_env_file=None,
                    **{"platform_ai_" + k: v for k, v in frame["settings"].items()}))
            request = StructuredGenerationRequest(kind="haiku_workshop_combat_input",
                fallback_value={"action": "uncertain", "confidence": 0.0, "evidence": ""},
                details={"verse": frame["verse"], "player_text": text, "allowed_actions": ACTIONS},
                temperature=0.0, route="chat", max_tokens=120)
            result = self.router.generate_structured_json(request, fallback=ChatFallback())
            return {"payload": {k: result[k] for k in ("action", "confidence", "evidence") if k in result},
                    "needs_chat": result.get("__needs_chat", False),
                    "provider": result.get("__dogido_platform_ai_provider", "unknown")}
        raise ValueError("unsupported combat classifier operation")

    def close(self):
        if self.router is not None:
            self.router.close()


def main():
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    worker = Worker()
    try:
        for line in sys.stdin:
            try:
                result = worker.handle(json.loads(line))
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
