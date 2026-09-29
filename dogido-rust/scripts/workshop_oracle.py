#!/usr/bin/env python3
"""オフライン比較専用の正本Pythonアダプター。実行経路からは呼び出さない。

fixture生成・回帰検査に従来のprompt、検査、局所編集の契約を残す。
モデル、HTTP、音声、保存、sessionの変更は行わない。
"""
from dataclasses import asdict
from datetime import datetime
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.haiku.workshop import (RecentHaikuWorkshop, PlayerLineReplacement, build_player_line_revision, _explicit_workshop_line_indices,
    is_meaning_acknowledgement, close_confirmation_decision, combat_resume_confirmation_decision,
    parse_player_line_replacement, mentioned_workshop_line_fragment, grounded_material_for_question,
    mentioned_workshop_edit_fragment, explicit_workshop_line_index)
from dogido_server.haiku.workshop_agent import (build_workshop_agent_details, finalize_workshop_agent_step,
                                               _state_change_evidence_is_safe)
from dogido_server.haiku.workshop_context import workshop_context_block, workshop_context_details
from dogido_server.haiku.source_atoms import source_atoms_from_materials, line_source_ids_from_materials
from haiku_helper import handle as haiku_handle
from dogido_server.llm.structured_contracts import validate_structured_payload
from dogido_server.llm.character_mode import WORKSHOP_IDENTITY_PROMPT
from dogido_server.memory_types import HaikuLine
from dogido_server.haiku.verse import build_haiku_lines
from tts_shared_tokens import handle as shared_tts_tokens
from reading_overlay import apply_reading_snapshot

KIND = "haiku_workshop_agent_step"
FOLLOWUP_ACTIONS = {"acknowledge_meaning", "confirm_close", "continue_workshop", "resume_workshop", "decline_resume"}
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


def fixed_fragment_edit(frame, details):
    """一意な現行句の対象箇所と差し替え案が明示された編集だけ先に確定する。"""
    text = frame["text"]
    if details["phase"] != "decide" or "stage_player_edit" not in details["allowed_actions"]:
        return None
    if not explicit_player_edit(text, text) or len(_explicit_workshop_line_indices(text)) > 1:
        return None
    workshop = snapshot_for(frame)
    quoted_pairs = list(re.finditer(
        r"[「『](?P<target>[^」』]+)[」』]を[「『](?P<alternative>[^」』]+)[」』]に"
        r"(?:して|変えて|替えて|かえて|直して|置き換えて|変更して)", text,
    ))
    if quoted_pairs:
        if len(quoted_pairs) != 1:
            return None
        replacement = PlayerLineReplacement(
            text=quoted_pairs[0].group("alternative"),
            explicit_line_index=explicit_workshop_line_index(text),
        )
        fragment = quoted_pairs[0].group("target")
    else:
        parsed = parse_player_line_replacement(text)
        replacement = parsed.replacement
        if parsed.status != "accepted" or replacement is None:
            return None
        fragment = mentioned_workshop_edit_fragment(workshop, text, replacement.text)
    if not replacement.text or replacement.text not in text:
        return None
    if fragment is None:
        return None
    # 現在句中の対象箇所と発話中の差し替え案が一意な場合だけ。行呼称との衝突も検査する。
    revision = build_player_line_revision(workshop, PlayerLineReplacement(
        text=replacement.text, explicit_line_index=replacement.explicit_line_index,
        target_fragment=fragment))
    if revision.text is None:
        return None
    return {"action": "stage_player_edit", "purpose": "improve_wording", "confidence": 1.0,
        "evidence": text, "speech": "", "checks": [],
        "line_reference": INACTIVE_FIELDS["line_reference"],
        "line_proposal": {"found": True, "target_fragment": fragment,
            "replacement_text": replacement.text, "evidence": text, "confidence": 1.0}}


def discussion_candidate(frame, proposal):
    """Validate an idea mentioned in conversation without staging or saving it."""
    text = frame["text"]
    if re.search(r"(?:と|って)(?:言われた|聞いた|書いてあった|載っていた)", text):
        return {"candidate": None}
    if isinstance(proposal, dict) and proposal.get("replacement_text"):
        replacement = str(proposal.get("replacement_text") or "")
        fragment = str(proposal.get("target_fragment") or "")
        index = proposal.get("line_index")
        if not replacement or replacement not in text or type(index) not in (int, type(None)):
            return {"candidate": None}
    else:
        pairs = list(re.finditer(
            r"[「『](?P<target>[^」』]+)[」』]を[「『](?P<alternative>[^」』]+)[」』]に"
            r"(?:するのは|したら|変えるのは)(?:どう|どうかな)", text,
        ))
        if len(pairs) != 1 or len(_explicit_workshop_line_indices(text)) > 1:
            return {"candidate": None}
        replacement = pairs[0].group("alternative")
        fragment = pairs[0].group("target")
        index = explicit_workshop_line_index(text)
    result = build_player_line_revision(
        snapshot_for(frame),
        PlayerLineReplacement(replacement, explicit_line_index=index, target_fragment=fragment or None),
    )
    return {"candidate": {
        "proposal": {"line_index": result.target_line_index if result.target_line_index is not None else index,
                     "target_fragment": fragment, "replacement_text": replacement},
        "evidence": text, "validation_codes": list(result.failure_reasons),
    }}


def consultation_messages(details):
    """相談に使わない編集用空欄を生成させない。文脈と既存の根拠検証は維持する。"""
    editing = "stage_player_edit" in details["allowed_actions"]
    pending = bool(details.get("pending_verse"))
    extra = ""
    if details["original_player_text"] != details["player_text"]:
        extra += (f"今回のプレイヤー発話（認識原文）: {details['original_player_text']}\n"
            "会話理解用には現在の句や材料にある語だけの音近傍補正がある。"
            "編集・採否・終了のevidenceと置換語は必ず認識原文から取る。"
            "補正語だけを変更や保存の許可にしない。原文が曖昧ならaskで確認する。\n")
    if details.get("workshop_context", {}).get("current_player_idea"):
        extra += ("今回の発話は、一つの差し替え案について『どう？』と相談している。"
                  "対象箇所と差し替え案はcurrent_player_ideaにコードで抜き出し済み。"
                  "編集命令ではないのでrespond/ask/explainでその案に答える。今は変更・採用・保存をしない。"
                  "新しい句全体を書かず、指定された表現の違いを話す。\n")
    if "acknowledge_meaning" in details["allowed_actions"]:
        extra += ("直前に意味の説明を再生済み。今回の発話がその説明への納得だけならacknowledge_meaning、"
            "purposeはunderstand_meaning。評価・褒め言葉・新しい質問・修正要求とは区別する。\n")
    if "confirm_close" in details["allowed_actions"]:
        extra += ("『この句の話はここまででええ？』を再生済み。その問いへの今回の同意ならconfirm_close、"
            "purposeはfinish_workshop。まだ話したい返事ならcontinue_workshop、purposeはcontinue_discussion。"
            "案の採用とは区別する。新しい句の質問は通常のexplain/inspect等で答える。\n")
    if "resume_workshop" in details["allowed_actions"]:
        extra += ("戦闘後に同じ句を最後まで再掲済み。続ける同意はresume_workshop、purposeはcontinue_discussion。"
            "やめる意思はdecline_resume、purposeはfinish_workshop。新しい質問や編集依頼は通常の一手で処理する。"
            "未採用案の採用・破棄とは別。\n")
    if FOLLOWUP_ACTIONS.intersection(details["allowed_actions"]):
        extra += ("acknowledge_meaning/confirm_close/continue_workshop/resume_workshop/decline_resumeは6キーだけ、speechは空、checksは空配列、"
            "confidenceは0.85以上。原文の引用・否定・条件・伝聞・疑問を同意にしない。\n")
    if editing:
        extra += (
            "stage_player_edit=発話で指定された一行の置換を検査へ渡す。purposeはimprove_wording。"
            "発話にない差し替え案を考えない。ドギドに修正案を求めている場合はpropose_revisionを使う。"
            "stage_player_editではline_referenceとline_proposalを追加する。"
            "プレイヤーが表現案を相談しているだけならrespond/askで返し、"
            "その案の対象箇所と差し替え案が今回の発話に実在するときだけline_proposalも添える。"
            "この時点で句の編集や採用をしたと言わない。\n"
            'line_reference: {"found":true,"concept_id":"line_1","evidence":"上五","confidence":0.95}。'
            '概念はline_1=上五、line_2=中七、line_3=下五。行が不明ならfound:false,concept_id:"unknown"。\n'
            '専門的な行名を要求しない。「最初」「真ん中」「最後」や句本文を読む指定も同じ対象へ対応させる。'
            'target_fragmentは現在句中の対象箇所をそのまま抜く。発話で一行の一部分だけを指定したら、その連続部分だけをtarget_fragmentに、'
            '発話中の差し替え案だけをreplacement_textに入れる。全行の言い換えなら現在行全体をtarget_fragmentにする。'
            '今回すぐ差し替える指示はstage_player_edit、表現案への相談だけならrespond/ask、'
            '差し替え案をドギドに考えてほしい依頼だけpropose_revision。対象や聞き取れた表現が曖昧なら、'
            'その句本文を引用して一つだけ確認し、同じ一般的な質問を繰り返さない。\n'
            'line_proposal: {"found":true,"target_fragment":"","replacement_text":"発話にある差し替え案",'
            '"evidence":"置換依頼の連続部分","confidence":0.95}。行中の語で対象を指定されたらtarget_fragmentに抜く。\n'
        )
    if "stage_conversation_candidate" in details["allowed_actions"]:
        extra += (
            "conversation_candidateは、いま相談しているプレイヤーの一案。検査不合格でも相談用に保持する。"
            "まだ句にも未採用案にも反映していない。適用時に読み・音数・対象箇所を再検査する。今回の発話が明確にその案を使う指示なら"
            "stage_conversation_candidateを選び、purposeはimprove_wording、speechは空にする。"
            "新しい案の提案や単なる比較・質問では選ばない。今回の連続した発話をevidenceへ抜く。"
            "『やっぱりXXにして』のように差し替え表現だけ訂正されたらstage_player_edit。"
            "line_proposalには今回のXXを抜き、対象を指定していなければtarget_fragmentは空、line_referenceはfound:false。"
            "コードが相談中の一案の対象箇所を保持して検査する。別の箇所を指定された場合はその指定を優先する。\n"
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
        "プレイヤーが現在句の問題を具体的に指摘した場合、respond/explain/askにもfindingsを付けられる。"
        "findingsは今回の指摘と実在する句の断片だけ。問題を勝手に追加しない。"
        '外形は[{"line_index":0,"fragment":"句中の断片","problem":"unreadable","note":"今回の指摘","confidence":0.9}]。'
        f"problemは{', '.join(details['allowed_problem_types'])}から選ぶ。"
        "checksはreading/meter/sourceから必要なものだけ。検査後はその結果を説明する。"
        "検査前に読み・音数・出典記録を断言しない。\n"
        "respond/explain/ask/compareだけspeechへ自然な関西弁一文を120字以内で入れる。"
        "ほかのactionのspeechは空。inspect以外のchecksは空配列。"
        "confidenceは今回の確信度0〜1。会話は0.72以上、編集・採用・却下・終了は0.85以上。"
        "evidenceは今回の発話の連続部分を原文通り抜く。"
        "終了は疑問・引用・伝聞・条件・否定では選ばず、以前の同意も根拠にしない。"
        "判断できなければaskで確認する。終了のpurposeはfinish_workshop。\n"
        f"{extra}会話段階: {details['conversation_stage']}\n段階: {details['phase']}\n"
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
    return [{"role": "system", "content": "あなたは川柳の共同編集者ドギド。" + WORKSHOP_IDENTITY_PROMPT
            + "speechは関西弁のやさしい相棒の話し声。一人称はオレ。関西弁は語尾中心で、単語は自然な日本語。"
            "今は一句について許可された一手だけを選ぶ。指定JSONだけを返す。"},
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
        awaiting_meaning_ack=view.get("followup") == "meaning_explained",
        awaiting_close_confirmation=view.get("followup") == "close_confirmation",
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
        workshop, frame.get("interpreted_text") or frame["text"], original_player_text=frame["text"],
        phase=frame["phase"], observation=frame.get("observation"),
        turn_steps=frame.get("turn_steps", []),
    )
    # 実行できる一手はRustが渡す。段階とpendingに応じて一手を制限する。
    details["allowed_actions"] = [a for a in details["allowed_actions"] if a in frame["allowed_actions"]]
    details["allowed_actions"] += [a for a in frame["allowed_actions"] if a in FOLLOWUP_ACTIONS]
    candidate = frame["workshop"].get("conversation_candidate")
    if "stage_conversation_candidate" in frame["allowed_actions"] and isinstance(candidate, dict):
        details["allowed_actions"].append("stage_conversation_candidate")
        details["workshop_context"]["conversation_candidate"] = candidate
    if frame["workshop"].get("current_player_idea"):
        details["workshop_context"]["current_player_idea"] = frame["workshop"]["current_player_idea"]
    if frame["workshop"].get("followup") == "combat_resume_confirmation":
        details["conversation_stage"] = "combat_resume_confirmation"
    return details


def handle(frame):
    if frame["op"] == "knowledge_route":
        from dogido_server.player_input import route_player_input
        context = route_player_input(frame["text"])
        query = context.knowledge_query
        if query is None or context.wants_quiet or context.normalized_text.startswith("/"):
            return {"query": None}
        workshop = snapshot_for(frame)
        # 補正で現在句・材料に一致した問いも、一般知識へ流さず句相談へ残す。
        # DB queryと全ての操作は認識原文のまま。
        question_text = frame.get("interpreted_text") or frame["text"]
        subject = "".join(query.subject.split())
        whole_verse = (subject.startswith(("この", "今の", "いまの", "さっきの", "先ほどの", "今詠んだ", "いま詠んだ"))
                       and any(term in subject for term in ("句", "川柳", "俳句", "三行")))
        if (whole_verse or mentioned_workshop_line_fragment(workshop, question_text) is not None
                or grounded_material_for_question(workshop, question_text) is not None):
            return {"query": None}
        return {"query": asdict(query)}
    if frame["op"] == "whole_verse":
        return {"lines": [asdict(line) for line in build_haiku_lines(frame["text"], provenance=frame["source"])]}
    if frame["op"] == "reading_overlay":
        apply_reading_snapshot(frame["rows"])
        return {"applied": True}
    if frame["op"] == "fixed_followup":
        action = None
        if frame["stage"] == "combat_resume_confirmation":
            action = {"resume": "resume_workshop", "close": "decline_resume"}.get(combat_resume_confirmation_decision(frame["text"]))
        elif not frame["pending"]:
            if frame["stage"] == "meaning_explained" and is_meaning_acknowledgement(frame["text"]):
                action = "acknowledge_meaning"
            elif frame["stage"] == "close_confirmation":
                action = {"accept": "confirm_close", "continue": "continue_workshop"}.get(close_confirmation_decision(frame["text"]))
        return {"action": action}
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
    if frame["op"] == "tts_tokens":
        return shared_tts_tokens(frame)
    if frame["op"] == "reading":
        raise ValueError("free-text reading belongs to the Rust host")
    if frame["op"] == "player_edit":
        # Replacement is from a previously validated extraction. This pure helper
        # keeps the existing dictionary/hard-rule behavior; Rust owns CAS and state.
        proposal = dict(frame["proposal"])
        # A correction can supply only the replacement. Preserve the already
        # grounded target of this one discussion; explicit new targets win.
        discussed = frame["workshop"].get("conversation_candidate")
        # Do not mistake a model's omitted field for the player's omission.
        original = frame.get("text", "")
        explicit = _explicit_workshop_line_indices(original)
        if len(explicit) > 1:
            return {"text": None, "failure_reasons": ["target_conflict"]}
        if explicit:
            index = next(iter(explicit))
            if proposal.get("line_index") not in (None, index):
                return {"text": None, "failure_reasons": ["target_conflict"]}
            proposal["line_index"] = index
        fragment = mentioned_workshop_edit_fragment(snapshot_for(frame), original, proposal["replacement_text"])
        if not proposal.get("target_fragment") and fragment:
            proposal["target_fragment"] = fragment
        if (not proposal.get("target_fragment") and proposal.get("line_index") is None
                and isinstance(discussed, dict) and isinstance(discussed.get("proposal"), dict)):
            target = discussed["proposal"]
            proposal["target_fragment"] = target["target_fragment"]
            proposal["line_index"] = target["line_index"]
        result = build_player_line_revision(snapshot_for(frame), PlayerLineReplacement(
            text=proposal["replacement_text"], explicit_line_index=proposal.get("line_index"),
            target_fragment=proposal.get("target_fragment") or None))
        return asdict(result)
    if frame["op"] == "explicit_discussion":
        # This is the same literal proposal form already recognized below.
        # Require the whole utterance, so a mixed question + command is not downgraded.
        if re.fullmatch(r"\s*[「『][^」』]+[」』]を[「『][^」』]+[」』]に"
                        r"(?:するのは|したら|変えるのは)(?:どう|どうかな)[？?。！!]*\s*", frame["text"]):
            return discussion_candidate(frame, None)
        return {"candidate": None}
    if frame["op"] == "discussion_candidate":
        return discussion_candidate(frame, frame.get("proposal"))
    if frame["op"] == "fragment_candidate":
        # Native Rust details decide whether a fixed local edit is available.
        # Keep only the existing dictionary-dependent raw-text extraction here.
        # Never rebuild details, prompts, validation, or saved source readers.
        return {"fixed_payload": fixed_fragment_edit(frame, {
            "phase": frame["phase"], "allowed_actions": frame["allowed_actions"],
        })}
    details = details_for(frame)
    if frame["op"] == "prepare_details":
        # Compatibility/golden oracle only. Runtime uses native details plus
        # fragment_candidate; keep the previous full projection reproducible.
        prepared = {"details": details}
        fixed_payload = fixed_fragment_edit(frame, details)
        if fixed_payload is not None:
            prepared["fixed_payload"] = fixed_payload
        return prepared
    if frame["op"] == "prepare":
        messages = consultation_messages(details)
        fixed_payload = fixed_fragment_edit(frame, details)
        if fixed_payload is not None:
            return {"messages": messages, "fixed_payload": fixed_payload}
        retry = frame.get("retry")
        if retry:
            messages.append({"role": "user", "content":
                "前のJSONは契約違反でした。同じ発話について外形だけ直してください。\n"
                + json.dumps(retry, ensure_ascii=False)
                + "\naction,purpose,evidence,speechは文字列、confidenceは0〜1の数値、checksはreading/meter/sourceの重複なし配列。"
                + "この6キーをすべて含め、許可actionとpurposeを守る。"})
        return {"messages": messages}
    if frame["op"] == "validate":
        # Compatibility/golden oracle. Rust validates the prepared details locally.
        payload = frame["payload"]
        if isinstance(payload, dict) and payload.get("action") in FOLLOWUP_ACTIONS:
            if payload["action"] not in details["allowed_actions"]:
                return {"contract_errors": [], "step": None, "reason": "action_not_allowed"}
            # 外形・confidence・段階・pending・evidenceはRustが厳格に検査する。
            # ここには移植前と同じ原文の否定・引用等の純粋な言語検査だけを残す。
            action = payload["action"]
            text = frame["text"]
            evidence = payload.get("evidence")
            safe = isinstance(evidence, str) and _state_change_evidence_is_safe(
                "close_workshop" if action in {"confirm_close", "decline_resume"} else "stage_player_edit",
                player_text=text, evidence=evidence)
            # 短い同意は「終了」のような操作語を含まない。操作語向けの旧検査だけでは
            # 「いいよとは言ってない」を見落とすため、同意の否定・仮定も照合する。
            if action in {"confirm_close", "acknowledge_meaning", "resume_workshop", "decline_resume"} and re.search(
                    r"(?:とは|って|という意味|ということ|わけ|つもり).{0,20}(?:ない|なく|ません|へん)|"
                    r"(?:言|い)(?:って|った)(?:ない|わけ|つもり)|もし|仮に|たら|なら|[?？]", text):
                safe = False
            if action == "resume_workshop" and re.search(r"続け(?:ない|ん|へん)|再開(?:しない|せん|せえへん)|やめ(?:る|たい|よう)", text):
                safe = False
            if action == "acknowledge_meaning" and re.search(
                    r"わか(?:ら|り|って)ない|分か(?:ら|り|って)ない|理解(?:できない|してない)|納得(?:できない|してない)|違う|ちがう|まだ|ではない|じゃない", text):
                safe = False
            return {"contract_errors": [], "step": payload if safe else None,
                    "reason": "accepted" if safe else "unsafe_followup_evidence"}
        # 省略された項目は非操作の値で補う。編集には実在するline_proposalが必須で、
        # 必須6項目・未知キー・採否/終了の根拠は既存契約でも検査する。
        if isinstance(payload, dict):
            payload = {**INACTIVE_FIELDS, **payload}
        if isinstance(payload, dict) and payload.get("action") in {
                "stage_player_edit", "propose_revision", "accept_pending", "reject_pending", "close_workshop"}:
            # 操作の外形・根拠も原文に照合する。補正で単語が変わっても、原文から
            # 確定できた編集をevidence不一致にせず、補正だけの許可も作らない。
            details = {**details, "player_text": details["original_player_text"]}
        contract = validate_structured_payload(KIND, payload, details=details)
        if not contract.accepted:
            return {"contract_errors": list(contract.errors), "step": None, "reason": "schema_contract_error"}
        step, reason = finalize_workshop_agent_step(payload, details=details)
        if step and step.action == "stage_player_edit" and not explicit_player_edit(frame["text"], step.evidence):
            step, reason = None, "player_edit_intent_not_explicit"
        if step and step.action == "stage_conversation_candidate" and not frame["workshop"].get("conversation_candidate"):
            step, reason = None, "conversation_candidate_missing"
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
