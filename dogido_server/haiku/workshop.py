"""川柳ワークショップ: 発句の pin（付箋）と open/close。

会話履歴（5往復）とは別に、セッション上に「いまの句」を保持する。
lifecycle だけをここに置き、発句本体（mixins/haiku.py）には混ぜない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from typing import Any

from dogido_server.memory_types import HaikuEmission
from dogido_server.llm.haiku import count_japanese_sounds, haiku_line_failure_reasons
from dogido_server.tts_reading import hiraganize_japanese_text, katakana_to_hiragana

from .edit_contract import (
    PLAYER_LINE_EDIT_CONTRACT_VERSION,
    line_edit_plan_applies,
)

# 発句からの最大 open 時間
DEFAULT_T_OPEN = timedelta(seconds=240)
# 句関連の最後のやり取りからの無活動
DEFAULT_T_IDLE = timedelta(seconds=120)
# 句と無関係な入力が連続したら close
DEFAULT_N_DRIFT = 2

# H7-lite: soft_default の intent 補助と、既知講評を含む対象行・問題箇所の
# structured 抽出だけに使う。close / clear_lessons / revise / reading は含めない。
WORKSHOP_LLM_INTENTS = frozenset(
    {
        "ask_meaning",
        "critique_forced",
        "critique_gibberish",
        "critique_offscene",
        "praise",
        "ack",
        "other_haiku",
        "request_repair",
        "soft_default",
    }
)
WORKSHOP_LLM_MIN_CONFIDENCE = 0.75
WORKSHOP_FINDING_MIN_CONFIDENCE = 0.65
WORKSHOP_PROBLEM_TYPES = frozenset(
    {
        "unnatural_japanese",
        "unreadable",
        "forced_compression",
        "off_scene",
        "meter",
        "reading",
        "preference",
        "other",
    }
)

_PENDING_REVISION_REJECT_PATTERN = re.compile(
    r"^(?:うん[、, ]*)?(?:やっぱり[、, ]*)?(?:"
    r"やめとく|やめておく|"
    r"(?:元|もと|前)のまま(?:で(?:いい|ええ)?|に(?:する|しとく))?|"
    r"その案(?:は)?なし"
    r")[。！!]*$"
)
_PENDING_REVISION_ACCEPT_PATTERN = re.compile(
    r"^(?:うん[、, ]*)?(?:"
    r"それでいこう|それで行こう|それでいい|それでええ|"
    r"その案で(?:いこう|行こう|いい|ええ|お願い(?:します)?)?|"
    r"採用(?:する|で)?"
    r")[。！!]*$"
)

_EXPLICIT_LINE_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (0, re.compile(r"(?:一|1)行目|上五|上の句|最初の行")),
    (1, re.compile(r"(?:二|2)行目|中七|中の句|真ん中の行")),
    (2, re.compile(r"(?:三|3)行目|下五|下の句|最後の行")),
)
_PLAYER_REPLACEMENT_PATTERNS = (
    re.compile(
        r"(?P<value>[^、，。！？!?\n]{1,48}?)(?:に|へ)"
        r"(?:変えた|かえた|変える|かえる|した)"
        r"(?:方|ほう)が(?:いい|ええ|良い|よい)"
    ),
    re.compile(
        r"(?P<value>[^、，。！？!?\n]{1,48}?)(?:に|へ)"
        r"(?:"
        r"(?:変えて|かえて)(?=$|[。！!]|(?:ほしい|ください|くれる|みて|みよう))|"
        r"してみて(?=$|[。！!]|(?:ほしい|ください|みよう))|"
        r"したら(?=$|[。！!]|(?:いい|ええ|どう))"
        r")"
    ),
    re.compile(
        r"(?P<value>[^、，。！？!?\n]{1,48}?)(?:の方|のほう)が"
        r"(?:いい|ええ|良い|よい)"
    ),
)
_PLAYER_REPLACEMENT_NEGATION = re.compile(
    r"(?:方|ほう)が(?:いい|ええ|良い|よい)(?:とは|わけ(?:では|じゃ)?)"
    r".{0,12}(?:ない|へん|思わん|おもわん|言ってない|いってない)"
)
_PLAYER_REPLACEMENT_REPORT = re.compile(
    r"(?:と|って)(?:言われた|いわれた|聞いた|きいた|書いてある|かいてある)"
)
_STRICT_HIRAGANA_LINE = re.compile(r"[\u3041-\u3096ー]+")


@dataclass(frozen=True, slots=True)
class WorkshopFinding:
    line_index: int | None
    fragment: str
    problem: str
    note: str
    confidence: float

    def to_dict(self) -> dict[str, object]:
        return {
            "line_index": self.line_index,
            "fragment": self.fragment,
            "problem": self.problem,
            "note": self.note,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class WorkshopAnalysis:
    intent: str = "soft_default"
    confidence: float = 0.0
    repair_requested: bool = False
    findings: tuple[WorkshopFinding, ...] = ()


@dataclass(frozen=True, slots=True)
class PlayerLineReplacement:
    """プレイヤーが明示した一行分の置換語。行番号はコード抽出時だけ入る。"""

    text: str
    explicit_line_index: int | None = None


@dataclass(frozen=True, slots=True)
class PlayerLineReplacementParse:
    """置換らしい発話と、採用可能な一意の置換を区別する。"""

    status: str
    replacement: PlayerLineReplacement | None = None


@dataclass(frozen=True, slots=True)
class PlayerLineRevisionResult:
    """LLMを通さず組み立てた、未保存の局所編集結果。"""

    text: str | None
    base_text: str
    edits: tuple[dict[str, object], ...] = ()
    failure_reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class RecentHaikuWorkshop:
    """セッション用 pin。closed 後は open=False（または session 側で None）。"""

    surface_text: str
    emitted_at: datetime
    entry_id: str | None = None
    preface: str | None = "ここで一句。"
    interpretation: str | None = None
    materials: dict[str, Any] = field(default_factory=dict)
    biome: str | None = None
    structure: str | None = None
    time_phase: str | None = None
    open: bool = True
    last_workshop_at: datetime | None = None
    drift_count: int = 0
    close_reason: str | None = None
    # 講評抽出と修正案は会話履歴とは分離。修正案はプレイヤーが採用するまで
    # 元句や memory を上書きしない。
    last_findings: list[dict[str, object]] = field(default_factory=list)
    pending_revision: str | None = None
    pending_revision_line_sources: list[dict[str, object]] = field(default_factory=list)
    pending_revision_base_text: str | None = None
    pending_revision_edits: list[dict[str, object]] = field(default_factory=list)
    pending_revision_edit_contract: str | None = None
    pending_revision_source: str | None = None
    current_revision_id: str | None = None
    marked_line_index: int | None = None

    def display_line(self) -> str:
        """明示採用済みの現在句。pending案とは混ぜない。"""

        return (self.surface_text or "").strip()

    def editing_line(self) -> str:
        """対話・次の局所編集で見せる最新版（未採用案があればそちら）。"""

        return (self.pending_revision or self.surface_text or "").strip()


def open_from_emission(
    emission: HaikuEmission,
    *,
    materials: dict[str, Any] | None = None,
    entry_id: str | None = None,
    now: datetime | None = None,
) -> RecentHaikuWorkshop:
    """発句成功時に pin を立てる。"""
    at = now or emission.created_at
    # emission.materials（厚いシード + fragment_links）を優先。明示 materials があれば上書き合成。
    mats: dict[str, Any] = {}
    if getattr(emission, "materials", None):
        mats.update(dict(emission.materials or {}))
    if materials:
        mats.update(dict(materials))
    if emission.interpretation and "interpretation" not in mats:
        mats["interpretation"] = emission.interpretation
    if emission.biome and "biome" not in mats:
        mats["biome"] = emission.biome
    if emission.structure and "structure" not in mats:
        mats["structure"] = emission.structure
    if emission.time_phase and "time_phase" not in mats:
        mats["time_phase"] = emission.time_phase
    initial_text = normalize_workshop_verse(emission.text) or (emission.text or "").strip()
    return RecentHaikuWorkshop(
        surface_text=initial_text,
        emitted_at=at,
        entry_id=entry_id,
        preface=emission.preface,
        interpretation=emission.interpretation,
        materials=mats,
        biome=emission.biome,
        structure=emission.structure,
        time_phase=emission.time_phase,
        open=True,
        last_workshop_at=at,
        drift_count=0,
        close_reason=None,
    )


def is_open(workshop: RecentHaikuWorkshop | None) -> bool:
    return workshop is not None and bool(workshop.open)


def close_workshop(
    workshop: RecentHaikuWorkshop | None,
    *,
    reason: str,
) -> RecentHaikuWorkshop | None:
    """pin を閉じる。オブジェクトは返す（ログ用）。session は None にしてよい。"""
    if workshop is None:
        return None
    workshop.open = False
    workshop.close_reason = reason
    return workshop


def pending_revision_is_current(workshop: RecentHaikuWorkshop) -> bool:
    """未保存差分が、いま pin されている元句へだけ適用できるか確認する。"""

    base_text = (workshop.pending_revision_base_text or "").strip()
    revised_text = (workshop.pending_revision or "").strip()
    if not base_text or base_text != workshop.display_line() or not revised_text:
        return False
    return line_edit_plan_applies(
        original_text=base_text,
        revised_text=revised_text,
        edit_contract=workshop.pending_revision_edit_contract,
        edits=workshop.pending_revision_edits,
    )


def clear_pending_revision(workshop: RecentHaikuWorkshop) -> None:
    """未採用案だけを捨てる。現在句と長期記憶は変更しない。"""

    workshop.pending_revision = None
    workshop.pending_revision_line_sources.clear()
    workshop.pending_revision_base_text = None
    workshop.pending_revision_edits.clear()
    workshop.pending_revision_edit_contract = None
    workshop.pending_revision_source = None


def advance_workshop_revision(
    workshop: RecentHaikuWorkshop,
    *,
    revision_id: str | None,
) -> None:
    """採用済みpendingを現在句へ昇格し、次の行を続けて直せるようにする。"""

    revised = (workshop.pending_revision or "").strip()
    if not revised:
        return
    line_sources = list(workshop.pending_revision_line_sources)
    source = workshop.pending_revision_source
    workshop.surface_text = revised
    workshop.current_revision_id = revision_id
    workshop.marked_line_index = None
    workshop.last_findings.clear()
    clear_pending_revision(workshop)
    if source == "generated_confirmed" and line_sources:
        workshop.materials["line_sources"] = line_sources
    else:
        # プレイヤーの語を観測atomへ偽装しない。以後のAI修正は出典不足ならfail closed。
        workshop.materials.pop("line_sources", None)


def record_workshop_activity(
    workshop: RecentHaikuWorkshop,
    *,
    now: datetime,
) -> None:
    """句関連のやり取りがあったとき。"""
    workshop.last_workshop_at = now
    workshop.drift_count = 0


def record_drift(
    workshop: RecentHaikuWorkshop,
    *,
    now: datetime,
    n_drift: int = DEFAULT_N_DRIFT,
) -> RecentHaikuWorkshop | None:
    """句と無関係な入力。連続 N 回で close。閉じたら workshop を返す（open=False）。"""
    workshop.drift_count += 1
    if workshop.drift_count >= n_drift:
        return close_workshop(workshop, reason="drift")
    return workshop


def maybe_close_for_time(
    workshop: RecentHaikuWorkshop | None,
    *,
    now: datetime,
    t_open: timedelta = DEFAULT_T_OPEN,
    t_idle: timedelta = DEFAULT_T_IDLE,
) -> RecentHaikuWorkshop | None:
    """時間切れで close。変化なければそのまま返す。"""
    if not is_open(workshop) or workshop is None:
        return workshop
    if now - workshop.emitted_at >= t_open:
        return close_workshop(workshop, reason="timeout_open")
    last = workshop.last_workshop_at or workshop.emitted_at
    if now - last >= t_idle:
        return close_workshop(workshop, reason="timeout_idle")
    return workshop


def workshop_prompt_details(workshop: RecentHaikuWorkshop | None) -> dict[str, str]:
    """player_chat / workshop 返事用に details へ足す短いブロック。"""
    if not is_open(workshop) or workshop is None:
        return {
            "haiku_workshop_open": "",
            "haiku_workshop_text": "",
            "haiku_workshop_materials": "",
        }
    return {
        "haiku_workshop_open": "1",
        "haiku_workshop_text": workshop.editing_line(),
        "haiku_workshop_materials": materials_speech_line(workshop),
    }


def materials_speech_line(workshop: RecentHaikuWorkshop) -> str:
    """プレイヤー向けの短い狙い文。内部キー名（biome: 等）は絶対に出さない。

    純最短だと「静寂」「昼」が斧・原木に勝つので、source 優先で具体物を選ぶ。
    """
    from dogido_server.haiku.materials import short_material_entries

    mats = dict(workshop.materials or {})
    if workshop.interpretation and "interpretation" not in mats:
        mats["interpretation"] = workshop.interpretation
    if workshop.biome and "biome" not in mats:
        mats["biome"] = workshop.biome
    if workshop.structure and "structure" not in mats:
        mats["structure"] = workshop.structure
    if workshop.time_phase and "time_phase" not in mats:
        mats["time_phase"] = workshop.time_phase
    entries = short_material_entries(mats)
    if not entries:
        return ""
    # held / nearby / structure / motif を抽象語・解釈条片より先に
    source_rank = {
        "held_item": 0,
        "nearby_block": 1,
        "structure": 2,
        "motif": 3,
        "passive_mob": 4,
        "biome": 5,
        "place": 6,
        "time_phase": 7,
        "interpretation": 8,
    }

    def rank(item: tuple[str, str]) -> tuple:
        label, source = item
        # 2 文字の雰囲気語（静寂・温もり等）は concrete より後
        abstract_short = len(label) <= 2
        return (
            source_rank.get(source, 9),
            abstract_short,
            len(label) > 20,
            len(label),
            label,
        )

    return min(entries, key=rank)[0]


def materials_debug_line(workshop: RecentHaikuWorkshop) -> str:
    """ログ用。生 materials（メタキー込み）を短く。"""
    materials = workshop.materials or {}
    parts: list[str] = []
    interpretation = str(
        materials.get("interpretation") or workshop.interpretation or ""
    ).strip()
    if interpretation:
        parts.append(interpretation[:80])
    for key in ("biome", "structure", "time_phase", "place", "held_item"):
        val = materials.get(key) or getattr(workshop, key, None)
        if val:
            parts.append(f"{key}={val}")
    motifs = materials.get("motifs")
    if isinstance(motifs, (list, tuple)) and motifs:
        parts.append("motifs=" + ",".join(str(m) for m in motifs[:4] if m))
    nearby = materials.get("nearby_blocks")
    if isinstance(nearby, (list, tuple)) and nearby:
        parts.append("nearby=" + ",".join(str(b) for b in nearby[:3] if b))
    links = materials.get("fragment_links")
    if isinstance(links, list) and links:
        parts.append(f"links={len(links)}")
    return " / ".join(parts) if parts else ""


def material_candidates_for_speech(workshop: RecentHaikuWorkshop) -> list[str]:
    """「それは〇〇やで」用の候補。日本語の中身だけ（キー名なし）。

    短い具体物（motifs / held / nearby / biome_ja）を先に、長い解釈文は後。
    ドメイン固有の禁止語リストは持たない（materials.short_material_entries に委譲）。
    """
    from dogido_server.haiku.materials import short_material_entries

    mats = dict(workshop.materials or {})
    if workshop.interpretation and "interpretation" not in mats:
        mats["interpretation"] = workshop.interpretation
    if workshop.biome and "biome" not in mats:
        mats["biome"] = workshop.biome
    if workshop.structure and "structure" not in mats:
        mats["structure"] = workshop.structure
    if workshop.time_phase and "time_phase" not in mats:
        mats["time_phase"] = workshop.time_phase
    return [label for label, _source in short_material_entries(mats)]


def pick_material_for_fragment(
    fragment: str | None,
    workshop: RecentHaikuWorkshop,
    *,
    player_text: str | None = None,
) -> str | None:
    """LLM 失敗時のフォールバック。fragment_links 優先、なければ部分一致。"""
    from dogido_server.haiku.materials import resolve_material_from_links

    verse = workshop.editing_line() or ""
    linked = resolve_material_from_links(
        player_text or "",
        verse,
        workshop.materials,
        fragment=fragment,
    )
    if linked:
        return linked

    frag = _compact_kana(fragment or "")
    if len(frag) < 2:
        return None
    cands = material_candidates_for_speech(workshop)
    if not cands:
        return None
    hits: list[str] = []
    for cand in cands:
        m = _compact_kana(cand)
        if len(m) < 2:
            continue
        if frag in m or m in frag:
            hits.append(cand)
    if not hits:
        return None
    # 短い具体語を優先
    return min(hits, key=lambda s: (len(s), s.count("の")))


def build_ask_meaning_llm_details(
    workshop: RecentHaikuWorkshop,
    player_text: str,
) -> dict[str, Any]:
    """structured material pick 用の details（候補はコードが閉じる）。"""
    verse = workshop.editing_line() or ""
    verse_one = " ".join(verse.replace("\n", " ").split())
    fragment = _quoted_or_fragment_about_verse(player_text or "", verse)
    candidates = material_candidates_for_speech(workshop)
    return {
        "verse": verse_one,
        "player_text": (player_text or "").strip(),
        "fragment": fragment or "",
        "candidates": candidates,
    }


def finalize_ask_meaning_reply(
    workshop: RecentHaikuWorkshop,
    player_text: str,
    payload: dict[str, Any] | None,
) -> tuple[str, str]:
    """LLM 出力を軽く整えて返事にする。戻り値は (reply, path)。

    path: llm | template | soft_fail
    検証は緩め（言い回しの面白さを潰さない）。schema 漏れと長文だけ切る。
    """
    verse = workshop.editing_line() or "（句なし）"
    verse_one = " ".join(verse.replace("\n", " ").split())
    said = player_text or ""
    fragment = _quoted_or_fragment_about_verse(said, verse)
    candidates = material_candidates_for_speech(workshop)

    pick: str | None = None
    raw_reply = ""
    if isinstance(payload, dict):
        raw_reply = str(payload.get("reply") or "").strip()
        idx = payload.get("pick_index")
        if isinstance(idx, bool):
            idx = None
        if isinstance(idx, float) and idx == int(idx):
            idx = int(idx)
        if isinstance(idx, int) and 0 <= idx < len(candidates):
            pick = candidates[idx]
        elif isinstance(idx, str) and idx.isdigit():
            i = int(idx)
            if 0 <= i < len(candidates):
                pick = candidates[i]

    accepted = _soft_accept_ask_meaning_reply(raw_reply, candidates=candidates, pick=pick)
    if accepted:
        return accepted, "llm"

    if pick:
        return f"それは、{pick}やで。", "template"

    # fragment_links（句断片→材料）を優先。無ければ部分一致。
    simple = pick_material_for_fragment(fragment, workshop, player_text=said)
    if simple:
        return f"それは、{simple}やで。", "template"

    if fragment:
        return (
            f"「{fragment}」の読みやね。ちょっと分かりにくかったかも。",
            "soft_fail",
        )
    return (
        f"どの言葉？「{verse_one}」のどこが気になった？",
        "soft_fail",
    )


def _soft_accept_ask_meaning_reply(
    reply: str,
    *,
    candidates: list[str],
    pick: str | None,
) -> str | None:
    """講義・メタ漏れ・過長だけ落とす。口調や言い換えは通す。"""
    t = (reply or "").strip().strip("「」\"'")
    if not t:
        return None
    # 1〜2 文想定。縛りすぎない（約 80 字）
    if len(t) > 80:
        return None
    lowered = t.lower()
    meta_needles = (
        "biome:",
        "structure:",
        "minecraft:",
        "materials",
        "interpretation",
        "pick_index",
        "village_plains",
        "biome=",
        "structure=",
    )
    if any(n in lowered or n in t for n in meta_needles):
        return None
    # 候補外の捏造をやや抑える: pick あり / 候補語入り / 正直に読みにくい なら OK
    if pick is not None:
        return t
    if any(len(c) >= 2 and c in t for c in candidates):
        return t
    soft_fail_marks = (
        "分かりにく",
        "わかりにく",
        "読め",
        "わから",
        "どの言葉",
        "ちょっと",
        "読みや",
    )
    if any(m in t for m in soft_fail_marks):
        return t
    return None


def _split_material_chunks(text: str) -> list[str]:
    import re

    parts = re.split(r"[、，。・/／]|と、|と", text)
    return [p.strip() for p in parts if p and len(p.strip()) >= 2]


def _shorten_for_speech(text: str, *, max_chars: int = 36) -> str:
    cleaned = text.strip().rstrip("。．.！!？?")
    if not cleaned:
        return ""
    cleaned = cleaned.replace("プレイヤー", "あんた")
    if len(cleaned) <= max_chars:
        return cleaned
    for sep in ("、", "，", "。"):
        if sep in cleaned:
            first = cleaned.split(sep, 1)[0].strip()
            if 6 <= len(first) <= max_chars:
                return first
            if len(first) > max_chars:
                return first[: max_chars - 1].rstrip("、， ") + "…"
    return cleaned[: max_chars - 1].rstrip("、， ") + "…"


# --- 意図判定（ルール・初版） ---

_CLOSE_PATTERN = re.compile(
    r"^(?:(?:うん|はい)[、, ]*)?(?:"
    r"もう(?:ええ|いい)(?:わ|よ|で|です)?|"
    r"(?:次|つぎ)(?:いこ|行こ)(?:う|か)?|"
    r"わかった(?:よ|で|わ)?|おk(?:です)?|おけ(?:です)?|ok(?:です)?|よし|"
    r"了解(?:や|です|しました)?"
    r")[。！!]*$",
    re.IGNORECASE,
)
_PRAISE_PATTERN = re.compile(
    r"^(?:(?:うん|ほんまに|めっちゃ|なかなか|すごく)[、, ]*)?"
    r"(?:(?:これ|この句|その句|句)(?:は|が)?[、, ]*)?"
    r"(?:いい句|良い句|ええ句|うまい|上手|好き|気に入った|"
    r"そのままで(?:いい|ええ))"
    r"(?:やな|やね|やん|やで|やわ|や|だね|ですね|だ|です|な|ね|よ|わ|"
    r"(?:だ|や)?(?:と|って)思う|(?:だ|や)?(?:と|って)おもう)?[。！!]*$"
)
_CLEAR_LESSON_PATTERN = re.compile(
    r"^(?:(?:うん|はい)[、, ]*)?(?:もう[、, ]*)?(?:"
    r"気にせんで(?:ええ|いい)?(?:わ|よ|で)?|"
    r"気にし(?:なくて|んで)(?:ええ|いい)?(?:わ|よ|で)?|"
    r"(?:前の)?注意(?:は)?(?:もう[、, ]*)?(?:いらない|いらん)(?:わ|よ|で)?|"
    r"縛らんで(?:ええ|いい)?|(?:ゆるめて|緩めて)(?:ください)?|"
    r"前の注意やめて(?:ください)?"
    r")[。！!]*$"
)


def _matches_explicit_state_change(text: str, pattern: re.Pattern[str]) -> bool:
    """状態変更は引用・複合語を拾わず、閉じた明示形だけを許可する。"""

    normalized = text.strip()
    if not normalized or "?" in normalized or "？" in normalized:
        return False
    return pattern.fullmatch(normalized) is not None


_FORCED_MARKERS = ("無理やり", "詰め込み", "つめこみ", "圧縮", "息苦", "ごちゃごちゃ")
_GIBBERISH_MARKERS = ("読めん", "読めない", "わからん", "意味わから", "日本語", "何言", "なにい")
# 場面ずれ: 固有モチーフ名（海・村など）は入れない。メタな言い回しだけ。
_OFFSCENE_MARKERS = (
    "関係ない",
    "場違い",
    "場と違う",
    "場とちが",
    "場面と違う",
    "場面ちゃう",
    "場面じゃない",
    "ここちゃう",
    "ここじゃない",
    "ここやない",
    "空気ちゃう",
    "空気じゃない",
    "ずれてる",
    "ずれた",
    "外れとる",
    "はずれとる",
    "見当違い",
    "見当ちがい",
)
# 「意味」単独は「そういう意味か」に誤爆するので入れない
_MEANING_MARKERS = (
    "って何",
    "ってなに",
    "とは何",
    "とはなに",
    "何それ",
    "なにそれ",
    "何でしょう",
    "なんでしょう",
    "何でしょうか",
    "なんでしょうか",
    "って何だ",
    "ってなんだ",
    "って何だろ",
    "ってなんだろ",
    "意味わから",
    "意味がわから",
    "意味不明",
)
# 納得・相槌（再講義しない）
_ACK_MARKERS = (
    "なるほど",
    "そういう意味",
    "そういうことか",
    "そっか",
    "そうか",
    "せやな",
    "そうやな",
    "了解や",
)
_REPAIR_REQUEST_PATTERN = re.compile(
    r"^(?:(?:うん|じゃあ|なら)[、, ]*)?"
    r"(?:(?:これ|そこ|(?:この|その)?句|(?:一|二|三|1|2|3)行目|上の句|中の句|下の句)"
    r"(?:を|だけ)?[、, ]*)?"
    r"(?:直して|なおして|直そう|なおそう|直すかな|なおすかな|"
    r"直してみ(?:て)?|なおしてみ(?:て)?|直せる|なおせる|"
    r"修正して|しゅうせいして)"
    r"(?:ほしい|ください|くれる|もらえる|みよう)?[。！!？?]*$"
)
# 読みの好み（メタ語のみ。素材名・地名は禁止）
_READING_META_MARKERS = (
    "読み",
    "よみ",
    "読み方",
    "よみかた",
)
# 言い換え・好み（「AじゃなくB」「〜の方が」）。ドメイン語は見ない。
_PREFERENCE_MARKERS = (
    "じゃなく",
    "ではなく",
    "の方が",
    "のほうが",
    "方がいい",
    "方がええ",
    "方がよかった",
    "ほうがよかった",
    "方が良い",
    "追加して",
    "追加しと",
    "足して",
)
# Stage1: 字数・長さの指摘（メタ語のみ。固有モチーフ名は載せない）
_LENGTH_MARKERS = (
    "長い",
    "ながい",
    "短すぎ",
    "みじかすぎ",
    "字余り",
    "字足らず",
    "音数",
    "モーラ",
    "五七五",
    "575",
    "字数",
)
# Stage1: 言い換え提案の構文（「Aとかにしたら」「だったら」）
_SOFT_SUGGEST_MARKERS = (
    "にしたら",
    "にしてみ",
    "とかに",
    "だったら",
    "言い換え",
    "に変えて",
    "変えた方",
    "変えたほう",
)
# Stage1: 教訓・記憶の依頼
_REMEMBER_MARKERS = (
    "覚えて",
    "覚えと",
    "おぼえて",
    "おぼえと",
    "メモしと",
    "メモして",
)
# Stage1: 狙い・由来の問い（materials 寄り。固有名は見ない）
_ORIGIN_MARKERS = (
    "どこから来",
    "どこからき",
    "何を見て",
    "なにを見て",
    "狙いは",
    "材料は",
    "どこから",
)
# Stage1: 読み・日本語の疑い（「おかしい」「通じる」）
_SOFT_DOUBT_MARKERS = (
    "おかしい",
    "おかしく",
    "間違いか",
    "まちがいか",
    "通じる",
    "通じな",
    "変じゃ",
    "変や",
    "変だ",
)
# Stage2: open 中でも chat+drift に落とす「明確な別件」（ゲーム・挨拶）
# 固有モチーフを講評に使わない方針と両立するよう、ゲーム行為・挨拶に寄せる。
_HARD_OFF_TOPIC_MARKERS = (
    "松明",
    "たいまつ",
    "どこ行く",
    "どこいく",
    "どっち行く",
    "どっちいく",
    "おはよう",
    "こんにちは",
    "こんばんは",
    "インベントリ",
    "持ち物",
    "クラフト",
    "レシピ",
    "逃げよ",
    "逃げて",
    "戦って",
    "ゾンビ",
    "クリーパー",
    "スケルトン",
)
def wants_clear_haiku_lessons(user_text: str | None) -> bool:
    """「もう気にせんで」系。close（もうええ）とは別。"""
    text = (user_text or "").strip()
    if not text:
        return False
    return _matches_explicit_state_change(text, _CLEAR_LESSON_PATTERN)


def classify_workshop_intent(
    user_text: str,
    *,
    verse: str | None = None,
) -> str | None:
    """句関連なら kind、無関係なら None。

    kinds: close | praise | clear_lessons | request_repair | critique_forced |
           critique_gibberish | critique_offscene | ask_meaning | ack | other_haiku

    verse を渡すと「晴れのバラ?」のように句断片＋疑問を ask_meaning にできる。
    soft_default はここではなく workshop_open_intent（open 中のみ）。
    """
    text = (user_text or "").strip()
    if not text:
        return None
    # 明示緩めを close より先に（「もう気にせんで」に「もう」が含まれるため）
    if wants_clear_haiku_lessons(text):
        return "clear_lessons"
    if _matches_explicit_state_change(text, _CLOSE_PATTERN):
        return "close"
    if _matches_explicit_state_change(text, _PRAISE_PATTERN):
        return "praise"
    # 「こう直して: 完成句」は service が先に revision として抽出する。
    # 句本文のない「直すかな／直して」は、現在句の修正案を求める操作。
    if _REPAIR_REQUEST_PATTERN.fullmatch(text):
        return "request_repair"
    # 納得相槌は「意味」より先（「そういう意味か」誤爆防止）
    if any(m in text for m in _ACK_MARKERS):
        return "ack"
    if any(m in text for m in _FORCED_MARKERS):
        return "critique_forced"
    # 字数・長さ（詰め込み系に寄せる）
    if any(m in text for m in _LENGTH_MARKERS):
        return "critique_forced"
    if any(m in text for m in _OFFSCENE_MARKERS):
        return "critique_offscene"
    # 「〜って何／とは何／何でしょう」
    if any(m in text for m in _MEANING_MARKERS):
        return "ask_meaning"
    # 狙い・由来（材料の話）
    if any(m in text for m in _ORIGIN_MARKERS):
        return "ask_meaning"
    # 句の断片を指して疑問（「晴れのバラ?」「はれのばら？」）
    if verse and _looks_like_verse_fragment_question(text, verse):
        return "ask_meaning"
    if any(m in text for m in _GIBBERISH_MARKERS):
        return "critique_gibberish"
    # 読みメタ + 好み／言い換えパターン（素材名・地名は見ない）
    if any(m in text for m in _READING_META_MARKERS) and any(
        h in text for h in _PREFERENCE_MARKERS
    ):
        return "other_haiku"
    if any(m in text for m in _READING_META_MARKERS) and any(
        h in text for h in ("方が", "ほうが", "よかった", "良かった", "違う", "ちがう")
    ):
        return "other_haiku"
    # 「AじゃなくB」「〜の方が〜」「とかにしたら」など好み・訂正を、
    # 曖昧な「通じる」疑いより先に（言い換え提案を優先）
    if any(m in text for m in _PREFERENCE_MARKERS):
        return "other_haiku"
    if any(m in text for m in _SOFT_SUGGEST_MARKERS):
        return "other_haiku"
    if any(m in text for m in _REMEMBER_MARKERS):
        return "other_haiku"
    if any(m in text for m in _SOFT_DOUBT_MARKERS):
        return "critique_gibberish"
    # 句・川柳・俳句への明示参照（ジャンル語のみ）
    if any(m in text for m in ("句", "川柳", "俳句", "せんりゅう", "詠ん", "よんだ")):
        return "other_haiku"
    return None


def is_workshop_hard_off_topic(
    user_text: str | None,
    *,
    player_input: Any | None = None,
) -> bool:
    """open 中でも chat+drift に落とす明確な別件か。

    player_input のフラグ（インベントリ・敵数など）を優先し、
    なければゲーム行為・挨拶のメタ語のみ（固有モチーフ講評は吸わない）。
    """
    if player_input is not None:
        if bool(getattr(player_input, "asks_inventory", False)):
            return True
        if bool(getattr(player_input, "asks_hostile_count", False)):
            return True
        if bool(getattr(player_input, "asks_dragon_direction", False)):
            return True
        if bool(getattr(player_input, "asks_about_sound", False)):
            return True
    text = (user_text or "").strip()
    if not text:
        return False
    return any(m in text for m in _HARD_OFF_TOPIC_MARKERS)


def workshop_open_intent(
    user_text: str | None,
    *,
    verse: str | None = None,
    player_input: Any | None = None,
) -> str | None:
    """workshop **open 中**の取り込み判定。

    Returns:
        既知 kind（close / praise / … / other_haiku）
        ``soft_default`` … マーカー外だが別件でもない → 句の話として扱う
        ``None`` … hard off-topic（player_chat + drift 候補）
    """
    if extract_conversational_revise(user_text):
        # 呼び出し側は revise 経路を先に見る。ここは safety。
        return "other_haiku"
    kind = classify_workshop_intent(user_text or "", verse=verse)
    if kind is not None:
        return kind
    if is_workshop_hard_off_topic(user_text, player_input=player_input):
        return None
    text = (user_text or "").strip()
    if not text:
        return None
    return "soft_default"


def build_workshop_intent_llm_details(
    workshop: RecentHaikuWorkshop,
    player_text: str,
) -> dict[str, object]:
    """H7-lite の provider 非依存入力。

    ライフサイクルや保存判断は渡さず、句・短い狙い・プレイヤー発話だけを渡す。
    同じ契約を chat route、Apple Foundation Models、Foundry Local で使う。
    """
    lines = workshop_verse_lines(workshop.editing_line())
    return {
        "verse": "\n".join(lines),
        "verse_lines": [
            {"line_index": index, "text": line}
            for index, line in enumerate(lines)
        ],
        "materials_speech": materials_speech_line(workshop),
        "player_text": (player_text or "").strip(),
        "allowed_intents": sorted(WORKSHOP_LLM_INTENTS),
        "allowed_problem_types": sorted(WORKSHOP_PROBLEM_TYPES),
    }


def workshop_verse_lines(verse: str) -> list[str]:
    normalized = (verse or "").strip()
    if not normalized:
        return []
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if len(lines) == 1:
        space_parts = [part.strip() for part in normalized.split() if part.strip()]
        if len(space_parts) == 3:
            return space_parts
    return lines


def normalize_player_haiku_line(text: str | None) -> str | None:
    """プレイヤーの置換語をコードで読みへ展開し、ひらがなだけなら返す。"""

    source = str(text or "").strip().strip("「」『』\"' 。．.!！?？…")
    if not source or "\n" in source or "\r" in source:
        return None
    normalized = hiraganize_japanese_text(source)
    normalized = katakana_to_hiragana(normalized)
    normalized = re.sub(r"\s+", "", normalized).strip()
    if not normalized or _STRICT_HIRAGANA_LINE.fullmatch(normalized) is None:
        return None
    return normalized


def normalize_workshop_verse(text: str | None) -> str | None:
    """三行の句を、内容を作り替えずひらがな表記へ正規化する。"""

    lines = workshop_verse_lines(text or "")
    if len(lines) != 3:
        return None
    normalized = [normalize_player_haiku_line(line) for line in lines]
    if any(line is None for line in normalized):
        return None
    return "\n".join(str(line) for line in normalized)


def explicit_workshop_line_index(text: str | None) -> int | None:
    """上五／二行目など、コードで確定できる行指定だけを返す。"""

    source = str(text or "")
    matches = _explicit_workshop_line_indices(source)
    return next(iter(matches)) if len(matches) == 1 else None


def _explicit_workshop_line_indices(text: str | None) -> set[int]:
    source = str(text or "")
    return {
        line_index
        for line_index, pattern in _EXPLICIT_LINE_PATTERNS
        if pattern.search(source)
    }


def update_marked_workshop_line(
    workshop: RecentHaikuWorkshop,
    *,
    findings: tuple[WorkshopFinding, ...] = (),
    player_text: str | None = None,
) -> int | None:
    """明示行、または一意に検証済みのfindingだけを次の編集対象に固定する。"""

    explicit_indices = _explicit_workshop_line_indices(player_text)
    explicit = next(iter(explicit_indices)) if len(explicit_indices) == 1 else None
    if explicit is not None:
        workshop.marked_line_index = explicit
        return explicit
    if len(explicit_indices) > 1:
        workshop.marked_line_index = None
        return None
    targets = {finding.line_index for finding in findings if finding.line_index is not None}
    if len(targets) == 1:
        workshop.marked_line_index = next(iter(targets))
    elif len(targets) > 1:
        workshop.marked_line_index = None
    return workshop.marked_line_index


def parse_player_line_replacement(raw_text: str | None) -> PlayerLineReplacementParse:
    """置換発話を no_match / rejected / ambiguous / accepted へ閉じる。"""

    text = str(raw_text or "").strip()
    if not text:
        return PlayerLineReplacementParse("no_match")
    replacementish = any(pattern.search(text) for pattern in _PLAYER_REPLACEMENT_PATTERNS)
    if not replacementish:
        return PlayerLineReplacementParse("no_match")
    if _PLAYER_REPLACEMENT_NEGATION.search(text) or _PLAYER_REPLACEMENT_REPORT.search(text):
        return PlayerLineReplacementParse("rejected")
    if len(_explicit_workshop_line_indices(text)) > 1:
        return PlayerLineReplacementParse("ambiguous")
    candidates: list[str] = []
    for pattern in _PLAYER_REPLACEMENT_PATTERNS:
        for match in pattern.finditer(text):
            candidate = match.group("value").strip()
            quoted_parts = re.findall(r"[「『]([^」』]+)[」』]", candidate)
            had_quoted = len(quoted_parts) == 1
            if len(quoted_parts) == 1:
                candidate = quoted_parts[0].strip()
                had_line_prefix = False
            else:
                had_line_prefix = False
            line_prefix = re.compile(
                r"^(?:(?:一|二|三|1|2|3)行目|上五|中七|下五|上の句|中の句|下の句)"
                r"(?:は|を|だけ|なら)?[、， ]*"
            )
            had_line_prefix = had_line_prefix or line_prefix.match(candidate) is not None
            candidate = re.sub(
                line_prefix,
                "",
                candidate,
            ).strip()
            # 「元の語を新しい語に変える」「元より新しい語の方が」の左側を落とす。
            for separator in ("じゃなくて", "じゃなく", "ではなくて", "ではなく", "より", "から"):
                if separator in candidate:
                    candidate = candidate.rsplit(separator, 1)[-1].strip()
            quoted = re.fullmatch(r"[「『](.+)[」』]", candidate)
            if quoted:
                candidate = quoted.group(1).strip()
            elif (
                not had_line_prefix
                and not had_quoted
                and "を" in candidate
                and ("変え" in match.group(0) or "かえ" in match.group(0))
            ):
                candidate = candidate.rsplit("を", 1)[-1].strip()
            candidate = candidate.strip("「」『』\"' 、，:：")
            if candidate.endswith("とか"):
                candidate = candidate[:-2].rstrip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    if len(candidates) != 1:
        return PlayerLineReplacementParse("ambiguous")
    return PlayerLineReplacementParse(
        "accepted",
        PlayerLineReplacement(
            text=candidates[0],
            explicit_line_index=explicit_workshop_line_index(text),
        ),
    )


def extract_player_line_replacement(raw_text: str | None) -> PlayerLineReplacement | None:
    """互換用の単値helper。採用可能な一意の置換だけを返す。"""

    return parse_player_line_replacement(raw_text).replacement


def build_player_line_revision(
    workshop: RecentHaikuWorkshop,
    replacement: PlayerLineReplacement,
) -> PlayerLineRevisionResult:
    """明示語だけを対象行へ置き、現在句に対するCAS差分を作る。"""

    base_text = workshop.display_line()
    if workshop.pending_revision and workshop.pending_revision_source not in {
        None,
        "player_line_confirmed",
    }:
        return PlayerLineRevisionResult(
            None,
            base_text,
            failure_reasons=("pending_source_conflict",),
        )
    base_lines = workshop_verse_lines(base_text)
    draft_lines = workshop_verse_lines(workshop.editing_line())
    if len(base_lines) != 3 or len(draft_lines) != 3:
        return PlayerLineRevisionResult(None, base_text, failure_reasons=("invalid_verse",))
    if any(normalize_player_haiku_line(line) != line for line in base_lines + draft_lines):
        return PlayerLineRevisionResult(None, base_text, failure_reasons=("verse_not_hiragana",))
    target = replacement.explicit_line_index
    if target is None:
        target = workshop.marked_line_index
    if target not in (0, 1, 2):
        return PlayerLineRevisionResult(None, base_text, failure_reasons=("missing_target",))
    normalized = normalize_player_haiku_line(replacement.text)
    if normalized is None:
        return PlayerLineRevisionResult(None, base_text, failure_reasons=("not_hiragana",))
    reasons = list(haiku_line_failure_reasons(normalized, target, workshop.materials))
    # プレイヤーの明示編集は「新5-7-5」を作るため、±1ではなく対象音数に合わせる。
    if count_japanese_sounds(normalized) != (5, 7, 5)[target]:
        reasons.append("meter_not_exact")
    if any(
        index != target and _compact_kana(line) == _compact_kana(normalized)
        for index, line in enumerate(draft_lines)
    ):
        reasons.append("duplicate_line")
    if reasons:
        return PlayerLineRevisionResult(
            None,
            base_text,
            failure_reasons=tuple(dict.fromkeys(reasons)),
        )
    draft_lines[target] = normalized
    revised_text = "\n".join(draft_lines)
    if revised_text == workshop.editing_line():
        return PlayerLineRevisionResult(None, base_text, failure_reasons=("no_change",))
    edits = tuple(
        {
            "line_index": index,
            "expected_text": base_lines[index],
            "replacement_text": draft_lines[index],
            "provenance": "player_explicit",
        }
        for index in range(3)
        if base_lines[index] != draft_lines[index]
    )
    if not line_edit_plan_applies(
        original_text=base_text,
        revised_text=revised_text,
        edit_contract=PLAYER_LINE_EDIT_CONTRACT_VERSION,
        edits=edits,
    ):
        return PlayerLineRevisionResult(None, base_text, failure_reasons=("invalid_edit",))
    return PlayerLineRevisionResult(revised_text, base_text, edits=edits)


def wants_show_workshop_verse(text: str | None) -> bool:
    """現在の三行を尋ねる発話。本文はLLMではなくコードから返す。"""

    source = str(text or "").strip()
    if not source:
        return False
    return bool(
        re.search(
            r"(?:全体|全部|今の句|いまの句|直した句|修正した句)"
            r".{0,12}(?:どんな|どうな|見せて|みせて|読んで|よんで|言って|いって)",
            source,
        )
    )


def finalize_workshop_analysis_payload(
    payload: dict[str, object] | None,
    *,
    verse_lines: list[str] | None = None,
    min_confidence: float = WORKSHOP_LLM_MIN_CONFIDENCE,
    finding_min_confidence: float = WORKSHOP_FINDING_MIN_CONFIDENCE,
) -> WorkshopAnalysis:
    """OS / cloud 共通の講評抽出結果を閉じた値へ変換する。

    LLM が close・lesson解除・保存を実行する余地はない。対象行・問題種別も
    コードで範囲検査し、不明な finding は捨てる。
    """

    intent = _finalize_workshop_intent_payload(payload, min_confidence=min_confidence)
    if not isinstance(payload, dict):
        return WorkshopAnalysis(intent=intent)
    raw_confidence = payload.get("confidence")
    try:
        confidence = float(raw_confidence) if not isinstance(raw_confidence, bool) else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    findings: list[WorkshopFinding] = []
    raw_findings = payload.get("findings")
    if isinstance(raw_findings, list):
        for row in raw_findings[:3]:
            if not isinstance(row, dict):
                continue
            raw_index = row.get("line_index")
            line_index: int | None
            if raw_index is None:
                line_index = None
            elif isinstance(raw_index, int) and not isinstance(raw_index, bool) and raw_index in (0, 1, 2):
                line_index = raw_index
            else:
                continue
            if line_index is not None and verse_lines is not None and line_index >= len(verse_lines):
                continue
            fragment = str(row.get("fragment") or "").strip()[:40]
            if verse_lines is not None:
                folded_fragment = _compact_kana(fragment)
                matches = [
                    index
                    for index, line in enumerate(verse_lines)
                    if folded_fragment and folded_fragment in _compact_kana(line)
                ]
                # AIの行番号だけでは修正対象にしない。断片が一意に見つかった場合
                # だけ、コード側で行を確定する。同じ断片が複数行なら曖昧として落とす。
                line_index = matches[0] if len(matches) == 1 else None
            problem = str(row.get("problem") or "").strip()
            if problem not in WORKSHOP_PROBLEM_TYPES:
                continue
            raw_finding_confidence = row.get("confidence")
            if isinstance(raw_finding_confidence, bool):
                continue
            try:
                finding_confidence = float(raw_finding_confidence)
            except (TypeError, ValueError):
                continue
            if not 0.0 <= finding_confidence <= 1.0 or finding_confidence < finding_min_confidence:
                continue
            findings.append(
                WorkshopFinding(
                    line_index=line_index,
                    fragment=fragment,
                    problem=problem,
                    note=str(row.get("note") or "").strip()[:120],
                    confidence=finding_confidence,
                )
            )
    repair_requested = payload.get("repair_requested") is True or intent == "request_repair"
    return WorkshopAnalysis(
        intent=intent,
        confidence=confidence,
        repair_requested=repair_requested,
        findings=tuple(findings),
    )


def workshop_findings_from_records(
    rows: list[dict[str, object]] | None,
) -> tuple[WorkshopFinding, ...]:
    """セッション中に保存した検証済み finding を型へ戻す。"""

    findings: list[WorkshopFinding] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        index = row.get("line_index")
        if index is not None and (
            not isinstance(index, int) or isinstance(index, bool) or index not in (0, 1, 2)
        ):
            continue
        problem = str(row.get("problem") or "").strip()
        if problem not in WORKSHOP_PROBLEM_TYPES:
            continue
        try:
            confidence = float(row.get("confidence"))
        except (TypeError, ValueError):
            continue
        if not 0.0 <= confidence <= 1.0:
            continue
        findings.append(
            WorkshopFinding(
                line_index=index,
                fragment=str(row.get("fragment") or "").strip()[:40],
                problem=problem,
                note=str(row.get("note") or "").strip()[:120],
                confidence=confidence,
            )
        )
    return tuple(findings)


def repair_target_indices(findings: tuple[WorkshopFinding, ...]) -> tuple[int, ...]:
    """行を特定できた finding だけを、修正AIの対象へする。"""

    return tuple(sorted({finding.line_index for finding in findings if finding.line_index is not None}))


def pending_revision_decision(user_text: str | None) -> str | None:
    """提示済み修正案への明示的な採用・却下だけを拾う。"""

    text = (user_text or "").strip()
    if "?" not in text and "？" not in text and _PENDING_REVISION_REJECT_PATTERN.fullmatch(text):
        return "reject"
    # 部分一致だと「その案ではまだだめ」「その案でいい？」を誤採用する。
    if "?" not in text and "？" not in text and _PENDING_REVISION_ACCEPT_PATTERN.fullmatch(text):
        return "accept"
    return None


def _finalize_workshop_intent_payload(
    payload: dict[str, object] | None,
    *,
    min_confidence: float = WORKSHOP_LLM_MIN_CONFIDENCE,
) -> str:
    """structured 出力を閉じた enum と信頼度で検証する。

    soft_default は「分類を見送る」の明示的な棄権。不正値・低信頼・生成失敗も
    必ず soft_default。LLM 出力から workshop を
    終了したり lesson を解除したりすることは、この関数の契約上できない。
    """
    if not isinstance(payload, dict):
        return "soft_default"
    intent = str(payload.get("intent") or "").strip()
    if intent not in WORKSHOP_LLM_INTENTS:
        return "soft_default"
    confidence = payload.get("confidence")
    if isinstance(confidence, bool):
        return "soft_default"
    try:
        score = float(confidence)
    except (TypeError, ValueError):
        return "soft_default"
    if not 0.0 <= score <= 1.0 or score < min_confidence:
        return "soft_default"
    return intent


def should_handle_as_workshop(
    user_text: str | None,
    *,
    verse: str | None = None,
    player_input: Any | None = None,
) -> bool:
    """workshop open 中に service/SM が player_chat より先に扱うべきか。

    Stage2 soft 既定: マーカー外でも hard off-topic でなければ True。
    """
    if extract_conversational_revise(user_text):
        return True
    return workshop_open_intent(user_text, verse=verse, player_input=player_input) is not None


def extract_conversational_revise(raw_text: str | None) -> str | None:
    """自然文の直し句。formal の「直し:」に加え、workshop 中の言い回しを拾う。"""
    from dogido_server.player_input.guardrails import _parse_haiku_payload, extract_revised_haiku

    formal = extract_revised_haiku(raw_text)
    if formal:
        return formal
    text = (raw_text or "").strip()
    if not text:
        return None
    soft_prefixes = (
        "こう直して:",
        "こう直して：",
        "こう直して",
        "こう直す:",
        "こう直す：",
        "こう直す",
        "直して:",
        "直して：",
        "直しは:",
        "直しは：",
        "直しは",
        "この方がええ:",
        "この方がええ：",
        "このほうがいい:",
        "このほうがいい：",
        "こうしたら:",
        "こうしたら：",
    )
    for prefix in soft_prefixes:
        if text.startswith(prefix):
            payload = text[len(prefix) :].strip()
            # 「こう直してや」だけのときは None
            if len(payload) < 4:
                return None
            return _parse_haiku_payload(payload)
    # 「直し … 五/七/五」が文中にある場合
    for marker in ("直し:", "直し：", "直して "):
        if marker in text:
            idx = text.find(marker)
            payload = text[idx + len(marker) :].strip()
            if len(payload) >= 4:
                parsed = _parse_haiku_payload(payload)
                if parsed:
                    return parsed
    return None


def lessons_from_critique_kind(kind: str, *, player_text: str = "") -> list[dict[str, object]]:
    """critique 種別から薄い soft lesson を0〜1件生成。

    H5.1: 強制禁止ではなく「できれば意識」。praise / other は常駐 lesson を増やさない
    （praise は lesson を触らない。全軸緩めは明示「気にせんで」のみ）。
    """
    del player_text  # 将来の自然文抽出用。いまは種別のみ
    k = (kind or "other").strip()
    # 軸は lesson_type で1本。同種は list 時に新しい1件だけ効く
    if k in {"unreadable", "ask_meaning"}:
        return [
            {
                "lesson_type": "readability",
                "note": "読みやすさを少し意識する（かな連続・謎語は控えめに）",
                "prefer_materials": True,
                "polarity": "tighten",
                # strength は将来用。現状 list は polarity / type のみ参照
                "strength": 0.3,
            }
        ]
    if k == "forced_compress":
        return [
            {
                "lesson_type": "compress",
                "note": "要素を少し絞って余白を残すとよい",
                "prefer_materials": True,
                "polarity": "tighten",
                "strength": 0.3,
            }
        ]
    if k == "off_context":
        return [
            {
                "lesson_type": "scene",
                "note": "材料・場面から大きく外れない方がよい",
                "prefer_materials": True,
                "polarity": "tighten",
                "strength": 0.3,
            }
        ]
    # praise / other / 不明 → 新規 tighten は作らない
    return []


def loosen_all_lessons() -> dict[str, object]:
    """全軸の soft lesson を抑止する loosen 行（明示「気にせんで」用）。"""
    return {
        "lesson_type": "*",
        "note": "",
        "prefer_materials": False,
        "polarity": "loosen",
        "strength": 0.0,
    }


def render_workshop_reply(
    kind: str,
    workshop: RecentHaikuWorkshop,
    *,
    player_text: str = "",
) -> str:
    """ルールベースの短い返事（LLM なし）。断片質問は短く、講義しない。"""
    verse = workshop.editing_line() or "（句なし）"
    verse_one_line = " ".join(verse.replace("\n", " ").split())
    materials = materials_speech_line(workshop)
    said = player_text or ""

    if kind == "close":
        return "おけ、この句の話はここまでや。"
    if kind == "clear_lessons":
        return "おけ、前の注意は気にせんでええわ。"
    if kind == "praise":
        return "ありがとうや。その句、残しとくで。"
    if kind == "ack":
        return "うん、そんな感じや。"
    if kind == "ask_meaning":
        # LLM なし経路: 部分一致テンプレ / soft_fail（本番は service が LLM 経由）
        reply, _path = finalize_ask_meaning_reply(workshop, said, None)
        return reply
    if kind == "critique_forced":
        return "せやな、詰め込みすぎた。余白を残すよう直した方がええな。"
    if kind == "critique_gibberish":
        return f"うん、「{verse_one_line}」は読みにくい。そこは直した方がええな。"
    if kind == "critique_offscene":
        return "せやな、場とずれとる。そこは直した方がええな。"
    if kind == "request_repair":
        return "うん、どの行を直すか確かめてみるわ。"
    if kind == "soft_default":
        return "句の話、まだ聞いてるで。気になるところある？"
    # other_haiku（読み・好み・句への言及など）— 短く
    if any(m in said for m in _REMEMBER_MARKERS):
        return "おけ、覚えとくわ。次に活かすで。"
    if any(m in said for m in _READING_META_MARKERS):
        return "せやな、読みの話やな。次は読みやすさ、ちょっと意識するわ。"
    if any(m in said for m in _PREFERENCE_MARKERS) or any(m in said for m in _SOFT_SUGGEST_MARKERS):
        return "なるほど、その言い方の方が自然やな。"
    if any(m in said for m in _LENGTH_MARKERS):
        return "せやな、ちょっと長かったかもな。次は短め、意識するわ。"
    return "気になるところあったら、その言葉だけ言ってな。"


def _kana_fold(text: str) -> str:
    """カタカナ→ひらがな。句断片マッチ用。"""
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
    for ch in ("\n", " ", "　", "、", "。", "？", "?", "！", "!", "「", "」", "『", "』"):
        t = t.replace(ch, "")
    return t


def _looks_like_verse_fragment_question(player_text: str, verse: str) -> bool:
    """句の断片を指した疑問か（「晴れのバラ?」等）。"""
    text = (player_text or "").strip()
    if not text or not verse:
        return False
    questionish = any(c in text for c in ("?", "？", "何", "なに", "って", "とは"))
    if not questionish and len(_compact_kana(text)) > 10:
        return False
    # 短い疑問・「〜?」は questionish とみなす
    if not questionish and ("?" in text or "？" in text or len(text) <= 12):
        questionish = "?" in text or "？" in text
    if not questionish:
        return False
    return _quoted_or_fragment_about_verse(text, verse) is not None


def _quoted_or_fragment_about_verse(player_text: str, verse: str) -> str | None:
    """プレイヤー文から句に関係しそうな断片を拾う（かな照合）。

    返すのはできるだけ **句側のフレーズ**（「はれのばら」）で、中途半端な
    「れのばら」より句の一行を優先する。
    """
    text = (player_text or "").strip()
    if not text or not verse:
        return None
    probe = _compact_kana(text)
    if not probe:
        return None
    # 句を行・空白で割ったフレーズごとに重なりを見る
    phrases = [
        p.strip()
        for p in verse.replace("　", " ").replace("\n", " ").split()
        if p.strip()
    ]
    best_phrase: str | None = None
    best_score = 0
    for part in phrases:
        ph = _compact_kana(part)
        if len(ph) < 2:
            continue
        score = 0
        if ph in probe or probe in ph:
            score = len(ph)
        else:
            for n in range(min(len(ph), len(probe)), 1, -1):
                hit = False
                for i in range(len(ph) - n + 1):
                    if ph[i : i + n] in probe:
                        score = n
                        hit = True
                        break
                if hit:
                    break
        if score >= 2 and score > best_score:
            best_score = score
            best_phrase = part
    if best_phrase is not None:
        return best_phrase
    # フレーズ単位で取れなければ句全体の部分一致
    verse_h = _compact_kana(verse)
    for n in range(min(6, len(verse_h)), 1, -1):
        for i in range(len(verse_h) - n + 1):
            sub = verse_h[i : i + n]
            if n >= 2 and sub in probe:
                return sub
    return None
