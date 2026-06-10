# llm/haiku_prompts.py
from __future__ import annotations


def _item_hint(details: dict[str, object]) -> str:
    held_item = str(details.get("held_item") or "").strip()
    close_pair = [str(item) for item in details.get("inventory_close_pair", []) if item]
    far_item = str(details.get("inventory_far_item") or "").strip()
    parts: list[str] = []
    if held_item and held_item != "なし":
        parts.append(f"手には{held_item}")
    if far_item:
        parts.append(f"目立つ別口は{far_item}")
    elif close_pair:
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
        return "なし"
    allowed_terms = "、".join(str(term) for term in constraints.get("allowed_terms", []) if term) or "なし"
    forbidden_terms = "、".join(str(term) for term in constraints.get("forbidden_terms", []) if term) or "なし"
    if allowed_terms == "なし" and forbidden_terms == "なし":
        return "なし"
    return (
        f"使ってよい語: {allowed_terms}\n"
        f"使ってはいけない語: {forbidden_terms}"
    )


def build_haiku_messages(details: dict[str, object]) -> list[dict[str, str]]:
    feature_candidates = details.get("feature_candidates", [])
    candidate_lines = "\n".join(
        f"- {candidate}" for candidate in feature_candidates if isinstance(candidate, str)
    )
    candidate_tensions = details.get("candidate_tensions", [])
    tension_lines = "\n".join(
        f"- {candidate}" for candidate in candidate_tensions if isinstance(candidate, str)
    ) or "- なし"
    nearby_blocks = "、".join(str(item) for item in details.get("nearby_blocks", []) if item) or "なし"
    passive_mobs = "、".join(str(item) for item in details.get("passive_mobs", []) if item) or "なし"
    haiku_tags = "、".join(str(item) for item in details.get("haiku_tags", []) if item) or "なし"
    biome_traits = "、".join(str(item) for item in details.get("biome_traits", []) if item) or "なし"
    item_hint = _item_hint(details)
    scene_block = _scene_block(details)
    constraint_block = _constraint_block(details)
    irony = details.get("irony")
    irony_block = "なし"
    if isinstance(irony, dict) and irony.get("description"):
        irony_focus = "、".join(str(item) for item in irony.get("focus", []) if item) or "なし"
        irony_elements = "、".join(str(item) for item in irony.get("elements", []) if item) or "なし"
        irony_block = (
            f"説明: {irony.get('description')}\n"
            f"焦点: {irony_focus}\n"
            f"要素: {irony_elements}\n"
            f"種類: {irony.get('kind', 'contrast')}"
        )

    user_prompt = (
        "Minecraft の現在状況から川柳を作る。\n"
        "以下の候補から、関係が強い2つまでを選び、その候補だけをモチーフにすること。\n"
        f"{candidate_lines}\n"
        "\n"
        "コード側で見つけた状況の取り合わせ候補:\n"
        f"{tension_lines}\n"
        "\n"
        "場面の要約:\n"
        f"{scene_block}\n"
        "\n"
        "注目したい取り合わせや場面の要約:\n"
        f"{irony_block}\n"
        "\n"
        "主役語の制約:\n"
        f"{constraint_block}\n"
        "\n"
        "川柳ルール:\n"
        "- 主題の優先度は、平和なMobと周辺の自然物を最優先、次にバイオームや天気、アイテムは最後の弱い味付けにする\n"
        "- アイテムだけを主題にしない\n"
        "- 3行で出力する\n"
        "- 1行目は5音、2行目は7音、3行目は5音。各行ともプラスマイナス1音まで許容\n"
        "- ひらがなかカタカナだけを使う。漢字、英字、数字、記号、句読点は使わない\n"
        "- 小さいゃゅょは前の文字と合わせて1音\n"
        "- 小さいっは1音\n"
        "- んは1音\n"
        "- ーは1音\n"
        "- 説明や前置きは書かない\n"
        "- 場面の要約があれば、その内容を5-7-5に圧縮するつもりで作る\n"
        "- 取り合わせや場面の焦点があれば、それを優先してよい\n"
        "- 候補や現在の状況にないアイテム、ブロック、Mob を勝手に出さない\n"
        "- もし道具名や主役語を句に入れるなら、『使ってよい語』だけを使い、『使ってはいけない語』へ言い換えない\n"
        "- 意味のない五十音並びやランダム文字列は禁止\n"
        "\n"
        "現在の状況:\n"
        f"- バイオーム: {details.get('biome', 'unknown')}\n"
        f"- バイオーム分類: {details.get('biome_group', 'unknown')}\n"
        f"- バイオーム特徴: {biome_traits}\n"
        f"- 周辺ブロック: {nearby_blocks}\n"
        f"- 平和なMob: {passive_mobs}\n"
        f"- アイテムヒント（弱）: {item_hint}\n"
        f"- 詩語ヒント: {haiku_tags}\n"
        f"- Z座標: {details.get('z_value', 0)}\n"
        f"- 天気: {details.get('weather_label', details.get('weather', 'unknown'))}\n"
        f"- 時間: {details.get('time_label', details.get('time_phase', 'unknown'))}\n"
        "\n"
        "3行の川柳だけを返す。"
    )
    return [
        {
            "role": "system",
            "content": (
                "あなたはMinecraft実況AI『ドギド』です。"
                "今は実況セリフではなく、日本語の川柳だけを作ります。"
                "余計な説明は禁止です。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def build_haiku_repair_messages(details: dict[str, object]) -> list[dict[str, str]]:
    scene_block = _scene_block(details)
    constraint_block = _constraint_block(details)
    draft = str(details.get("attempted_haiku") or "").strip() or "なし"
    user_prompt = (
        "以下の川柳下書きを、意味を保ったまま自然な日本語の川柳に直す。\n"
        "返答は3行の川柳だけ。説明は禁止。\n"
        "\n"
        "下書き:\n"
        f"{draft}\n"
        "\n"
        "場面の要約:\n"
        f"{scene_block}\n"
        "\n"
        "主役語の制約:\n"
        f"{constraint_block}\n"
        "\n"
        "修正ルール:\n"
        "- 3行で出力する\n"
        "- 1行目は5音、2行目は7音、3行目は5音。全行合計でプラスマイナス1音まで許容\n"
        "- ひらがなかカタカナだけを使う。漢字、英字、数字、記号、句読点は使わない\n"
        "- 元の場面や主題は保つ\n"
        "- 新しいアイテム、ブロック、Mob を足さない\n"
        "- もし道具名や主役語を句に入れるなら、『使ってよい語』だけを使い、『使ってはいけない語』へ言い換えない\n"
        "- 読みにくい造語や意味のない並びは使わない\n"
        "\n"
        "修正版の3行だけを返す。"
    )
    return [
        {
            "role": "system",
            "content": (
                "あなたはMinecraft実況AI『ドギド』です。"
                "今は実況ではなく、日本語の川柳の下書きを自然な川柳に直します。"
                "余計な説明は禁止です。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def build_haiku_irony_messages(details: dict[str, object]) -> list[dict[str, str]]:
    feature_candidates = "\n".join(
        f"- {candidate}" for candidate in details.get("feature_candidates", []) if isinstance(candidate, str)
    ) or "- なし"
    candidate_tensions = "\n".join(
        f"- {candidate}" for candidate in details.get("candidate_tensions", []) if isinstance(candidate, str)
    ) or "- なし"
    haiku_tags = "、".join(str(item) for item in details.get("haiku_tags", []) if item) or "なし"
    nearby_blocks = "、".join(str(item) for item in details.get("nearby_blocks", []) if item) or "なし"
    passive_mobs = "、".join(str(item) for item in details.get("passive_mobs", []) if item) or "なし"
    item_hint = _item_hint(details)
    user_prompt = (
        "以下の Minecraft 状況から、川柳の焦点になる『関係のある取り合わせ・印象的な場面』を1つだけ選ぶ。\n"
        "強い違和感がなくても、その場の空気や手触りがあれば found=true でよい。\n"
        "必ず JSON オブジェクトだけを返す。\n"
        "説明文や markdown は禁止。\n"
        "\n"
        "返答形式:\n"
        "{\"found\": true/false, \"kind\": \"relation|contrast|juxtaposition|scene\", "
        "\"description\": \"...\", \"elements\": [\"...\"], \"focus\": [\"...\"], \"confidence\": 0.0}\n"
        "\n"
        "候補:\n"
        f"{feature_candidates}\n"
        "\n"
        "コード側の取り合わせ候補:\n"
        f"{candidate_tensions}\n"
        "\n"
        "状況:\n"
        f"- バイオーム: {details.get('biome', 'unknown')} ({details.get('biome_group', 'unknown')})\n"
        f"- 天気: {details.get('weather_label', details.get('weather', 'unknown'))}\n"
        f"- 時間: {details.get('time_label', details.get('time_phase', 'unknown'))}\n"
        f"- アイテムヒント（弱）: {item_hint}\n"
        f"- 周辺ブロック: {nearby_blocks}\n"
        f"- 平和なMob: {passive_mobs}\n"
        f"- 詩語ヒント: {haiku_tags}\n"
        "\n"
        "優先度は、平和なMobと周辺の自然物を最優先、次にバイオームや天気、アイテムは最後の弱い味付けにする。\n"
        "与えられた状況にないアイテム、ブロック、Mob を書かない。\n"
        "無理に矛盾を作らず、平凡でも場面として立っていれば拾ってよい。"
    )
    return [
        {
            "role": "system",
            "content": (
                "あなたはMinecraftの状況から、川柳に向いた取り合わせや場面の焦点だけを抽出する。"
                "返答は JSON オブジェクトのみ。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def build_haiku_scene_messages(details: dict[str, object]) -> list[dict[str, str]]:
    feature_candidates = "\n".join(
        f"- {candidate}" for candidate in details.get("feature_candidates", []) if isinstance(candidate, str)
    ) or "- なし"
    candidate_tensions = "\n".join(
        f"- {candidate}" for candidate in details.get("candidate_tensions", []) if isinstance(candidate, str)
    ) or "- なし"
    nearby_blocks = "、".join(str(item) for item in details.get("nearby_blocks", []) if item) or "なし"
    passive_mobs = "、".join(str(item) for item in details.get("passive_mobs", []) if item) or "なし"
    item_hint = _item_hint(details)
    irony = details.get("irony")
    irony_block = "なし"
    if isinstance(irony, dict) and irony.get("description"):
        irony_block = (
            f"説明: {irony.get('description')}\n"
            f"焦点: {'、'.join(str(item) for item in irony.get('focus', []) if item) or 'なし'}"
        )
    user_prompt = (
        "以下の Minecraft 状況から、川柳の種になる『ひとつの場面』を短く要約する。\n"
        "特異でなくても、その場の空気が見えるなら found=true でよい。\n"
        "必ず JSON オブジェクトだけを返す。\n"
        "説明文や markdown は禁止。\n"
        "\n"
        "返答形式:\n"
        "{\"found\": true/false, \"summary\": \"...\", \"motifs\": [\"...\"], "
        "\"focus\": [\"...\"], \"confidence\": 0.0}\n"
        "\n"
        "候補:\n"
        f"{feature_candidates}\n"
        "\n"
        "コード側の取り合わせ候補:\n"
        f"{candidate_tensions}\n"
        "\n"
        "前段の要約候補:\n"
        f"{irony_block}\n"
        "\n"
        "状況:\n"
        f"- バイオーム: {details.get('biome', 'unknown')} ({details.get('biome_group', 'unknown')})\n"
        f"- 天気: {details.get('weather_label', details.get('weather', 'unknown'))}\n"
        f"- 時間: {details.get('time_label', details.get('time_phase', 'unknown'))}\n"
        f"- アイテムヒント（弱）: {item_hint}\n"
        f"- 周辺ブロック: {nearby_blocks}\n"
        f"- 平和なMob: {passive_mobs}\n"
        "\n"
        "優先度は、平和なMobと周辺の自然物を最優先、次にバイオームや天気、アイテムは最後の弱い味付けにする。\n"
        "summary は後段で5-7-5に圧縮しやすい、具体的で短い場面説明にする。\n"
        "与えられた状況にないアイテム、ブロック、Mob を書かない。\n"
        "村や名所の特定までは要らない。なんでもない場面でも、空気があれば拾ってよい。"
    )
    return [
        {
            "role": "system",
            "content": (
                "あなたはMinecraftの状況から、川柳の種になる短い場面要約だけを抽出する。"
                "返答は JSON オブジェクトのみ。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]
