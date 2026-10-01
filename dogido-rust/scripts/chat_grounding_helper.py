"""Closed projection only. Native Rust owns entity matching and fixed replies."""
from dataclasses import asdict
import math
from dogido_server.dialogue.player_chat_planner import PlayerChatEntityGrounding


def grounding_input(plan, *, topic_hits, observed_entities):
    hits = []
    for row in topic_hits[:8]:
        # Keep the canonical grounding coercion, including blank labels and scores.
        try:
            score = float(row.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        # Canonical nonfinite scores never form an ambiguity tie. JSON cannot carry
        # them: null is the explicit nonfinite marker (Rust converts it to NaN).
        hits.append({"entry_id": str(row.get("entry_id") or ""),
                     "label": str(row.get("label_ja") or row.get("label") or ""),
                     "score": score if math.isfinite(score) else None})
    observed = [{"entity_id": str(row.get("entity_id") or ""),
                 "label": str(row.get("label") or "")}
                for row in observed_entities]
    return {"schema_version": 1, "source": "current_observation",
            "plan": asdict(plan), "topic_hits": hits, "observed_entities": observed}


def grounding_result(report):
    if not isinstance(report, dict) or set(report) != {"grounding", "fixed_reply"}:
        raise ValueError("invalid native grounding result")
    row = report["grounding"]
    if not isinstance(row, dict) or set(row) != {
        "status", "query", "candidate_ids", "candidate_labels", "observed_ids", "observed_labels"
    }:
        raise ValueError("invalid native grounding shape")
    if row["status"] not in {"not_applicable", "unknown", "ambiguous", "not_observed", "observed"}:
        raise ValueError("invalid native grounding status")
    if not isinstance(row["query"], str) or not isinstance(report["fixed_reply"], str):
        raise ValueError("invalid native grounding text")
    values = dict(row)
    for key in ("candidate_ids", "candidate_labels", "observed_ids", "observed_labels"):
        if not isinstance(row[key], list) or any(not isinstance(item, str) for item in row[key]):
            raise ValueError("invalid native grounding list")
        values[key] = tuple(row[key])
    return PlayerChatEntityGrounding(**values), report["fixed_reply"]


def topics_input(plan, *, topic_hits, observed_entities, has_visual_threats,
                 threat_summary, user_text, observed_ids, native_catalog=False, name_context=None):
    """Project raw hits; filtering and policy belong to the same native exchange."""
    frame = grounding_input(plan, topic_hits=topic_hits, observed_entities=observed_entities)
    if len(topic_hits) > 8:
        raise ValueError("topic projection exceeds catalog limit")
    frame["topic_policy"] = {
        "has_visual_threats": bool(has_visual_threats),
        "threat_summary": threat_summary, "user_text": user_text,
        "observed_ids": list(observed_ids),
        "topic_hits": [{"entry_id": str(row.get("entry_id") or ""),
                        "label_ja": str(row.get("label_ja") or ""),
                        "matched_terms": list(row.get("matched_terms") or []),
                        "score": float(row.get("score") or 0.0)} for row in topic_hits],
    }
    if native_catalog:
        if topic_hits:
            raise ValueError("native catalog cannot accept Python hits")
        frame["native_catalog"] = True
    if name_context is not None:
        frame["name_context"] = name_context
    return frame


def topics_result(report, topic_hits):
    base_keys = {"grounding", "fixed_reply", "topics"}
    if not isinstance(report, dict) or set(report) not in (base_keys, base_keys | {"catalog", "catalog_topic_hints"}, base_keys | {"catalog", "catalog_topic_hints", "names"}):
        raise ValueError("invalid native topics result")
    if "catalog" in report:
        if not isinstance(report["catalog"], list) or not isinstance(report["catalog_topic_hints"], str):
            raise ValueError("invalid native catalog projection")
        topic_hits = report["catalog"]
        for row in topic_hits:
            if (not isinstance(row, dict) or set(row) != {"entry_id", "kind", "label_ja", "score", "matched_terms", "observed"}
                    or any(not isinstance(row[k], str) for k in ("entry_id", "label_ja"))
                    or row["kind"] not in {"mob", "structure"} or type(row["observed"]) is not bool
                    or type(row["score"]) not in (float, int) or not math.isfinite(row["score"])
                    or not isinstance(row["matched_terms"], list) or any(not isinstance(s, str) for s in row["matched_terms"])):
                raise ValueError("invalid native catalog row")
    names = report.get("names")
    if names is not None:
        if (not isinstance(names, dict) or set(names) != {"allowed_speech_labels", "speech_name_corrections", "speech_whitelist_enforce"}
                or not isinstance(names["allowed_speech_labels"], list) or any(not isinstance(s, str) for s in names["allowed_speech_labels"])
                or not isinstance(names["speech_name_corrections"], dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in names["speech_name_corrections"].items())
                or names["speech_whitelist_enforce"] is not True):
            raise ValueError("invalid native speech names")
    grounding, fixed = grounding_result({k: report[k] for k in ("grounding", "fixed_reply")})
    policy = report["topics"]
    if not isinstance(policy, dict) or set(policy) != {
        "usable_indices", "reply_stance", "reply_policy", "topic_for_identify_indices", "identify_skeleton"
    }:
        raise ValueError("invalid native topic policy")
    for key in ("usable_indices", "topic_for_identify_indices"):
        indices = policy[key]
        if (not isinstance(indices, list) or any(type(i) is not int or not 0 <= i < len(topic_hits) for i in indices)
                or len(set(indices)) != len(indices)):
            raise ValueError("invalid native topic indices")
    if (policy["reply_stance"] not in {"saw", "hypothesis", "clarify", "none"}
            or not isinstance(policy["reply_policy"], str)
            or policy["identify_skeleton"] is not None and not isinstance(policy["identify_skeleton"], str)):
        raise ValueError("invalid native topic text")
    return grounding, fixed, {
        **({"names": names} if names is not None else {}),
        "raw_topic_hits": topic_hits,
        "usable_topic_hits": [topic_hits[i] for i in policy["usable_indices"]],
        "topic_for_identify": [topic_hits[i] for i in policy["topic_for_identify_indices"]],
        **({"catalog_topic_hints": report["catalog_topic_hints"]} if "catalog_topic_hints" in report else {}),
        **{k: policy[k] for k in ("reply_stance", "reply_policy", "identify_skeleton")},
    }
