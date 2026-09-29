#!/usr/bin/env python3
"""移行途中の通常会話の純粋な補助。HTTP、音声、保存、game-event判断は実行しない。

現在snapshotとRust所有の履歴を読み、既存の会話材料を投影する。
通常本文のprompt・候補検査・最大一回の再考はRustが所有する。
narrationの最終安全網を保持し、最終本文と自由文の読みはRust側で検査・整形する。
plannerと本文の実モデル生成はstdio越しにRustへ要求する。一入力ごとに終了する。
"""
import json
import inspect
import logging
from dataclasses import asdict
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.config import Settings
from dogido_server.llm.client import DogidoLLM
from dogido_server.llm.types import LeafGenerationRequest, StructuredGenerationRequest
from dogido_server.llm.prompts import build_messages
try:
    from dogido_server.llm.types import GeneratedText
except ImportError:
    class GeneratedText(str):
        """終了情報の型を持たない旧Python leafにも文字列として渡せる。"""
        def __new__(cls, text, *, finish_reason=None, completion_tokens=None, prompt_tokens=None):
            value = super().__new__(cls, text)
            value.finish_reason = finish_reason
            value.completion_tokens = completion_tokens
            value.prompt_tokens = prompt_tokens
            return value
from dogido_server.models import GameEvent
from input_helper import prepared_context
from chat_validation_helper import leaf_input
from chat_grounding_helper import grounding_input, grounding_result, topics_input, topics_result
from dogido_server.state_machine import DogidoStateMachine
from tts_shared_tokens import handle as shared_tts_tokens
from reading_overlay import apply_reading_snapshot


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def emit_result(value):
    """Send raw final text, then serve at most one host-requested token lookup.

    EOF is the host's normal no-dictionary path. This process keeps the same
    optional dictionary singleton used by all earlier work during this turn.
    """
    if (not isinstance(value, dict) or value.get("op") != "result"
            or not isinstance(value.get("text"), str) or "spoken_text" in value):
        raise ValueError("final reading requires raw result text")
    emit(value)
    line = sys.stdin.readline(1_000_001)
    if not line:
        return
    if len(line.encode("utf-8")) > 1_000_000 or not line.endswith("\n"):
        raise ValueError("token request frame too large or incomplete")
    request = json.loads(line)
    if (not isinstance(request, dict) or request.get("op") != "tts_tokens"
            or request.get("text") != value["text"].strip()):
        raise ValueError("token request must match final raw text")
    emit(shared_tts_tokens(request))


def exchange(value):
    emit(value)
    line = sys.stdin.readline()
    if not line:
        raise EOFError("Rust host closed")
    reply = json.loads(line)
    if reply.get("error"):
        raise RuntimeError(reply["error"])
    return reply


COMBAT_LEAVES = frozenset({"death", "aftermath", "daylight_water_skeleton", "newly_burning_visual",
                           "deep_dark_ominous_sound", "occluded_hostile_presence"})
ENVIRONMENT_LEAVES = frozenset({"ambient", "weather_transition", "ender_eye_throw", "structure_entry", "light_source_gain",
    "darkness_escape", "occluded_entry_with_light", "occluded_entry_no_light", "dark_push_no_light",
    "dark_push_after_breath", "emergency_shelter_relief", "portal_appearance"})


class BridgeLLM(DogidoLLM):
    def __init__(self, settings, model, allowed_leaf="player_chat"):
        super().__init__(settings)
        self.model = model
        self.allowed_leaf = allowed_leaf

    def generate_structured_json(self, request):
        if request.kind != "player_chat_plan":
            raise ValueError("helper only supports player_chat_plan")
        # challengeはfallback focusにだけ依存させず、現行コードの解析結果をそのまま渡す。
        from dogido_server.dialogue.player_chat_planner import _fallback_plan
        hints = request.details["routing_hints"]
        options = {"inventory_question": hints["inventory_question"], "sound_question": hints["sound_question"]}
        if "history" in inspect.signature(_fallback_plan).parameters:
            options["history"] = request.details["history"]
        fallback = asdict(_fallback_plan(request.details["current"]["text"], **options))
        fallback.setdefault("presence_challenged", False)
        report = exchange({"op": "plan", "input": {"schema_version": 1, "model": self.model,
            "enable_thinking": False, "details": request.details, "fallback": fallback}})
        plan = report["plan"]
        if plan["source"] != "model":
            return dict(request.fallback_value, __dogido_status="disabled")
        payload = {k: plan[k] for k in ("action", "focus", "entity_query", "evidence", "confidence")}
        payload["repair"] = ({k: plan["repair"][k] for k in
            ("target_turn_id", "target_quote", "signal_quote", "replacement_quote")}
            if plan.get("repair") else None)
        payload["__dogido_status"] = "accepted"
        return payload

    def ground_player_chat(self, plan, *, topic_hits, observed_entities):
        if self.allowed_leaf != "player_chat":
            raise ValueError("unexpected grounding operation")
        report = exchange({"op": "chat_ground", "input": grounding_input(
            plan, topic_hits=topic_hits, observed_entities=observed_entities)})
        return grounding_result(report)

    def prepare_player_chat_topics(self, plan, **projection):
        if self.allowed_leaf != "player_chat":
            raise ValueError("unexpected topic operation")
        report = exchange({"op": "chat_ground", "input": topics_input(plan, **projection)})
        return topics_result(report, projection["topic_hits"])

    def generate_leaf_text(self, request):
        if request.kind != "player_chat":
            return super().generate_leaf_text(request)
        if self.allowed_leaf != "player_chat":
            raise ValueError("unexpected helper leaf")
        # Rust owns cleaning, observation-name correction, acceptance and one repair.
        # narration retains its final safety pass and authoritative fallback.
        response = exchange({"op": "chat_leaf", "input": leaf_input(
            request, self.model, self.settings.llm_max_tokens)})
        text = response.get("text")
        if not isinstance(text, str):
            raise ValueError("invalid native chat result")
        return text

    def _generate_backend_text(self, request):
        if request.kind != self.allowed_leaf:
            raise ValueError("unexpected helper leaf")
        if request.kind == "player_chat":
            raise ValueError("player_chat generation belongs to Rust")
        response = exchange({"op": "generate", "input": {"schema_version": 1, "kind": request.kind,
            "model": self.model, "messages": build_messages(request), "temperature": request.temperature,
            "max_tokens": request.max_tokens or self.settings.llm_max_tokens, "enable_thinking": False}})
        return GeneratedText(**response["generated"])


def run_turn(data):
    if isinstance(data.get("address_reply"), str):
        emit_result({"op":"result", "text":data["address_reply"]})
        return
    settings = Settings(_env_file=None, llm_enabled=True, llm_backend="chat_completions",
        llm_provider="local", llm_model=data["model"], llm_max_tokens=data["max_tokens"],
        audio_enabled=False, memory_enabled=False, decision_policy="legacy",
        tts_reading_engine=data.get("reading_engine", "auto"))
    context = prepared_context(data)
    # この補助の固定ASR置換を、保存・操作の原文や会話の意味解釈へ混ぜない。
    context.raw_text = data["text"]
    context.interpreted_text = data["text"]
    if context.asks_haiku_recall and not (context.asks_save_last_haiku or context.revised_haiku_text or context.player_haiku_text):
        emit({"op": "result", "memory_query_requested": True})
        return
    if any((context.requests_sword, context.asks_save_last_haiku,
            context.revised_haiku_text, context.player_haiku_text,
            context.asks_hostile_count, context.asks_hostile_direction, context.asks_dragon_direction)):
        emit({"op": "result", "unsupported": "通常会話の試験中です。その操作・川柳の機能はまだ接続していません。"})
        return
    if context.wants_quiet:
        emit_result({"op": "result", "text": "", "repair": None})
        return
    if context.knowledge_query is not None:
        if data.get("workshop_fallback"):
            # workshop内の先行routingは別の移植単位。unrelated扱いでpinを流さない。
            emit({"op": "result", "unsupported": "句の相談中の知識検索はまだ接続していません。"})
            return
        plan = exchange({"op": "knowledge", "request_text": data["text"], "query": asdict(context.knowledge_query)})
        emit_result({"op": "result", "text": plan["text"]})
        return
    from language_helper import run as run_language
    if (not data.get("host_chat_confirmed") and not data.get("workshop_fallback") and not data.get("workshop")
            and not context.asks_inventory and not context.normalized_text.startswith("/")
            and data["language_requested"]):
        outcome = run_language(exchange)
        if outcome["status"] != "host_chat":
            emit_result({"op":"result", "text":outcome["text"]})
            return
    event = GameEvent.model_validate(data["event"])
    llm = BridgeLLM(settings, data["model"])
    machine = DogidoStateMachine(settings, llm=llm)
    machine.player_input = context
    history = data["history"]
    machine.dialogue_context_provider = lambda: SimpleNamespace(
        prompt_turns=lambda: history, prompt_blocks=lambda: {
            "conversation_history": data["conversation_history"], "event_digest": str(data.get("event_digest", ""))})
    # process(event)を呼ばない。戦闘判断・発句・世界操作・記憶はこの補助の対象外。
    text = machine._render_player_chat_reply(event)
    emit_result({"op": "result", "text": text,
          "repair": asdict(machine.player_chat_repair) if machine.player_chat_repair else None})


def run_combat_leaf(data):
    """Rustで確定した出来事のprompt・発話検査だけ。観測、状態機械、保存を進めない。"""
    kind = data["kind"]
    if kind not in COMBAT_LEAVES | ENVIRONMENT_LEAVES:
        raise ValueError("unsupported combat leaf")
    settings = Settings(_env_file=None, llm_enabled=True, llm_backend="chat_completions",
        llm_provider="local", llm_model=data["model"], llm_max_tokens=data["max_tokens"],
        audio_enabled=False, memory_enabled=False, tts_reading_engine=data.get("reading_engine", "auto"))
    details = dict(data["details"])
    details.pop("__ambient_guard", None)
    suffix = str(details.pop("__speech_suffix", ""))
    fallback = data["fallback_text"]
    if suffix and fallback.endswith(suffix):
        fallback = fallback[:-len(suffix)].rstrip()
    llm = BridgeLLM(settings, data["model"], allowed_leaf=kind)
    text = llm.generate_leaf_text(LeafGenerationRequest(kind=kind, fallback_text=fallback,
        details=details, temperature=data["temperature"], route="chat"))
    if kind == "aftermath" and DogidoStateMachine._aftermath_claim_conflicts(
            str(details.get("combat_outcome", "disengaged")), text):
        text = fallback
    if kind == "light_source_gain":
        # 所持数増加からクラフト・設置・本数を補作しない既存の最終検査。
        machine = DogidoStateMachine(settings, llm=llm)
        if machine._invalid_light_source_gain_claim(text):
            text = fallback
    text += suffix
    emit_result({"op": "result", "text": text})


def run_light_plan(data):
    """所持数変化への一言の必要性。状態・発話・操作はRust側に残す。"""
    settings = Settings(_env_file=None, llm_enabled=True, llm_backend="chat_completions",
        llm_provider="local", llm_model=data["model"], llm_max_tokens=160,
        audio_enabled=False, memory_enabled=False)
    llm = BridgeLLM(settings, data["model"], allowed_leaf="light_source_comment_plan")
    payload = DogidoLLM.generate_structured_json(llm, StructuredGenerationRequest(
        kind="light_source_comment_plan", details=data["details"],
        fallback_value=data["fallback_payload"], temperature=0.0, route="chat", max_tokens=160))
    emit({"op": "result", "payload": payload})


def run_address_route(data):
    # 保留の確認が、知識・所持品・保存等の既存入力を横取りしない。
    # 入力parserだけを使い、モデル・状態機械・DB検索は実行しない。
    c = prepared_context(data)
    general = bool(c.semantic_text.strip()) and not c.normalized_text.startswith("/") and not any((
        c.wants_quiet, c.asks_hostile_count, c.asks_hostile_direction, c.asks_dragon_direction,
        c.asks_save_last_haiku, c.asks_inventory, c.requests_sword, c.knowledge_query is not None,
        c.player_haiku_text is not None, c.revised_haiku_text is not None,
        c.reading_correction is not None, c.asks_haiku_recall))
    emit({"op":"result", "general_conversation":general})


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="[会話補助] %(message)s")
    try:
        data = json.loads(sys.stdin.readline())
        apply_reading_snapshot(data.get("reading_corrections", []))
        {"combat_leaf": run_combat_leaf, "light_plan": run_light_plan,
         "address_route": run_address_route}.get(data.get("op"), run_turn)(data)
    except Exception as exc:
        emit({"op": "error", "error": f"{type(exc).__name__}: {exc}"})
        raise SystemExit(1)
