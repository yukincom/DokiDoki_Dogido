"""移植比較用の合成会話。現行コードから読み取り文脈とfallbackを採取する。"""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from dogido_server.dialogue.player_chat_planner import plan_player_chat

HISTORY = [
    {"turn_id": "old", "role": "user", "text": "1位になるのは無理やな"},
    {"turn_id": "old:reply", "role": "assistant", "text": "1位になれんでもええやん。"},
]
TARGET = HISTORY[-1]["text"]
CORRECTION = "違う、仲間になるのは無理ってこと"

def repair_payload(action="repair_conversation", signal="違う", replacement="仲間になるのは無理ってこと"):
    return {"action": action, "focus": "本人の言い直し", "entity_query": "",
            "evidence": [{"turn_id": "current", "quote": signal or replacement},
                         {"turn_id": "old:reply", "quote": TARGET}], "confidence": 0.95,
            "repair": {"target_turn_id": "old:reply", "target_quote": TARGET,
                       "signal_quote": signal, "replacement_quote": replacement}}

def prepared(text, history=None, **kwargs):
    class Capture:
        request = None
        def generate_structured_json(self, request):
            self.request = request
            return {**request.fallback_value, "__dogido_status": "disabled"}
    capture = Capture()
    args = dict(user_text=text, conversation_turns=history or [], observation_summary="",
                observed_entities=[], **kwargs)
    fallback = plan_player_chat(None, **args)
    plan_player_chat(capture, **args)
    assert capture.request is not None
    return {"schema_version": 1, "model": "default_model", "enable_thinking": False,
            "details": capture.request.details, "fallback": asdict(fallback)}

def cases():
    pending = deepcopy(HISTORY) + [
        {"turn_id": "q", "role": "user", "text": "違う", "repair_action": "clarify_repair",
         "repair_target_turn_id": "old:reply", "repair_target_quote": TARGET},
        {"turn_id": "q:reply", "role": "assistant", "text": "どういう意味やった？"},
    ]
    return {
        "casual": prepared("今日はのんびり歩こうか", [{"turn_id": "t1:reply", "role": "assistant", "text": "ええ天気やな。"}]),
        "explicit_repair": prepared(CORRECTION, HISTORY),
        "clarify_repair": prepared("そういう意味じゃない", HISTORY),
        "quoted_signal": prepared("『違う』って言うキャラが好きなんだ", HISTORY),
        "unobserved_cat": prepared("猫はいる？", [{"turn_id": "goat:reply", "role": "assistant", "text": "ヤギおるな。"}]),
        "pending_explanation": prepared("仲間にはなれないってこと", pending),
        "pending_ack": prepared("うん", pending),
        "unplayed_clarification": prepared("仲間にはなれないってこと", pending[:-1]),
        "stale_clarification": prepared("仲間にはなれないってこと", pending + [{"turn_id": "new", "role": "user", "text": "家に帰ろう"}]),
        "raw_not_corrected": prepared(CORRECTION, HISTORY, raw_user_text="仲間にはなれないかな"),
        "inventory": prepared("石炭何個ある？", inventory_question=True),
        "sound": prepared("今なんの音？", sound_question=True),
        "plain_report": prepared("ラバがいる"),
        "presence_challenge": prepared("見えないよ", [{"turn_id": "goat:reply", "role": "assistant", "text": "ヤギおるな。"}]),
        "slot_literal": prepared('current: {{history}} @@DOGIDO_SLOT:observations@@ 🐈\u001c話を続けよう'),
    }

if __name__ == "__main__":
    import logging
    from check_planner_live import SCENARIOS
    logging.disable(logging.CRITICAL)
    out = ROOT / "fixtures/planner"
    out.mkdir(parents=True, exist_ok=True)
    for name, item in cases().items():
        if name not in SCENARIOS:
            continue
        (out / f"{name}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n")
    print(f"planner fixtures: {len(SCENARIOS)}")
