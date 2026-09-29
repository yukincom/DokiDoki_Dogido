"""Rustの検証済み入力投影を、既存のdataclassへ型変換する。再分類しない。"""
from dataclasses import fields
from datetime import datetime
import logging
import unicodedata

from dogido_server.knowledge_query import ExplicitKnowledgeQuery
from dogido_server.player_input.types import HaikuRecallQuery, PlayerInputContext, ReadingCorrection

BOOL_FIELDS = (
    "breaks_silence", "wants_quiet", "should_block_ambient", "asks_hostile_count",
    "asks_hostile_direction", "asks_dragon_direction", "asks_save_last_haiku",
    "asks_inventory", "requests_sword", "asks_about_sound", "asks_haiku_recall",
)
TEXT_FIELDS = ("raw_text", "normalized_text", "interpreted_text", "assist_intent_source", "assist_intent_evidence")
OPTIONAL_TEXT_FIELDS = ("player_haiku_text", "revised_haiku_text", "haiku_recall_biome_hint")


def _require(valid, detail="prepared context has invalid types"):
    if not valid:
        raise ValueError(detail)


def _optional_text(value):
    return value is None or isinstance(value, str)


def _reading(value):
    if value is None:
        return None
    _require(isinstance(value, dict) and set(value) == {f.name for f in fields(ReadingCorrection)})
    _require(all(isinstance(value[k], str) and value[k] for k in ("surface", "reading")))
    _require(_optional_text(value["wrong_reading"]) and type(value["explicit"]) is bool)
    _require(value["wrong_reading"] is None or value["explicit"])
    return ReadingCorrection(**value)


def _recall(value):
    if value is None:
        return None
    _require(isinstance(value, dict) and set(value) == {f.name for f in fields(HaikuRecallQuery)})
    converted = value.copy()
    for key in ("biome_id", "place_label", "time_label"):
        _require(_optional_text(value[key]))
    for key in ("biome_ids", "group_ids"):
        _require(isinstance(value[key], list) and all(isinstance(s, str) for s in value[key]))
        converted[key] = tuple(value[key])
    for key in ("since", "until"):
        _require(_optional_text(value[key]))
        converted[key] = datetime.fromisoformat(value[key]) if value[key] is not None else None
        _require(converted[key] is None or converted[key].tzinfo is not None)
    return HaikuRecallQuery(**converted)


def prepared_context(data):
    prepared = data.get("prepared_input")
    _require(isinstance(prepared, dict) and prepared.get("raw_text") == data["text"]
             and isinstance(prepared.get("normalized_text"), str),
             "prepared input does not match current text")
    value = data.get("prepared_context")
    _require(isinstance(value, dict) and set(value) == {f.name for f in fields(PlayerInputContext)})
    _require(all(type(value[key]) is bool for key in BOOL_FIELDS))
    _require(all(isinstance(value[key], str) for key in TEXT_FIELDS))
    _require(all(_optional_text(value[key]) for key in OPTIONAL_TEXT_FIELDS))
    spoken = prepared["normalized_text"] or data["text"]
    _require(value["normalized_text"] == prepared["normalized_text"]
             and value["raw_text"] == spoken and value["interpreted_text"] == spoken,
             "prepared context does not match current input")
    sword = value["requests_sword"]
    _require(value["assist_intent_source"] == ("code" if sword else "none")
             and value["assist_intent_evidence"] == (prepared["normalized_text"] if sword else "")
             and type(value["assist_intent_confidence"]) in (int, float)
             and value["assist_intent_confidence"] == (1.0 if sword else 0.0))
    query = value["knowledge_query"]
    _require(query == data.get("prepared_knowledge_query"), "prepared knowledge query does not match current input")
    if query is not None:
        evidence = unicodedata.normalize("NFKC", spoken).replace("～", "~").replace("〜", "~").strip()
        _require(not sword and isinstance(query, dict)
                 and set(query) == {"domain", "subject", "intent", "evidence"}
                 and all(isinstance(v, str) and 0 < len(v) <= 240 for v in query.values())
                 and query["evidence"] == evidence
                 and query["domain"] in {"japanese_language", "poetry", "minecraft"}
                 and query["intent"] in {"definition", "rules", "reading", "grade", "identifier", "change", "properties", "classification"},
                 "prepared knowledge query does not match current input")
        query = ExplicitKnowledgeQuery(**query)
    converted = value.copy()
    converted["knowledge_query"] = query
    converted["reading_correction"] = _reading(value["reading_correction"])
    converted["haiku_recall_query"] = _recall(value["haiku_recall_query"])
    _require(value["asks_haiku_recall"] == (converted["haiku_recall_query"] is not None))
    if prepared.get("applied_fixes"):
        logging.getLogger("uvicorn.error").warning(
            "asr_fix applied=%s original=%s fixed=%s",
            ",".join(f"{a}->{b}" for a,b in prepared["applied_fixes"]),
            data["text"][:80], prepared["normalized_text"][:80])
    return PlayerInputContext(**converted)
