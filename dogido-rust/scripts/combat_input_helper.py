#!/usr/bin/env python3
"""端末SDKの接続・応答外形検査・provider切替を担うworker。"""
# chatが必要ならneeds_chatをRustへ返す。行為根拠の採否・状態・発声・保存はRust所有。
# payloadの余分なキーを落とす前にrouterが外形を検査するため、検査だけを除去しない。
import json
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.config import Settings
from dogido_server.platform_ai import PlatformStructuredAIRouter
from dogido_server.combat_input_contract import CombatInputRequest, allowed_actions



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
            request = CombatInputRequest(messages=frame["messages"],
                details={"verse": frame["verse"], "player_text": text,
                         "allowed_actions": frame.get("allowed_actions")},
                temperature=0.0, max_tokens=120)
            # 不正なIPCはSDKの初期化前に拒否し、Rustの許可集合だけを使う。
            request.details["allowed_actions"] = allowed_actions(request)
            if self.router is None:
                self.router = PlatformStructuredAIRouter(Settings(_env_file=None,
                    **{"platform_ai_" + k: v for k, v in frame["settings"].items()}))
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
