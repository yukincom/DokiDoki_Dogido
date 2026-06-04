# llm/haiku_prompts.py
from __future__ import annotations


def build_haiku_messages(details: dict[str, object]) -> list[dict[str, str]]:
    feature_candidates = details.get("feature_candidates", [])
    candidate_lines = "\n".join(
        f"- {candidate}" for candidate in feature_candidates if isinstance(candidate, str)
    )
    candidate_tensions = details.get("candidate_tensions", [])
    tension_lines = "\n".join(
        f"- {candidate}" for candidate in candidate_tensions if isinstance(candidate, str)
    ) or "- なし"
    inventory_items = "、".join(str(item) for item in details.get("inventory_items", []) if item) or "なし"
    nearby_blocks = "、".join(str(item) for item in details.get("nearby_blocks", []) if item) or "なし"
    peaceful_mobs = "、".join(str(item) for item in details.get("peaceful_mobs", []) if item) or "なし"
    haiku_tags = "、".join(str(item) for item in details.get("haiku_tags", []) if item) or "なし"
    held_item = details.get("held_item") or "なし"
    biome_traits = "、".join(str(item) for item in details.get("biome_traits", []) if item) or "なし"
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
        "注目したい取り合わせや場面の要約:\n"
        f"{irony_block}\n"
        "\n"
        "川柳ルール:\n"
        "- 3行で出力する\n"
        "- 1行目は5音、2行目は7音、3行目は5音。各行ともプラスマイナス1音まで許容\n"
        "- ひらがなかカタカナだけを使う。漢字、英字、数字、記号、句読点は使わない\n"
        "- 小さいゃゅょは前の文字と合わせて1音\n"
        "- 小さいっは1音\n"
        "- んは1音\n"
        "- ーは1音\n"
        "- 説明や前置きは書かない\n"
        "- 取り合わせや場面の焦点があれば、それを優先してよい\n"
        "- 候補や現在の状況にないアイテム、ブロック、Mob を勝手に出さない\n"
        "- 意味のない五十音並びやランダム文字列は禁止\n"
        "\n"
        "現在の状況:\n"
        f"- バイオーム: {details.get('biome', 'unknown')}\n"
        f"- バイオーム分類: {details.get('biome_group', 'unknown')}\n"
        f"- バイオーム特徴: {biome_traits}\n"
        f"- 周辺ブロック: {nearby_blocks}\n"
        f"- 平和なMob: {peaceful_mobs}\n"
        f"- 手持ち: {held_item}\n"
        f"- インベントリ: {inventory_items}\n"
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


def build_haiku_irony_messages(details: dict[str, object]) -> list[dict[str, str]]:
    feature_candidates = "\n".join(
        f"- {candidate}" for candidate in details.get("feature_candidates", []) if isinstance(candidate, str)
    ) or "- なし"
    candidate_tensions = "\n".join(
        f"- {candidate}" for candidate in details.get("candidate_tensions", []) if isinstance(candidate, str)
    ) or "- なし"
    haiku_tags = "、".join(str(item) for item in details.get("haiku_tags", []) if item) or "なし"
    inventory_items = "、".join(str(item) for item in details.get("inventory_items", []) if item) or "なし"
    nearby_blocks = "、".join(str(item) for item in details.get("nearby_blocks", []) if item) or "なし"
    peaceful_mobs = "、".join(str(item) for item in details.get("peaceful_mobs", []) if item) or "なし"
    user_prompt = (
        "以下の Minecraft 状況から、川柳の焦点になる『関係のある取り合わせ・印象的な場面』を1つだけ選ぶ。\n"
        "弱ければ found=false にする。\n"
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
        f"- 手持ち: {details.get('held_item', 'なし')}\n"
        f"- インベントリ: {inventory_items}\n"
        f"- 周辺ブロック: {nearby_blocks}\n"
        f"- 平和なMob: {peaceful_mobs}\n"
        f"- 詩語ヒント: {haiku_tags}\n"
        "\n"
        "与えられた状況にないアイテム、ブロック、Mob を書かない。\n"
        "無理に矛盾を作らず、関係が弱ければ found=false を返す。"
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
