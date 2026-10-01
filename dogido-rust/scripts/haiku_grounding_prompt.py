"""Rust移行用の検査prompt。合否先行・一時番号の契約を固定する。

既存Python版の同関数と比較する。Rust版が採用する検査形式をここに保持し、
補助に使うPython側のバージョンで検査形式が戻らないようにする。
"""
import json
from dogido_server.llm.haiku_prompts import _grounding_scene_text, _structured_json_tail
from dogido_server.haiku.workshop_context import workshop_context_block


def _source_atoms_block(details: dict[str, object]) -> str:
    atoms = details.get("source_atoms")
    if not isinstance(atoms, list):
        return "なし"
    lines: list[str] = []
    numbers = details.get("grounding_atom_numbers")
    numbers = numbers if isinstance(numbers, dict) else {}
    for atom in atoms:
        if not isinstance(atom, dict):
            continue
        atom_id = str(atom.get("atom_id") or "").strip()
        text = str(atom.get("text") or "").strip()
        if atom_id and text:
            kind = str(atom.get("kind") or "").strip()
            claim_class = str(atom.get("claim_class") or "").strip()
            raw_scopes = atom.get("claim_scopes")
            scopes = ",".join(
                str(scope) for scope in raw_scopes if isinstance(scope, str) and scope
            ) if isinstance(raw_scopes, list) else ""
            raw_basis = atom.get("basis_atom_ids")
            basis = ",".join(
                str(numbers.get(value, value)) for value in raw_basis if isinstance(value, str) and value
            ) if isinstance(raw_basis, list) else ""
            origin = (
                " / 発話済みの見どころ"
                if kind in {"preface_clause", "poetic_interpretation"}
                else ""
            )
            basis_note = f" / basis={basis}" if basis else ""
            lines.append(
                f"- [{numbers.get(atom_id, atom_id)}] {text}"
                f" / class={claim_class or 'unknown'} / scopes={scopes or 'none'}"
                f"{basis_note}{origin}"
            )
    if not lines:
        return "なし"
    guide = (
        "scope: identity_only=名称そのものだけ / source_meaning=原文の意味の言い換えまで / "
        "observed_state=現在の実測状態まで / "
        "player_reported_context=プレイヤーが話した内容まで（世界の実測ではない） / "
        "poetic_interpretation=印象・取り合わせだけ。"
        "kind=poetic_interpretation は、一次材料へ照合済みで実際に発話する一句全体の意味の枠"
    )
    return f"{guide}\n" + "\n".join(lines)


def build_haiku_line_grounding_messages(details: dict[str, object]) -> list[dict[str, str]]:
    """字面ではなく、原文要素の意味が各行に残ったかを保守的に判定する。"""

    lines = details.get("grounding_lines")
    line_block = "\n".join(
        f"- {row.get('line_index')}: {row.get('text')}"
        for row in lines if isinstance(row, dict)
    ) if isinstance(lines, list) else "なし"
    atoms = _source_atoms_block(details)
    requested_indices = [
        row.get("line_index")
        for row in lines
        if isinstance(row, dict)
        and isinstance(row.get("line_index"), int)
        and not isinstance(row.get("line_index"), bool)
    ] if isinstance(lines, list) else []
    numbers = details.get("grounding_atom_numbers")
    numbers = numbers if isinstance(numbers, dict) else {}
    source_rows = details.get("source_atoms")
    source_rows = source_rows if isinstance(source_rows, list) else []
    example_numbers = [
        numbers[row["atom_id"]] for row in source_rows
        if isinstance(row, dict) and row.get("atom_id") in numbers
    ] or [1]
    example = {
        "verdicts": {str(index): "pass" for index in requested_indices},
        "assessments": [
            {
                "line_index": index,
                "atom_ids": [example_numbers[min(position, len(example_numbers) - 1)]],
            }
            for position, index in enumerate(requested_indices)
        ],
        "failure_reasons": {},
    }
    revision_block = ""
    if isinstance(details.get("revision_edits"), list):
        revision_block = ("【今回の修正差分】\n" + json.dumps(details["revision_edits"], ensure_ascii=False)
            + "\n修正前後を比べる。修正案だから合格に寄せない。"
            "末尾に助詞・接尾語を足しただけで不自然な反復や未完の文になった場合もjapanese_fail。"
            "かなの別の区切り方を考えれば無理に読める、という救済はしない。"
            "三行を実際に続けて読んだ修飾関係も確認する。\n")
    user_prompt = (
        "川柳の各行を、原文材料と一行ずつ照合する。\n"
        "最初に verdicts を書き、依頼された行番号ごとの合否を先に確定する。"
        "値は pass=意味保持も日本語も合格、meaning_fail=意味保持だけ不合格、"
        "japanese_fail=日本語だけ不合格、both_fail=両方不合格の4種類。"
        "その後 assessments に各行の材料番号を返す。最後に failure_reasons を書き、"
        "不合格行の番号だけをキーに、実際に確認できた理由を0〜3件の配列で書く。"
        '各理由は {"kind":"meaning または japanese","fragment":"その行にある連続した断片","reason":"短い具体的な理由"}。'
        "kindは不合格にした軸だけを選び、fragmentは24字以内、reasonは48字以内にする。"
        "同じ問題を言い換えて増やさず、3件を埋めるためにミスを探さない。"
        "具体的な根拠がない指摘は加えない。"
        "合格理由は書かず、全行合格なら failure_reasons は空のオブジェクト。"
        "meaning_retained と natural_japanese の項目は返さない。\n"
        "音や名前から説明にない性質を推測しない。遠い連想や、意味のない造語は不合格。\n"
        "意味保持の合格は、指定材料の意味が言い換えとして残る場合だけ。\n"
        "材料名の一部だけを自然に使うのはよい。名前全体の復唱は必須ではない。\n"
        "class=factual は scopes の範囲に明記された主張だけを許す。"
        "class=interpretive は印象・取り合わせとしてだけ使い、新しい事実の根拠にしない。\n"
        "preface_clause は basis の一次atomから派生した発話であり、"
        "basisにない状態・感覚・因果を足してはならない。\n"
        "kind=poetic_interpretation は、一次atomへ照合済みの見どころ全体である。"
        "このatomでは語句の逐語的な再現を求めない。行がその情景に反せず、"
        "そこから自然に浮かぶ比喩・余情・印象なら意味保持は合格としてよい。"
        "同じ poetic_interpretation を複数行が共有してよい。\n"
        "日本語の合格は、単独で聞いて意味の通る自然な現代日本語の場合だけ。"
        "音数合わせで接尾語や助動詞を機械的につないだ語、修飾関係が分からない語、"
        "一般の語として意味を説明できない造語は日本語不合格にする。\n"
        "atom_ids には、原文材料の角括弧にある一時的な整数番号だけを入れる。"
        "長いIDの転記や候補外の番号は禁止。実際に意味が残った材料だけを選び、"
        "意味の合う材料がないときは meaning_fail または both_fail として空配列にする。\n\n"
        "似た材料が複数あるときは、行の修飾語と【発話済みの見どころ】まで比べ、"
        "最も具体的に意味が合う出典を選ぶ。たとえば色や状態を持つ対象を、"
        "単に同じ種類の一般的な物へ寄せない。逐語一致より意味の対応を優先する。\n"
        f"{revision_block}【判定する行】\n{line_block}\n\n"
        f"【原文材料】\n{atoms}\n\n"
        f"【発話済みの見どころ】\n{_grounding_scene_text(details)}\n"
        f"{workshop_context_block(details)}"
        + _structured_json_tail(json.dumps(example, ensure_ascii=False))
    )
    return [
        {
            "role": "system",
            "content": "あなたは日本語と出典の厳格な検証者。返答は JSON のみ。",
        },
        {"role": "user", "content": user_prompt},
    ]
