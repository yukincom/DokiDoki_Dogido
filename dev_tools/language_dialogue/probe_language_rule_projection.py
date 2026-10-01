"""Post-hoc diagnostic: append stored rules, not question-specific answers.

This is not a held-out evaluation and does not modify the registered A/B policies.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_language_retrieval import (
    ExperimentModel, Settings, MODEL_ID, ROOT, StructuredGenerationRequest,
    FROZEN_POLICY_HASH, policy_hash, measure_call,
)


def main():
    assert policy_hash() == FROZEN_POLICY_HASH
    folder = ROOT / "logs/language-dialogue/retrieval-compare-20260907-v1"
    selected_ids = {"new_05", "new_07", "new_12"}
    rows = [json.loads(s) for s in (folder / "results.jsonl").read_text().splitlines()]
    rows = [r for r in rows if r["case_id"] in selected_ids]
    originals = {}
    for name in ("japanese_grammar.json", "historical_kana_and_scripts.json", "makurakotoba.json", "japanese_poetry_forms.json"):
        data = json.loads((ROOT / "reference/language_education_and_poetry" / name).read_text())
        for entry in data["entries"]:
            originals[entry["id"]] = entry
    settings = Settings().llm_route_settings("chat")
    assert settings.llm_backend == "mlx" and settings.mlx_model_id == MODEL_ID
    model = ExperimentModel(settings)
    model.variant = "B"
    if not model.preload():
        raise SystemExit(model.disabled_reason())
    results = []
    for row in rows:
        request_data = next(c["request"] for c in row["variants"]["B"]["calls"] if c["request"]["kind"] == "language_dialogue_reply")
        request = StructuredGenerationRequest(**request_data)
        before = json.loads(json.dumps(request.details, ensure_ascii=False))
        for fact in request.details["facts"]:
            original = originals.get(fact["id"], {})
            if original.get("rules"):
                fact["rules"] = original["rules"]
            if original.get("structured_data"):
                fact["structured_data"] = original["structured_data"]
        result = measure_call(model, request)
        result.update(case_id=row["case_id"], question=row["case"]["text"],
                      original_B=row["variants"]["B"]["record"].get("reply_analysis"),
                      original_input=before, expanded_request=asdict(request))
        results.append(result)
        print(json.dumps({"case_id": row["case_id"], "reply": result["result"], "duration_ms": result["duration_ms"]}, ensure_ascii=False), flush=True)
    artifact = {"post_hoc": True, "created_at": datetime.now(timezone.utc).isoformat(),
                "model": MODEL_ID, "policy_hash": policy_hash(), "rules": "all stored rules and structured_data of each already retrieved core entry; no new queries or facts",
                "no_audio": True, "no_web": True, "results": results}
    path = folder / "rule_projection_diagnostic.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(artifact, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
