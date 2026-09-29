"""Rustから届く原文と正規化面を、残る読み取り用parserへ渡す。"""
import logging
import unicodedata
from dogido_server.knowledge_query import ExplicitKnowledgeQuery
from dogido_server.player_input.routing import route_prepared_player_input


def prepared_context(data):
    prepared = data["prepared_input"]
    if (not isinstance(prepared, dict) or prepared.get("raw_text") != data["text"]
            or not isinstance(prepared.get("normalized_text"), str)):
        raise ValueError("prepared input does not match current text")
    # 既知誤変換の診断を維持。再び補正を実行して適用一覧を作らない。
    if prepared.get("applied_fixes"):
        logging.getLogger("uvicorn.error").warning(
            "asr_fix applied=%s original=%s fixed=%s",
            ",".join(f"{a}->{b}" for a,b in prepared["applied_fixes"]),
            data["text"][:80], prepared["normalized_text"][:80])
    value = data["prepared_knowledge_query"]
    query = None
    if value is not None:
        evidence = unicodedata.normalize("NFKC", prepared["normalized_text"]).replace("～", "~").replace("〜", "~").strip()
        if (not isinstance(value, dict) or set(value) != {"domain", "subject", "intent", "evidence"}
                or any(not isinstance(v, str) or not v or len(v) > 240 for v in value.values())
                or value["evidence"] != evidence
                or value["domain"] not in {"japanese_language", "poetry", "minecraft"}
                or value["intent"] not in {"definition", "rules", "reading", "grade", "identifier", "change", "properties", "classification"}):
            raise ValueError("prepared knowledge query does not match current input")
        query = ExplicitKnowledgeQuery(**value)
    return route_prepared_player_input(data["text"], prepared["normalized_text"],
                                       knowledge_query_extractor=lambda _: query)
