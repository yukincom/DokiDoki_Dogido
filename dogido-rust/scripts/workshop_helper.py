#!/usr/bin/env python3
"""共同編集移植中の純粋なprompt・契約・発話検査・読み補助。

モデル、HTTP、音声、保存、sessionの変更は行わない。各要求はRustの
現在句snapshotから独立して組み立て、旧serviceや状態機械は起動しない。
"""
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.haiku.workshop import RecentHaikuWorkshop
from dogido_server.haiku.workshop_agent import build_workshop_agent_details, finalize_workshop_agent_step
from dogido_server.haiku.workshop_context import workshop_context_block
from dogido_server.llm.structured_contracts import validate_structured_payload
from dogido_server.memory_types import HaikuLine
from dogido_server.tts_reading import prepare_text_for_tts

KIND = "haiku_workshop_agent_step"
CONSULTATION_KEYS = {"action", "purpose", "confidence", "evidence", "speech", "checks"}
INACTIVE_FIELDS = {
    "close_after_action": False, "close_evidence": "", "findings": [],
    "line_reference": {"found": False, "concept_id": "unknown", "evidence": "", "confidence": 0.0},
    "line_proposal": {"found": False, "target_fragment": "", "replacement_text": "", "evidence": "", "confidence": 0.0},
}


def consultation_messages(details):
    """相談に使わない編集用空欄を生成させない。文脈と既存の根拠検証は維持する。"""
    prompt = (
        "現在の一句について、相談の次の一手を一つだけJSONで返す。\n"
        "action: respond=感想への返答、explain=句の意味の説明、ask=不足点を一つ質問、"
        "inspect=コードに読み/音数/出典を確認、show_current=三行をそのまま表示、"
        "close_workshop=相談全体の明確な終了意思、unrelated=句と無関係な話。\n"
        "句・未採用案・保存を変更する権限はない。未実行の修正・採用・保存を成功したと言わない。"
        "由来や意味は当時の材料・詩的解釈と比較して説明し、不明な対応を作らない。"
        "過去の自分の説明も取り違えうる。指摘されたら記録を再確認して答える。\n"
        "読み/音数/出典を尋ねられたら、実検査結果が無い項目をinspectにする。"
        "checksはreading/meter/sourceから必要なものだけ。検査後はその結果を説明する。"
        "検査前に読み・音数・出典記録を断言しない。\n"
        "respond/explain/askだけspeechへ自然な関西弁一文を120字以内で入れる。"
        "ほかのactionのspeechは空。inspect以外のchecksは空配列。"
        "confidenceは今回の確信度0〜1。会話は0.72以上、終了は0.85以上。"
        "evidenceは今回の発話の連続部分を原文通り抜く。"
        "終了は疑問・引用・伝聞・条件・否定では選ばず、以前の同意も根拠にしない。"
        "判断できなければaskで確認する。終了のpurposeはfinish_workshop。\n"
        f"段階: {details['phase']}\n"
        f"今回のプレイヤー発話（会話理解用）: {details['player_text']}\n"
        f"このターンの実行済み一手: {json.dumps(details['turn_steps'], ensure_ascii=False)}\n"
        f"コードから返った実検査結果: {json.dumps(details['tool_observation'], ensure_ascii=False)}\n"
        f"{workshop_context_block(details)}"
        f"許可action: {', '.join(details['allowed_actions'])}\n"
        f"許可purpose: {', '.join(details['allowed_purposes'])}\n"
        'JSONはaction,purpose,confidence,evidence,speech,checksの6キーのみ。'
        '例: {"action":"ask","purpose":"continue_discussion","confidence":0.8,'
        '"evidence":"発話の連続部分","speech":"短い質問","checks":[]}。説明文・思考文は禁止。'
    )
    return [{"role": "system", "content": "あなたは川柳の共同編集者ドギド。今は一句を読む・相談する・終了する一手だけを選ぶ。指定JSONだけを返す。"},
            {"role": "user", "content": prompt}]


def details_for(frame):
    view = frame["workshop"]
    e = view["emission"]
    workshop = RecentHaikuWorkshop(
        surface_text=e["reading_text"], emitted_at=datetime.fromisoformat(e["created_at"]),
        current_lines=tuple(HaikuLine(**row) for row in e["lines"]),
        interpretation=e.get("interpretation"), materials=view["materials"],
        agent_steps=view.get("agent_steps", [])[-12:],
    )
    for pair in view.get("dialogue", [])[-4:]:
        workshop.dialogue.add_player(pair["player_text"], turn_id=pair["turn_id"])
        workshop.dialogue.add_dogido(pair["dogido_text"], turn_id=pair["turn_id"])
    details = build_workshop_agent_details(
        workshop, frame["text"], original_player_text=frame["text"],
        phase=frame["phase"], observation=frame.get("observation"),
        turn_steps=frame.get("turn_steps", []),
    )
    # 実行できる一手はRustが渡す。未移植の編集・採否を選ばせない。
    details["allowed_actions"] = [a for a in details["allowed_actions"] if a in frame["allowed_actions"]]
    return details


def handle(frame):
    if frame["op"] == "reading":
        return {"spoken_text": prepare_text_for_tts(frame["text"], engine=frame.get("reading_engine", "auto"))}
    details = details_for(frame)
    if frame["op"] == "prepare":
        messages = consultation_messages(details)
        retry = frame.get("retry")
        if retry:
            messages.append({"role": "user", "content":
                "前のJSONは契約違反でした。同じ発話について外形だけ直してください。\n"
                + json.dumps(retry, ensure_ascii=False)
                + "\naction,purpose,evidence,speechは文字列、confidenceは0〜1の数値、checksはreading/meter/sourceの重複なし配列。"
                + "この6キーをすべて含め、許可actionとpurposeを守る。"})
        return {"messages": messages}
    if frame["op"] == "validate":
        payload = frame["payload"]
        # 省略を許すのは相談で使わない5項目だけ。必須6項目や未知キーは既存契約で拒否する。
        if isinstance(payload, dict):
            payload = {**INACTIVE_FIELDS, **payload}
        contract = validate_structured_payload(KIND, payload, details=details)
        if not contract.accepted:
            return {"contract_errors": list(contract.errors), "step": None, "reason": "schema_contract_error"}
        step, reason = finalize_workshop_agent_step(payload, details=details)
        return {"contract_errors": [], "step": asdict(step) if step else None, "reason": reason}
    raise ValueError("unsupported workshop helper operation")


def main():
    for line in sys.stdin:
        try:
            if len(line.encode("utf-8")) > 1_000_000:
                raise ValueError("helper frame too large")
            output = handle(json.loads(line))
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), flush=True)
            return 1
        print(json.dumps(output, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
