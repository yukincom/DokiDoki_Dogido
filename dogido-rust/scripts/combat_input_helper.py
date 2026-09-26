#!/usr/bin/env python3
"""OS SDKだけを保持する移行用worker。chat生成・状態・発声・保存はRust所有。"""
import json
import logging
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.config import Settings
from dogido_server.platform_ai import PlatformStructuredAIRouter
from dogido_server.llm.types import StructuredGenerationRequest
from dogido_server.llm.prompts import build_messages
from dogido_server.haiku.combat_pause import fallback_combat_workshop_input_analysis
from dogido_server.haiku.workshop_agent import _state_change_evidence_is_safe
from workshop_helper import snapshot_for

ACTIONS = ["resume_workshop", "workshop_input", "close_workshop", "unrelated", "uncertain"]


class ChatFallback:
    def generate_structured_json(self, request):
        return {"__needs_chat": True}


def safe(action, text, evidence):
    if action in {"unrelated", "uncertain"}:
        return True
    # 再開の問いをまだしていない中断中は、対象のない相槌を再開意思にしない。
    if action == "resume_workshop" and re.fullmatch(r"(?:うん|はい|ええよ|いいよ|そうしよう|おけ|ok|OK)[。！!、\s]*", text):
        return False
    if re.search(r"(?:とは|って|という意味|ということ|わけ|つもり).{0,20}(?:ない|なく|ません|へん)", text):
        return False
    if action in {"resume_workshop", "workshop_input"} and re.search(
            r"続け(?:ない|ません|ん|へん|るな)|戻(?:らない|りません|らん|らへん|るな)|再開(?:しない|しません|せん|せえへん|するな)", text):
        return False
    if action == "close_workshop" and re.search(
            r"終わりに(?:しない|しません|せん|せえへん)|終(?:わらない|わりません|わらん)|やめ(?:ない|ません|へん|るな)", text):
        return False
    # 内容の質問は再開意思として使えるが、引用・否定された再開／終了は使わない。
    if action == "workshop_input":
        return True
    return _state_change_evidence_is_safe(
        "close_workshop" if action == "close_workshop" else "stage_player_edit",
        player_text=text, evidence=evidence)


class Worker:
    def __init__(self):
        self.router = None

    def handle(self, frame):
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
                    "provider": result.get("__dogido_platform_ai_provider", "unknown"),
                    "messages": build_messages(request)}
        if frame["op"] == "validate":
            p = frame["payload"]
            return {"safe": safe(p.get("action"), text, p.get("evidence", ""))}
        if frame["op"] == "fallback":
            a = fallback_combat_workshop_input_analysis(snapshot_for(frame), text)
            action = a.action if safe(a.action, text, a.evidence) else "uncertain"
            return {"action": action, "confidence": a.confidence, "evidence": a.evidence}
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
