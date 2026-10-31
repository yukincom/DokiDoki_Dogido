#!/usr/bin/env python3
"""パート8測定器の配線疎通専用。既存HTTP fixtureの固定応答でAPI・保存・trace接続を検査し、実モデルの品質・採否成功とはしない。"""
import argparse
import json
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python"), str(ROOT / "acceptance")]
from check_dialogue import dependencies
import part8_model


def message(incoming):
    prompt = "\n".join(item.get("content") or "" for item in incoming["messages"])
    text = re.search(r"今回のプレイヤー発話（会話理解用）: ([^\n]+)", prompt).group(1)
    if text.startswith("この句を作るとき"):
        context = part8_model.shared_context(incoming)
        assert context["source_attribution"] == "provided_materials_unverified"
        results = [m for m in incoming["messages"] if m["role"] == "tool"]
        if not results:
            return {"role":"assistant","content":None,"tool_calls":[{
                "id":"smoke-materials","type":"function","function":{
                    "name":"inspect_verse","arguments":json.dumps({"checks":["source"]})}}]}
        assert len(results) == 1
        observed = json.loads(results[0]["content"])
        assert observed["checks"] == ["source"] and len(observed["lines"]) == 3
        for row, saved in zip(observed["lines"], context["saved_line_sources"]):
            assert row["source_atom_ids"] == saved["atom_ids"] and row["source_status"] == "recorded"
        return {"action":"explain","speech":"葉と斧が材料として渡されていた記録や。各行で実際に使ったかまでは確認してへんで。"}
    if text.startswith("上の句は何"):
        return {"action": "ask", "speech": "どの言葉に変えたい？"}
    if text.startswith("『さくらのは』"):
        return {"action": "respond", "speech": "その言葉の案を相談しよか。",
                "line_proposal": {"found": True, "target_fragment": "さくらのは",
                    "replacement_text": "さくらいろ", "evidence": text, "confidence": .9}}
    assert text.startswith("髪の子") or text.startswith("じゃあ、上の句"), text
    replacement = "ひかりさす" if text.startswith("髪の子") else "さくらいろ"
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": "smoke-edit", "type": "function", "function": {
            "name": "stage_player_edit", "arguments": json.dumps({
                "target_fragment": "さくらのは", "replacement_text": replacement,
                "evidence": text}, ensure_ascii=False)}}]}


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", required=True, type=Path)
args = parser.parse_args()
output = ROOT / "acceptance/results" / f"part8-transport-smoke-{uuid.uuid4().hex}.jsonl"
output.parent.mkdir(parents=True, exist_ok=True)
with dependencies() as (dep, control, seen):
    control["structured_handler"] = message
    sys.argv = ["part8_model.py", "--binary",
        str(args.binary.resolve()),
        "--base-url", f"{dep}/v1", "--model", "transport-fixture-not-a-real-model",
        "--output", str(output)]
    code = part8_model.main()
results = [record for record in part8_model.read_rows(output) if record.get("kind") == "result"]
print(json.dumps({"scope": "transport smoke only; model quality unmeasured", "exit": code,
    "result_file": str(output), "results": results}, ensure_ascii=False, indent=2))
assert code == 2 and len(results) == 4
assert all(row["automatic_checks"] == "pass" and row["acceptance"] == "review_required" for row in results)
