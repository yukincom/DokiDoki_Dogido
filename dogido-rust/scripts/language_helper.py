"""国語対話の純粋補助。状態・モデル呼出・音声・Web・保存はRustが所有する。"""
from dataclasses import asdict
import unicodedata


def explicit_request(text):
    text = " ".join(text.split())
    if any(p in text for p in ("ってどういう意味", "って何て読む", "ってなんて読む", "の読み方",
                              "言葉の意味", "ことばの意味", "何年生で習", "何年生の漢字")):
        return True
    return any(p in text for p in ("漢字", "短歌", "俳句", "川柳", "音数", "文法", "熟語", "ことわざ", "枕詞", "語句")) and any(
        p in text for p in ("?", "？", "教えて", "知りたい", "調べて", "勉強", "習う", "習った", "話をしよう", "話しよう", "話そう"))


def parse(generated, model):
    from dogido_server.llm.client import DogidoLLM

    if generated.get("finish_reason") in {"length", "max_tokens", "MAX_TOKENS"}:
        return None
    # 既存JSON parser・厳密な外形を再利用。補完・再生成は行わない。
    payload = DogidoLLM.__new__(DogidoLLM)._extract_json_object(generated.get("text", ""))
    if not isinstance(payload, dict):
        return None
    payload.pop("__dogido_status", None)
    try:
        return model.model_validate(payload, strict=True)
    except (TypeError, ValueError):
        return None


def handle(command):
    op = command["command"]
    if op == "prompt":
        # 通常雑談の入口判定では、国語用コンポーネントを読み込まない。
        from dogido_server.language_dialogue.prompts import (
            build_interpretation_messages, build_grounded_reply_messages,
        )
        from dogido_server.llm.types import StructuredGenerationRequest

        kind = command["kind"]
        build = {"language_dialogue_interpretation": build_interpretation_messages,
                 "language_dialogue_reply": build_grounded_reply_messages}[kind]
        request = StructuredGenerationRequest(kind=kind, details=command["details"], fallback_value={}, route="chat")
        return {"stage":"prompt", "messages":build(request)}
    if op == "interpretation":
        from dogido_server.language_dialogue.contracts import Interpretation
        from dogido_server.language_dialogue.controller import (
            KANJI_GRADE_CONFIRMATION, looks_like_information_request, mentions_writing,
        )
        from dogido_server.language_dialogue.verified_answers import count_explicit_kana

        details = command["details"]
        current, history = details["current"], details["history"]
        interpretation = parse(command["generated"], Interpretation)
        output = {"stage":"interpretation", "payload":None,
                  "information_request":looks_like_information_request(current["text"]),
                  "invalid_normal_chat":False, "computed_fact":None}
        if interpretation is None:
            return output
        normalize = lambda t: unicodedata.normalize("NFKC", t)
        turns = {t["turn_id"]:t["text"] for t in history + [current]}
        evidence = interpretation.evidence
        valid = bool(evidence) and any(e.turn_id == current["turn_id"] for e in evidence)
        valid = valid and all(e.turn_id in turns and normalize(e.quote) in normalize(turns[e.turn_id]) for e in evidence)
        if interpretation.dialogue_act == "information_request" and interpretation.target_status == "contextual":
            known = bool(interpretation.target) and any(normalize(interpretation.target) in normalize(t["text"]) for t in history + [current])
            switching = interpretation.facet == "other" and interpretation.relation in {"switch", "end"}
            valid = valid and (known or switching or any(e.turn_id != current["turn_id"] for e in evidence))
        if not valid:
            output["invalid_normal_chat"] = interpretation.dialogue_act in {"casual", "other"}
            return output
        if interpretation.facet != "other":
            interpretation.topic = "language"
        if interpretation.facet == "grade":
            continued = interpretation.target_status == "contextual" and interpretation.relation in {"continue", "resume", "correct"} and (
                command["state"]["kanji_scope_confirmed"] or details["focus"]["clarification"] == KANJI_GRADE_CONFIRMATION or any(
                    e.turn_id != current["turn_id"] and mentions_writing(e.quote) for e in evidence))
            if not mentions_writing(current["text"]) and not continued:
                interpretation.target_status = "ambiguous"
                interpretation.clarification = KANJI_GRADE_CONFIRMATION
                interpretation.alternatives = ["漢字の配当学年", "漢字以外の学習"]
        if interpretation.facet == "mora_count" and interpretation.target_status != "ambiguous":
            output["computed_fact"] = count_explicit_kana(interpretation.target, [e.quote for e in evidence])
            if output["computed_fact"] is None:
                interpretation.target_status = "ambiguous"
                interpretation.clarification = "数えたい言葉の読みを、ひらがなかカタカナで教えてくれる？"
        output["payload"] = interpretation.model_dump()
        return output
    if op == "lookup":
        from dogido_server.language_dialogue.contracts import Interpretation
        from dogido_server.language_dialogue.retrieval import LocalDialogueSearch, SearchResult
        from dogido_server.language_dialogue.verified_answers import verified_reply

        i = Interpretation.model_validate(command["interpretation"], strict=True)
        computed = command.get("computed_fact")
        lookup = SearchResult([], [computed], "computed") if computed else LocalDialogueSearch().search(i.search_terms, facet=i.facet, target=i.target)
        text = command["text"]
        if i.facet == "comparison" and len(i.target) == 1 and text.count(i.target) >= 2:
            lookup.facts.append({"id":f"input-character:{ord(i.target):x}", "title_ja":"入力文字の比較",
                "text_ja":f"今回の入力に同一文字『{i.target}』が{text.count(i.target)}回ある。文字列比較の結果。",
                "claim_status":"input_character_comparison", "sources":[]})
        fixed = verified_reply(i, lookup.facts)
        return {"stage":"lookup", "lookup":asdict(lookup), "fixed_reply":fixed.model_dump() if fixed else None}
    if op == "reply":
        from dogido_server.language_dialogue.contracts import GroundedReply

        reply = parse(command["generated"], GroundedReply)
        return {"stage":"reply", "payload":reply.model_dump() if reply else None}
    raise ValueError("unsupported language helper command")


def run(exchange):
    command = exchange({"op":"language", "stage":"start"})
    while command["command"] != "done":
        command = exchange({"op":"language", **handle(command)})
    return command
