#!/usr/bin/env python3
"""現行Pythonのplanner定型文を静的segmentへ書き出す。Rust実行時はPython不要。"""
from pathlib import Path
import json
import re
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.llm.player_chat_prompts import build_player_chat_plan_messages
from dogido_server.llm.structured_contracts import _PlayerChatPlan


def segments(text, replacements):
    # 出力を再解釈しない一回の置換。発話中のslot風文字列は常にデータ。
    pattern = re.compile("|".join(re.escape(s) for s in sorted(replacements, key=len, reverse=True)))
    result = []
    start = 0
    for match in pattern.finditer(text):
        if match.start() > start:
            result.append({"literal": text[start:match.start()]})
        result.append({"slot": replacements[match.group()]})
        start = match.end()
    if start < len(text):
        result.append({"literal": text[start:]})
    return result


def main():
    assets = {}
    for repair in (False, True):
        details = {key: {"_placeholder": key} for key in ("current", "observations", "routing_hints")}
        details["history"] = [{"_placeholder": "history"}]
        details["allowed_actions"] = ["repair_conversation"] if repair else ["continue_conversation"]
        details["pending_repair"] = {"repair_target_turn_id": "_target_id", "repair_target_quote": "_target_quote"}
        replacements = {json.dumps(value, ensure_ascii=False): key for key, value in details.items()}
        replacements[json.dumps({"turn_id": "_target_id", "quote": "_target_quote"}, ensure_ascii=False)] = "target_evidence"
        messages = build_player_chat_plan_messages(SimpleNamespace(details=details))
        converted = [{"role": m["role"], "segments": segments(m["content"], replacements)} for m in messages]
        assets["repair" if repair else "normal"] = converted[:2]
        assets["pending"] = converted[2]
    assets["schema"] = _PlayerChatPlan.model_json_schema()
    path = ROOT / "dogido-rust/src/planner/prompts.json"
    path.write_text(json.dumps(assets, ensure_ascii=False, indent=2) + "\n")
    print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
