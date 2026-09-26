#!/usr/bin/env python3
"""共同編集移植中の純粋なprompt・契約・発話検査・読み補助。

モデル、HTTP、音声、保存、sessionの変更は行わない。各要求はRustの
現在句snapshotから独立して組み立て、旧serviceや状態機械は起動しない。
"""
from dataclasses import asdict
from datetime import datetime
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.haiku.workshop import (RecentHaikuWorkshop, PlayerLineReplacement, build_player_line_revision, _explicit_workshop_line_indices)
from dogido_server.haiku.workshop_agent import (build_workshop_agent_details, finalize_workshop_agent_step,
                                               _state_change_evidence_is_safe)
from dogido_server.haiku.workshop_context import workshop_context_block, workshop_context_details
from dogido_server.haiku.source_atoms import source_atoms_from_materials, line_source_ids_from_materials
from haiku_helper import handle as haiku_handle
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


def explicit_player_edit(text, evidence):
    # The legacy extraction only verifies that replacement/evidence occur in the
    # utterance. That also holds for "にしないで"; check the act of editing too.
    if not _state_change_evidence_is_safe("stage_player_edit", player_text=text, evidence=evidence):
        return False
    outside_quotes = re.sub(r"「[^「」]*」|『[^『』]*』", "候補", text)
    if re.search(r"(?:に|へ)(?:しない|しなく|するな|せん|せえへん|変えない|変えるな|変えん|変えへん)|"
                 r"(?:置き換え|変更し|修正し|直さ)(?:ない|ん|へん)|"
                 r"(?:に|へ)(?:したら|するなら|変えたら)|もし|(?:とは|わけでは|わけじゃ).{0,12}(?:ない|へん)", outside_quotes):
        return False
    return bool(re.search(r"(?:に|へ)(?:して|変えて|かえて|替えて|直して|置き換えて|変更して)|"
                          r"(?:に|へ)(?:変えた|した)(?:方|ほう)が(?:いい|ええ|良い|よい)|"
                          r"(?:の方|のほう)が(?:いい|ええ|良い|よい)|で(?:いい|ええ|良い|よい)", outside_quotes))


def explicit_repair(text, evidence):
    if not _state_change_evidence_is_safe("stage_player_edit", player_text=text, evidence=evidence):
        return False
    outside = re.sub(r"「[^「」]*」|『[^『』]*』", "対象", text)
    if re.search(r"直(?:さない|さん|さへん|すな)|(?:修正|変更|改稿)(?:は|を)?(?:しない|せん|せえへん|するな)|変え(?:ない|ん|へん|るな)|直す(?:必要はない|つもりはない)", outside):
        return False
    return bool(re.search(r"直して|直そう|直し(?:て|たい|案)|修正(?:して|しよう|案)|書き直|言い換え|別の(?:表現|言い方)|変えて|改稿", evidence))


def consultation_messages(details):
    """相談に使わない編集用空欄を生成させない。文脈と既存の根拠検証は維持する。"""
    editing = "stage_player_edit" in details["allowed_actions"]
    pending = bool(details.get("pending_verse"))
    extra = ""
    if editing:
        extra += (
            "stage_player_edit=発話で指定された一行の置換を検査へ渡す。purposeはimprove_wording。"
            "発話にない置換語を考えない。ドギドに修正案を求めている場合はpropose_revisionを使う。"
            "このactionだけline_referenceとline_proposalを追加する。\n"
            'line_reference: {"found":true,"concept_id":"line_1","evidence":"上五","confidence":0.95}。'
            '概念はline_1=上五、line_2=中七、line_3=下五。行が不明ならfound:false,concept_id:"unknown"。\n'
            'line_proposal: {"found":true,"target_fragment":"","replacement_text":"発話にある置換語",'
            '"evidence":"置換依頼の連続部分","confidence":0.95}。行中の語で対象を指定されたらtarget_fragmentに抜く。\n'
        )
    if "propose_revision" in details["allowed_actions"]:
        extra += (
            "propose_revision=明示された修正依頼をコードの生成・検査へ渡す。purposeはimprove_wording。"
            "このactionは必須6キーとfindingsだけを返す。speechは空文字列。"
            "line_referenceとline_proposalは返さない。ここでは新しい句本文を考えない。"
            "対象行は現在句に一意にある断片で特定する。"
            "行名だけで頼まれたら、その行の本文をfragmentへ入れる。不明ならask。"
            'findings: [{"line_index":0,"fragment":"対象行中の断片","problem":"unnatural_japanese",'
            '"note":"今回の修正意図","confidence":0.95}]。'
            f"problemは{', '.join(details['allowed_problem_types'])}から選ぶ。"
            "否定・引用・伝聞・条件・疑問を修正の許可にしない。検査後は同じターンに再生成・採用しない。\n"
        )
    if pending:
        extra += (
            "未採用案がある。compare=元句と案の比較、accept_pending=案の明示採用、reject_pending=案の明示却下。"
            "採用purposeはadopt_pending、却下はdiscard_pending。採否と明示終了を同時に頼まれた場合だけ"
            'close_after_action:true,close_evidence:"終了意思の原文"を追加する。'
            "採否が不明な終了要求はaskで採否を確認する。\n"
        )
    if details["phase"] == "after_validation":
        extra += ("検査後の返答は、コードの検査結果への短い一言だけにする。"
            "句本文と採用案内はコードが付けるのでspeechへ入れない。別の句・表記・修正候補を追加提案しない。"
            "案がproposedならrespondで案の差分を説明するかshow_current。rejectedならaskで意図を確かめる。\n")
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
        "respond/explain/ask/compareだけspeechへ自然な関西弁一文を120字以内で入れる。"
        "ほかのactionのspeechは空。inspect以外のchecksは空配列。"
        "confidenceは今回の確信度0〜1。会話は0.72以上、編集・採用・却下・終了は0.85以上。"
        "evidenceは今回の発話の連続部分を原文通り抜く。"
        "終了は疑問・引用・伝聞・条件・否定では選ばず、以前の同意も根拠にしない。"
        "判断できなければaskで確認する。終了のpurposeはfinish_workshop。\n"
        f"{extra}段階: {details['phase']}\n"
        f"今回のプレイヤー発話（会話理解用）: {details['player_text']}\n"
        f"このターンの実行済み一手: {json.dumps(details['turn_steps'], ensure_ascii=False)}\n"
        f"コードから返った実検査結果: {json.dumps(details['tool_observation'], ensure_ascii=False)}\n"
        f"{workshop_context_block(details)}"
        f"許可action: {', '.join(details['allowed_actions'])}\n"
        f"許可purpose: {', '.join(details['allowed_purposes'])}\n"
        'JSONはaction,purpose,confidence,evidence,speech,checksの6キーを必須とし、上記action固有項目だけ追加。'
        '例: {"action":"ask","purpose":"continue_discussion","confidence":0.8,'
        '"evidence":"発話の連続部分","speech":"短い質問","checks":[]}。説明文・思考文は禁止。'
    )
    return [{"role": "system", "content": "あなたは川柳の共同編集者ドギド。今は一句について許可された一手だけを選ぶ。指定JSONだけを返す。"},
            {"role": "user", "content": prompt}]


def snapshot_for(frame):
    view = frame["workshop"]
    e = view["emission"]
    current = view.get("current_lines", e["lines"])
    workshop = RecentHaikuWorkshop(
        surface_text="\n".join(l["reading_text"] for l in current), emitted_at=datetime.fromisoformat(e["created_at"]),
        current_lines=tuple(HaikuLine(**row) for row in current),
        interpretation=e.get("interpretation"), materials=view["materials"],
        agent_steps=view.get("agent_steps", [])[-12:],
    )
    pending = view.get("pending")
    if pending:
        workshop.pending_revision_lines = tuple(HaikuLine(**row) for row in pending["lines"])
        workshop.pending_revision = "\n".join(l["reading_text"] for l in pending["lines"])
        workshop.pending_revision_surface_text = "\n".join(l["surface_text"] for l in pending["lines"])
        workshop.pending_revision_base_text = "\n".join(l["reading_text"] for l in pending["base"])
        workshop.pending_revision_source = "generated_confirmed" if pending.get("generated_basis") else "player_line_confirmed"
    for pair in view.get("dialogue", [])[-4:]:
        workshop.dialogue.add_player(pair["player_text"], turn_id=pair["turn_id"])
        workshop.dialogue.add_dogido(pair["dogido_text"], turn_id=pair["turn_id"])
    return workshop


def details_for(frame):
    workshop = snapshot_for(frame)
    details = build_workshop_agent_details(
        workshop, frame["text"], original_player_text=frame["text"],
        phase=frame["phase"], observation=frame.get("observation"),
        turn_steps=frame.get("turn_steps", []),
    )
    # 実行できる一手はRustが渡す。段階とpendingに応じて一手を制限する。
    details["allowed_actions"] = [a for a in details["allowed_actions"] if a in frame["allowed_actions"]]
    return details


def handle(frame):
    if frame["op"] in {"prepare", "transform"} and "request" in frame:
        return haiku_handle(frame)
    if frame["op"] == "revision_input":
        workshop = snapshot_for(frame)
        lines = [l.reading_text for l in workshop.current_lines]
        atoms = source_atoms_from_materials(workshop.materials)
        findings = frame["findings"]
        targets = sorted({f["line_index"] for f in findings if type(f.get("line_index")) is int and f["line_index"] in (0, 1, 2)})
        return {"lines": lines, "line_sources": line_source_ids_from_materials(workshop.materials, verse_lines=lines, allowed_atom_ids={a.atom_id for a in atoms}),
            "findings": findings, "basis": {"target_indices": targets, "source_atoms": [asdict(a) for a in atoms],
            "details": {**workshop.materials, "workshop_context": workshop_context_details(workshop)}},
            "max_tokens": frame["max_tokens"], "grounding_max_tokens": frame["grounding_max_tokens"]}
    if frame["op"] == "reading":
        return {"spoken_text": prepare_text_for_tts(frame["text"], engine=frame.get("reading_engine", "auto"))}
    if frame["op"] == "player_edit":
        # Replacement is from a previously validated extraction. This pure helper
        # keeps the existing dictionary/hard-rule behavior; Rust owns CAS and state.
        proposal = frame["proposal"]
        result = build_player_line_revision(snapshot_for(frame), PlayerLineReplacement(
            text=proposal["replacement_text"], explicit_line_index=proposal.get("line_index"),
            target_fragment=proposal.get("target_fragment") or None))
        return asdict(result)
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
        # 省略された項目は非操作の値で補う。編集には実在するline_proposalが必須で、
        # 必須6項目・未知キー・採否/終了の根拠は既存契約でも検査する。
        if isinstance(payload, dict):
            payload = {**INACTIVE_FIELDS, **payload}
        contract = validate_structured_payload(KIND, payload, details=details)
        if not contract.accepted:
            return {"contract_errors": list(contract.errors), "step": None, "reason": "schema_contract_error"}
        step, reason = finalize_workshop_agent_step(payload, details=details)
        if step and step.action == "stage_player_edit" and not explicit_player_edit(frame["text"], step.evidence):
            step, reason = None, "player_edit_intent_not_explicit"
        if step and step.action == "propose_revision":
            if step.confidence < .85 or not explicit_repair(frame["text"], step.evidence):
                step, reason = None, "repair_intent_not_explicit"
            if step:
                explicit = _explicit_workshop_line_indices(frame["text"])
                targets = {f.line_index for f in step.analysis.findings if f.line_index is not None}
                if explicit and not targets.issubset(explicit):
                    step, reason = None, "repair_target_conflict"
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
