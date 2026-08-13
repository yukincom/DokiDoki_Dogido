# llm/haiku_prompts.py
"""川柳系プロンプト。

ネガティブの積み上げより、ドギドが材料を見て一句詠みたくなる肯定形を先に置く。
音数・かな表記など形の約束だけ、短く残す。
"""

from __future__ import annotations

import json


def _item_hint(details: dict[str, object]) -> str:
    held_item = str(details.get("held_item") or "").strip()
    source = str(details.get("poem_item_source") or "hand").strip().lower()
    close_pair = [str(item) for item in details.get("inventory_close_pair", []) if item]
    far_item = str(details.get("inventory_far_item") or "").strip()
    parts: list[str] = []
    if held_item and held_item != "なし":
        # 道具手持ち時は所持から選んだ1つを主役に（つるはし連発を避ける）
        if source == "pocket":
            parts.append(f"持ち物のひとつは{held_item}")
        else:
            parts.append(f"手には{held_item}")
    if far_item and far_item != held_item:
        parts.append(f"目立つ別口は{far_item}")
    elif close_pair and not (held_item and held_item != "なし"):
        parts.append("同系統の持ち物が少しある")
    return "。".join(parts) if parts else "なし"


def _scene_block(details: dict[str, object]) -> str:
    scene = details.get("scene")
    if not isinstance(scene, dict) or not scene.get("summary"):
        return "なし"
    motifs = "、".join(str(item) for item in scene.get("motifs", []) if item) or "なし"
    focus = "、".join(str(item) for item in scene.get("focus", []) if item) or "なし"
    return (
        f"要約: {scene.get('summary')}\n"
        f"モチーフ: {motifs}\n"
        f"焦点: {focus}"
    )


def _constraint_block(details: dict[str, object]) -> str:
    constraints = details.get("haiku_constraints")
    if not isinstance(constraints, dict):
        return ""
    allowed_terms = "、".join(str(term) for term in constraints.get("allowed_terms", []) if term)
    forbidden_terms = "、".join(str(term) for term in constraints.get("forbidden_terms", []) if term)
    player_lessons = [str(x) for x in constraints.get("player_lessons", []) if x][:3]
    if not allowed_terms and not forbidden_terms and not player_lessons:
        return ""
    parts: list[str] = []
    if allowed_terms:
        parts.append(f"道具の読みの目安: {allowed_terms}")
    if forbidden_terms:
        parts.append(f"道具の読みで避けたい語: {forbidden_terms}")
    if player_lessons:
        lesson_lines = "\n".join(f"- {note}" for note in player_lessons)
        parts.append(f"プレイヤーの最近の好み（軽く参考）:\n{lesson_lines}")
    return "\n".join(parts)


def _catalog_notes_block(details: dict[str, object]) -> str:
    notes = [str(note) for note in details.get("catalog_notes", []) if note]
    if not notes:
        return "なし"
    return "\n".join(f"- {note}" for note in notes)


def _poetic_lines_block(details: dict[str, object]) -> str:
    lines = [str(line) for line in details.get("poetic_lines", []) if line]
    if not lines:
        return "なし"
    return "\n".join(f"- {line}" for line in lines)


def _haiku_tags_hint(details: dict[str, object]) -> str:
    tags = [str(item) for item in details.get("haiku_tags", []) if item]
    if not tags:
        return "なし"
    return "、".join(tags)


def _has_structure_focus(details: dict[str, object]) -> bool:
    if details.get("has_structure"):
        return True
    return bool(str(details.get("structure_label") or "").strip())


def _list_lines(items: object, *, empty: str = "なし") -> str:
    if not isinstance(items, (list, tuple)):
        return empty
    lines = [f"- {item}" for item in items if isinstance(item, str) and item.strip()]
    return "\n".join(lines) if lines else empty


def _materials_for_dogido(details: dict[str, object]) -> str:
    """ドギドが見る『いまの材料』一式。禁止文ではなく眺め用。"""
    structure_label = str(details.get("structure_label") or "").strip()
    climate_hint = str(details.get("climate_hint") or "").strip()
    nearby = "、".join(str(x) for x in details.get("nearby_blocks", []) if x) or "とくになし"
    mobs = "、".join(str(x) for x in details.get("passive_mobs", []) if x) or "とくになし"
    item_hint = _item_hint(details)
    weather = details.get("weather_label", details.get("weather", "不明"))
    time_label = details.get("time_label", details.get("time_phase", "不明"))
    weather_context = str(details.get("weather_context") or "").strip()

    chunks: list[str] = []
    if _has_structure_focus(details) and structure_label:
        chunks.append(f"いまいる場所: {structure_label}")
        if climate_hint:
            chunks.append(f"空気・温度の気配: {climate_hint}")
    else:
        biome = details.get("biome", "不明")
        group = details.get("biome_group", "")
        traits = "、".join(str(t) for t in details.get("biome_traits", []) if t)
        place = f"いまの景色: {biome}"
        if group:
            place += f"（{group}）"
        chunks.append(place)
        if traits:
            chunks.append(f"土地の感触: {traits}")

    chunks.append(f"空と時間: {weather} / {time_label}")
    if weather_context:
        chunks.append(f"コードで確定した現在地の気象: {weather_context}")
    chunks.append(f"そばにあるもの: {nearby}")
    chunks.append(f"穏やかないきもの: {mobs}")
    chunks.append(f"手もと: {item_hint}")

    tags = _haiku_tags_hint(details)
    if tags != "なし":
        chunks.append(f"ことばの匂い: {tags}")

    catalog = _catalog_notes_block(details)
    if catalog != "なし":
        chunks.append(f"ちょっとした知識:\n{catalog}")

    poetic = _poetic_lines_block(details)
    if poetic != "なし":
        chunks.append(f"いきものの声・姿:\n{poetic}")

    return "\n".join(chunks)


def _form_card() -> str:
    """形の約束。短く、でも漢字混入ははっきり止める（落とすと一句ごと消える）。"""
    kana_line = (
        "- ひらがなとカタカナだけ。漢字は一文字も使わない"
        "（土→つち、夜→よる、闇→やみ、森→もり、灯→ひ）"
    )
    return (
        "かたち:\n"
        "- 3行（改行で区切る）。五・七・五（各行±1音までゆるく）\n"
        f"{kana_line}\n"
        "- 場面メモが漢字でも、句にするときは全部かな\n"
        "- 句以外の前置きや説明は入れない"
    )


def _dogido_haiku_spirit(*, has_structure: bool) -> str:
    if has_structure:
        place = (
            "いまは特別な場所にいる。"
            "その場所の空気を一句の芯にしてよい。"
            "名前は詩に馴染む言い方でよい。"
        )
    else:
        place = (
            "穏やかないきものや、そばの自然が寄り添ってくれる。"
            "空や土地は味付け。手のものは最後の一滴。"
        )
    return (
        "あなたはドギド。怖がりだけど、プレイヤーと並んで景色を見て、"
        "ふっと一句詠む関西の相棒や。\n"
        f"{place}\n"
        "各行は、示された材料のどれか一つ以上を意味の根にする。"
        "説明文の写経でなくてよいが、元の意味を別物に変えない。"
        "造語や崩れた文を避け、耳で一度聞いて意味の通る現代の日本語で。"
    )


def _source_atoms_block(details: dict[str, object]) -> str:
    atoms = details.get("source_atoms")
    if not isinstance(atoms, list):
        return "なし"
    lines: list[str] = []
    for atom in atoms:
        if not isinstance(atom, dict):
            continue
        atom_id = str(atom.get("atom_id") or "").strip()
        text = str(atom.get("text") or "").strip()
        if atom_id and text:
            kind = str(atom.get("kind") or "").strip()
            origin = "（発話済みの見どころ）" if kind == "preface_interpretation" else ""
            lines.append(f"- [{atom_id}] {text}{origin}")
    return "\n".join(lines) if lines else "なし"


def _generation_strategy_block(details: dict[str, object]) -> str:
    """比較実験中の生成単位を、詩作側へ短く明示する。"""

    strategy = str(details.get("generation_strategy") or "three_slot")
    descriptions = {
        "whole_poem": (
            "一句全体方式: 三行を一つの完成形として組み立てる。"
            "上から順に独立採用せず、三行全体の流れと着地を優先する。"
        ),
        "three_slot": (
            "三点スロット方式: 上五・中七・下五をそれぞれ独立した観察点として組み立てる。"
        ),
        "one_plus_two": (
            "前1＋後2方式: 上五に入口や印象を置き、中七と下五を一続きの展開として組み立てる。"
        ),
        "two_plus_one": (
            "前2＋後1方式: 上五と中七で場面を作り、下五を独立した着地や印象として組み立てる。"
        ),
    }
    return descriptions.get(strategy, descriptions["three_slot"])


def build_haiku_draft_messages(details: dict[str, object]) -> list[dict[str, str]]:
    has_structure = _has_structure_focus(details)
    materials = _materials_for_dogido(details)
    candidates = _list_lines(details.get("feature_candidates", []))
    tensions = _list_lines(details.get("candidate_tensions", []))
    scene_block = _scene_block(details)
    constraint_block = _constraint_block(details)
    source_atoms = _source_atoms_block(details)
    generation_strategy = _generation_strategy_block(details)

    irony = details.get("irony")
    irony_block = "なし"
    if isinstance(irony, dict) and irony.get("description"):
        irony_focus = "、".join(str(item) for item in irony.get("focus", []) if item) or "—"
        irony_block = f"{irony.get('description')}（焦点: {irony_focus}）"

    constraint_section = ""
    if constraint_block:
        constraint_section = f"\n読みのメモ:\n{constraint_block}\n"

    user_prompt = (
        f"{_dogido_haiku_spirit(has_structure=has_structure)}\n"
        "\n"
        "【いまの材料】\n"
        f"{materials}\n"
        "\n"
        "【眺めのヒント】\n"
        f"{candidates}\n"
        "\n"
        "【ちょっとした取り合わせ】\n"
        f"{tensions}\n"
        "\n"
        "【先に感じた場面】\n"
        f"{scene_block}\n"
        f"{irony_block}\n"
        f"{constraint_section}"
        "\n"
        "【行の出典に使える材料】\n"
        f"{source_atoms}\n"
        "三行それぞれで、異なる材料を主な意味の根にする。"
        "同じ [atom_id] を二行で使わない。\n"
        "【今回の生成単位】\n"
        f"{generation_strategy}\n"
        f"{_form_card()}\n"
        "\n"
        + _structured_json_tail('{"lines": ["ごおんのく", "ななおんのく", "ごおんのく"]}')
    )
    return [
        {
            "role": "system",
            "content": (
                "あなたは Minecraft の相棒『ドギド』。"
                "材料に根拠のある、自然な日本語の川柳を一句詠む。"
                "返答は JSON のみ。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


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
    example = {
        "assessments": [
            {
                "line_index": index,
                "atom_ids": ["..."],
                "meaning_retained": True,
                "natural_japanese": True,
                "reason": "...",
            }
            for index in requested_indices
        ]
    }
    user_prompt = (
        "川柳の各行を、原文材料と一行ずつ照合する。\n"
        "音や名前から説明にない性質を推測しない。遠い連想や、意味のない造語は不合格。\n"
        "meaning_retained は、指定atomの意味が言い換えとして残る場合だけ true。\n"
        "材料名の一部だけを自然に使うのはよい。名前全体の復唱は必須ではない。\n"
        "natural_japanese は、単独で聞いて自然な現代日本語の場合だけ true。\n"
        "atom_ids には、実際に意味が残ったIDだけを入れる。候補外IDは禁止。\n\n"
        f"【判定する行】\n{line_block}\n\n"
        f"【原文材料】\n{atoms}\n\n"
        + _structured_json_tail(json.dumps(example, ensure_ascii=False))
    )
    return [
        {
            "role": "system",
            "content": "あなたは日本語と出典の厳格な検証者。返答は JSON のみ。",
        },
        {"role": "user", "content": user_prompt},
    ]


def build_haiku_line_regeneration_messages(details: dict[str, object]) -> list[dict[str, str]]:
    """合格済み行を固定し、指定された不合格行だけを作り直す。"""

    rows = details.get("current_lines")
    current = "\n".join(
        _regeneration_line_prompt(row)
        for row in rows if isinstance(row, dict)
    ) if isinstance(rows, list) else "なし"
    indices = details.get("failed_line_indices")
    retry_indices = [int(value) for value in indices if isinstance(value, int)] if isinstance(indices, list) else []
    targets = ", ".join(str(index) for index in retry_indices) or "なし"
    atoms = _source_atoms_block(details)
    generation_strategy = _generation_strategy_block(details)
    constraint_block = _constraint_block(details)
    constraints = f"\n読みのメモ:\n{constraint_block}\n" if constraint_block else ""
    user_prompt = (
        "固定行は書き換えず、再生成対象の行だけを作り直す。\n"
        "使える材料には、固定行ですでに使ったatomは含まれていない。"
        "再生成する行どうしでもatomを重複させない。\n"
        "各行は材料の意味を保ち、自然な現代日本語にする。\n\n"
        f"【今回の生成単位】\n{generation_strategy}\n"
        "再生成対象に同じスロットの複数行が含まれる場合は、"
        "その全行を一つの流れとして作り直す。\n"
        f"【現在の三行】\n{current}\n"
        f"【再生成するline_index】 {targets}\n"
        f"【残っている原文材料】\n{atoms}\n"
        f"{constraints}\n"
        "再生成行の現在音数はコードで計測済み。目標音数を優先し、少なくとも許容範囲へ収める。\n"
        "音数目標は line_index 0=五音、1=七音、2=五音（各±1音）。かなだけ。\n"
        + _structured_json_tail(
            '{"lines": [{"line_index": 1, "text": "ななおんのく"}]}'
        )
    )
    return [
        {
            "role": "system",
            "content": (
                "あなたは Minecraft の相棒『ドギド』。"
                "出典を守り、指定された川柳の行だけを直す。返答は JSON のみ。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def _regeneration_line_prompt(row: dict[str, object]) -> str:
    """コード計測済みの音数を、再生成対象の行だけへ明示する。"""

    line_index = row.get("line_index")
    text = row.get("text")
    if row.get("frozen"):
        return f"- {line_index}: {text}（固定）"
    count = row.get("sound_count")
    target = row.get("target_sound_count")
    minimum = row.get("allowed_sound_min")
    maximum = row.get("allowed_sound_max")
    status = {
        "too_long": "音数が長い",
        "too_short": "音数が短い",
        "within_range": "音数は許容内",
    }.get(str(row.get("meter_status") or ""), "音数を再確認")
    if all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (count, target, minimum, maximum)
    ):
        return (
            f"- {line_index}: {text}（再生成・{status}: "
            f"現在{count}音 → 目標{target}音、許容{minimum}〜{maximum}音）"
        )
    return f"- {line_index}: {text}（再生成）"


def _structured_json_tail(schema_line: str) -> str:
    return (
        "返事は JSON オブジェクト1つだけ。\n"
        f"形: {schema_line}"
    )


def build_haiku_irony_messages(details: dict[str, object]) -> list[dict[str, str]]:
    has_structure = _has_structure_focus(details)
    materials = _materials_for_dogido(details)
    candidates = _list_lines(details.get("feature_candidates", []))
    tensions = _list_lines(details.get("candidate_tensions", []))
    place_nudge = (
        "いまいる場所の空気を大切に。"
        if has_structure
        else "いきものや自然の手触りを大切に。"
    )
    user_prompt = (
        "ドギドとして、いまの材料をひと息ながめてほしい。\n"
        "川柳の芯になりそうな『取り合わせ』か『その場の気配』をひとつだけ拾う。\n"
        f"{place_nudge}\n"
        "大げさな矛盾は要らない。平凡でも、ふっと心に残る一点でよい。\n"
        "材料の意味を変えず、具体的な一点を短く書く。\n"
        "\n"
        f"【いまの材料】\n{materials}\n"
        "\n"
        f"【眺めのヒント】\n{candidates}\n"
        "\n"
        f"【ちょっとした取り合わせ】\n{tensions}\n"
        "\n"
        + _structured_json_tail(
            '{"found": true/false, "kind": "relation|contrast|juxtaposition|scene", '
            '"description": "...", "elements": ["..."], "focus": ["..."], "confidence": 0.0}'
        )
    )
    return [
        {
            "role": "system",
            "content": (
                "あなたは Minecraft の相棒『ドギド』。"
                "材料から、一句の芯になる取り合わせを感じ取る。"
                "返答は JSON のみ。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def build_haiku_scene_messages(details: dict[str, object]) -> list[dict[str, str]]:
    has_structure = _has_structure_focus(details)
    materials = _materials_for_dogido(details)
    candidates = _list_lines(details.get("feature_candidates", []))
    tensions = _list_lines(details.get("candidate_tensions", []))
    irony = details.get("irony")
    irony_block = "まだなし"
    if isinstance(irony, dict) and irony.get("description"):
        irony_block = (
            f"{irony.get('description')} / "
            f"焦点: {'、'.join(str(item) for item in irony.get('focus', []) if item) or '—'}"
        )
    place_nudge = (
        "場所の気配をひと場面に。"
        if has_structure
        else "なんでもない午後でも、空気が見えればそれでよい。"
    )
    user_prompt = (
        "ドギドとして、川柳の種になる『ひとつの場面』を短く描いてほしい。\n"
        f"{place_nudge}\n"
        "材料の意味を変えず、あとで五七五に落としやすい具体的なことばで。\n"
        "\n"
        f"【いまの材料】\n{materials}\n"
        "\n"
        f"【眺めのヒント】\n{candidates}\n"
        "\n"
        f"【ちょっとした取り合わせ】\n{tensions}\n"
        "\n"
        f"【さきに感じた芯】\n{irony_block}\n"
        "\n"
        + _structured_json_tail(
            '{"found": true/false, "summary": "...", "motifs": ["..."], '
            '"focus": ["..."], "confidence": 0.0}'
        )
    )
    return [
        {
            "role": "system",
            "content": (
                "あなたは Minecraft の相棒『ドギド』。"
                "材料から、一句の種になる短い場面を描く。"
                "返答は JSON のみ。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def build_haiku_workshop_material_pick_messages(details: dict[str, object]) -> list[dict[str, str]]:
    """句の断片質問に、材料候補から選んで短く答える（workshop ask_meaning）。"""
    candidates = details.get("candidates") or []
    if isinstance(candidates, (list, tuple)):
        cand_lines = "\n".join(
            f"{i}: {c}" for i, c in enumerate(candidates) if isinstance(c, str) and c.strip()
        )
    else:
        cand_lines = ""
    if not cand_lines:
        cand_lines = "（候補なし）"
    verse = str(details.get("verse") or "").strip() or "（句なし）"
    player_text = str(details.get("player_text") or "").strip() or "（なし）"
    fragment = str(details.get("fragment") or "").strip() or "（特定できず）"
    user_prompt = (
        "プレイヤーが、いまの句のことばを聞いてくれた。\n"
        "ドギドとして、材料のなかからいちばんそれらしいものをそっと指して、短く答える。\n"
        "言い方は気さくに。わかればそれでええ。\n"
        "\n"
        f"句: {verse}\n"
        f"プレイヤー: {player_text}\n"
        f"指してそうなところ: {fragment}\n"
        "\n"
        f"材料:\n{cand_lines}\n"
        "\n"
        + _structured_json_tail('{"pick_index": 0, "reply": "それは、平原やで。"}')
        + "\nどれも違えば pick_index は null で、正直に短く。"
    )
    return [
        {
            "role": "system",
            "content": (
                "あなたは Minecraft の相棒『ドギド』。"
                "句の言葉と材料をつないで、短く関西弁で答える。"
                "返答は JSON のみ。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]
