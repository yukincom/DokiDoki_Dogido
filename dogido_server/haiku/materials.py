"""workshop 用材料スナップショットと句断片リンク（汎用）。

句テキストに制御タグは埋め込まない。
候補は短い表示名、リンクは surface（句側）→ material（材料側）の対応表。
"""

from __future__ import annotations

import re
from typing import Any


def build_workshop_materials_seed(
    *,
    interpretation: str | None = None,
    biome: str | None = None,
    structure: str | None = None,
    time_phase: str | None = None,
    motifs: list[str] | tuple[str, ...] | None = None,
    held_item: str | None = None,
    nearby_blocks: list[str] | tuple[str, ...] | None = None,
    passive_mobs: list[str] | tuple[str, ...] | None = None,
    focus: list[str] | tuple[str, ...] | None = None,
    elements: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """発句時点の材料シード。ドメイン固有の禁止リストは持たない。"""
    materials: dict[str, Any] = {}
    if interpretation and str(interpretation).strip():
        materials["interpretation"] = str(interpretation).strip()
    if biome:
        materials["biome"] = str(biome).removeprefix("minecraft:")
    if structure:
        materials["structure"] = str(structure).removeprefix("minecraft:")
    if time_phase:
        materials["time_phase"] = str(time_phase)

    merged_motifs: list[str] = []
    for group in (motifs, focus, elements):
        if not group:
            continue
        for item in group:
            t = _clean_label(str(item) if item else "")
            if t and t not in merged_motifs:
                merged_motifs.append(t)
    if merged_motifs:
        materials["motifs"] = merged_motifs

    held = _clean_label(held_item or "")
    if held and held not in {"なし", "無し"}:
        materials["held_item"] = held

    blocks: list[str] = []
    for item in nearby_blocks or ():
        t = _clean_label(str(item) if item else "")
        if t and t not in blocks:
            blocks.append(t)
    if blocks:
        materials["nearby_blocks"] = blocks

    mobs: list[str] = []
    for item in passive_mobs or ():
        t = _clean_label(str(item) if item else "")
        if t and t not in mobs:
            mobs.append(t)
    if mobs:
        materials["passive_mobs"] = mobs

    _attach_ja_labels(materials)
    return materials


def enrich_materials_labels(materials: dict[str, Any]) -> dict[str, Any]:
    """biome_ja / structure_ja を可能な範囲で付与（破壊的に materials を更新）。"""
    _attach_ja_labels(materials)
    return materials


def short_material_entries(materials: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(表示名, source) の短い候補。ask_meaning / fragment_links 共通。"""
    materials = materials or {}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str | None, source: str, *, max_len: int = 18) -> None:
        t = _clean_label(label or "")
        if not t or len(t) < 2:
            return
        if len(t) > max_len:
            t = _shorten_noun(t, max_len=max_len)
        if not t or t in seen or not _looks_like_material_noun(t):
            return
        seen.add(t)
        out.append((t, source))

    for m in materials.get("motifs") or []:
        add(str(m), "motif")
    add(str(materials.get("held_item") or ""), "held_item")
    for b in materials.get("nearby_blocks") or []:
        add(str(b), "nearby_block")
    for m in materials.get("passive_mobs") or []:
        add(str(m), "passive_mob")

    for key, source in (
        ("biome_ja", "biome"),
        ("structure_ja", "structure"),
        ("place_ja", "place"),
    ):
        add(str(materials.get(key) or ""), source)

    phase = materials.get("time_phase")
    phase_ja = {
        "morning": "朝",
        "day": "昼",
        "evening": "夕方",
        "night": "夜",
    }.get(str(phase or ""), None)
    add(phase_ja, "time_phase")

    try:
        from dogido_server.entry_catalog import biome_labels, structure_labels

        biome_id = materials.get("biome")
        if biome_id and not materials.get("biome_ja"):
            labels = biome_labels()
            add(labels.get(str(biome_id)) or labels.get(str(biome_id).removeprefix("minecraft:")), "biome")
        struct_id = materials.get("structure")
        if struct_id and not materials.get("structure_ja"):
            sl = structure_labels()
            sid = str(struct_id).removeprefix("minecraft:")
            add(sl.get(sid) or sl.get(str(struct_id)), "structure")
    except Exception:  # noqa: BLE001
        pass

    # 解釈文は名詞っぽい条片だけ（講義文・述語だけの欠片は捨てる）
    interpretation = str(materials.get("interpretation") or "").strip()
    if interpretation:
        for chunk in _split_chunks(interpretation):
            add(chunk, "interpretation", max_len=16)

    return out


def build_fragment_links(
    verse: str,
    materials: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """句と材料の対応表。かな部分一致の汎用リンクのみ（詩的飛躍はここではやらない）。"""
    verse = (verse or "").strip()
    if not verse:
        return []
    verse_h = _compact_kana(verse)
    if len(verse_h) < 2:
        return []

    phrases = _verse_phrases(verse)
    links: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for label, source in short_material_entries(materials):
        mh = _compact_kana(label)
        if len(mh) < 2:
            continue
        surface: str | None = None
        if mh in verse_h:
            surface = _best_phrase_for_needle(phrases, mh) or label
        else:
            # 材料の一部が句に含まれる（短すぎる1文字は除外）
            for n in range(min(len(mh), 6), 1, -1):
                for i in range(len(mh) - n + 1):
                    bit = mh[i : i + n]
                    if len(bit) < 2:
                        continue
                    if bit in verse_h:
                        surface = _best_phrase_for_needle(phrases, bit) or bit
                        break
                if surface:
                    break
        if not surface:
            continue
        key = (_compact_kana(surface), _compact_kana(label))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        links.append(
            {
                "surface": surface,
                "material": label,
                "source": source,
            }
        )
    return links


def resolve_material_from_links(
    player_text: str,
    verse: str,
    materials: dict[str, Any] | None,
    *,
    fragment: str | None = None,
) -> str | None:
    """fragment_links から材料を解決。無ければ None。

    プローブは player_text / fragment のみ。句全体を総なめすると無関係な link に誤爆する。
    verse 引数は将来の surface 再計算用に残す（現状は未使用）。
    """
    del verse  # API 互換。句側の再分解はここではしない
    materials = materials or {}
    links = materials.get("fragment_links")
    if not isinstance(links, list) or not links:
        return None
    probes: list[str] = []
    if fragment and fragment.strip():
        probes.append(fragment.strip())
    if player_text and player_text.strip():
        probes.append(player_text.strip())
    if not probes:
        return None

    best: str | None = None
    best_score = 0
    for probe in probes:
        ph = _compact_kana(probe)
        if len(ph) < 2:
            continue
        for link in links:
            if not isinstance(link, dict):
                continue
            surface = str(link.get("surface") or "")
            material = str(link.get("material") or "")
            sh = _compact_kana(surface)
            mh = _compact_kana(material)
            score = 0
            if ph == sh or ph == mh:
                score = 30
            elif len(ph) >= 2 and (ph in sh or sh in ph):
                score = 20 + min(len(ph), len(sh))
            elif len(ph) >= 2 and (ph in mh or mh in ph):
                score = 12 + min(len(ph), len(mh))
            if score > best_score:
                best_score = score
                best = material
    return best if best_score >= 12 else None


def attach_fragment_links(materials: dict[str, Any], verse: str) -> dict[str, Any]:
    """materials に fragment_links を付与して返す。"""
    mats = dict(materials or {})
    links = build_fragment_links(verse, mats)
    if links:
        mats["fragment_links"] = links
    return mats


# --- internals ---


def _attach_ja_labels(materials: dict[str, Any]) -> None:
    try:
        from dogido_server.entry_catalog import biome_labels, structure_labels

        biome_id = materials.get("biome")
        if biome_id and not materials.get("biome_ja"):
            labels = biome_labels()
            ja = labels.get(str(biome_id)) or labels.get(str(biome_id).removeprefix("minecraft:"))
            if ja:
                materials["biome_ja"] = ja
        struct_id = materials.get("structure")
        if struct_id and not materials.get("structure_ja"):
            sl = structure_labels()
            sid = str(struct_id).removeprefix("minecraft:")
            ja = sl.get(sid) or sl.get(str(struct_id))
            if ja:
                materials["structure_ja"] = ja
    except Exception:  # noqa: BLE001
        pass


def _clean_label(s: str) -> str:
    t = (s or "").strip().rstrip("。．.")
    t = t.replace("プレイヤー", "あんた")
    return t


def _shorten_noun(text: str, *, max_len: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_len:
        return cleaned
    for sep in ("の", "、", "，", " "):
        if sep in cleaned:
            # 末尾の名詞寄り（オークの原木 → 原木 より オークの原木を短く切る）
            parts = cleaned.split(sep)
            for tail in reversed(parts):
                tail = tail.strip()
                if 2 <= len(tail) <= max_len:
                    return tail
            first = parts[0].strip()
            if 2 <= len(first) <= max_len:
                return first
    return cleaned[: max_len - 1] + "…"


def _looks_like_material_noun(text: str) -> bool:
    """述語だけの欠片を捨てる汎用ヒューリスティック。"""
    t = text.strip()
    if len(t) < 2:
        return False
    # 解釈分割のゴミ
    if t in {"と", "や", "が", "を", "に", "は", "も"}:
        return False
    if re.fullmatch(r".*(ている|でいる|ていた|である)$", t) and len(t) <= 12:
        # 「向き合っている」等。漢字を含む長い名詞句は許可
        if not re.search(r"[\u4e00-\u9fff]", t) or len(t) <= 8:
            return False
    if re.fullmatch(r"[ぁ-んー]{1,3}", t):
        # 短すぎるひらがなのみ（「いる」「ただ」）
        if t in {"いる", "ただ", "して", "ある", "する", "なる", "よう"}:
            return False
    return True


def _split_chunks(text: str) -> list[str]:
    parts = re.split(r"[、，。・/／]|と、|と", text)
    return [p.strip() for p in parts if p and len(p.strip()) >= 2]


def _verse_phrases(verse: str) -> list[str]:
    return [
        p.strip()
        for p in verse.replace("　", " ").replace("\n", " ").split()
        if p.strip()
    ]


def _kana_fold(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def _compact_kana(text: str) -> str:
    t = _kana_fold(text or "")
    for ch in ("\n", " ", "　", "、", "。", "？", "?", "！", "!", "「", "」", "『", "』", "・", "…"):
        t = t.replace(ch, "")
    return t


def _best_phrase_for_needle(phrases: list[str], needle_h: str) -> str | None:
    best: str | None = None
    best_score = 0
    for phrase in phrases:
        ph = _compact_kana(phrase)
        if not ph:
            continue
        if needle_h in ph or ph in needle_h:
            score = len(ph)
            if score > best_score:
                best_score = score
                best = phrase
    return best
