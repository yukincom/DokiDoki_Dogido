"""川柳ワークショップ: 発句の pin（付箋）と open/close。

会話履歴（5往復）とは別に、セッション上に「いまの句」を保持する。
lifecycle だけをここに置き、発句本体（mixins/haiku.py）には混ぜない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from dogido_server.memory_types import HaikuEmission

# 発句からの最大 open 時間
DEFAULT_T_OPEN = timedelta(seconds=240)
# 句関連の最後のやり取りからの無活動
DEFAULT_T_IDLE = timedelta(seconds=120)
# 句と無関係な入力が連続したら close
DEFAULT_N_DRIFT = 2


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

    def display_line(self) -> str:
        return (self.surface_text or "").strip()


def open_from_emission(
    emission: HaikuEmission,
    *,
    materials: dict[str, Any] | None = None,
    entry_id: str | None = None,
    now: datetime | None = None,
) -> RecentHaikuWorkshop:
    """発句成功時に pin を立てる。"""
    at = now or emission.created_at
    mats = dict(materials or {})
    if emission.interpretation and "interpretation" not in mats:
        mats["interpretation"] = emission.interpretation
    if emission.biome and "biome" not in mats:
        mats["biome"] = emission.biome
    if emission.structure and "structure" not in mats:
        mats["structure"] = emission.structure
    return RecentHaikuWorkshop(
        surface_text=(emission.text or "").strip(),
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
        "haiku_workshop_text": workshop.display_line(),
        "haiku_workshop_materials": materials_speech_line(workshop),
    }


def materials_speech_line(workshop: RecentHaikuWorkshop) -> str:
    materials = workshop.materials or {}
    parts: list[str] = []
    interpretation = str(
        materials.get("interpretation") or workshop.interpretation or ""
    ).strip()
    if interpretation:
        parts.append(interpretation)
    motifs = materials.get("motifs") or materials.get("scene_motifs")
    if isinstance(motifs, (list, tuple)) and motifs:
        parts.append("モチーフ: " + "、".join(str(m) for m in motifs[:8] if m))
    for key in ("biome", "structure", "place"):
        val = materials.get(key) or getattr(workshop, key, None)
        if val:
            parts.append(f"{key}: {val}")
    return " / ".join(parts) if parts else ""


# --- 意図判定（ルール・初版） ---

_CLOSE_MARKERS = (
    "もうええ",
    "もういい",
    "次いこ",
    "つぎいこ",
    "わかった",
    "おk",
    "おけ",
    "ok",
    "OK",
    "よし",
    "了解",
)
_PRAISE_MARKERS = (
    "いい句",
    "ええ句",
    "うまい",
    "上手",
    "好き",
    "気に入った",
    "そのままでいい",
    "そのままでええ",
    "良い句",
)
_FORCED_MARKERS = ("無理やり", "詰め込み", "つめこみ", "圧縮", "息苦", "ごちゃごちゃ")
_GIBBERISH_MARKERS = ("読めん", "読めない", "わからん", "意味わから", "日本語", "何言", "なにい", "ぐう", "グー")
_OFFSCENE_MARKERS = ("海ちゃう", "海じゃない", "ここ海", "村なのに", "関係ない", "場違い")
_MEANING_MARKERS = ("って何", "ってなに", "とは何", "とはなに", "何それ", "なにそれ", "意味")


def classify_workshop_intent(user_text: str) -> str | None:
    """句関連なら kind、無関係なら None。

    kinds: close | praise | critique_forced | critique_gibberish |
           critique_offscene | ask_meaning | other_haiku
    """
    text = (user_text or "").strip()
    if not text:
        return None
    folded = text.lower()
    if any(m in text or m in folded for m in _CLOSE_MARKERS):
        return "close"
    if any(m in text for m in _PRAISE_MARKERS):
        return "praise"
    if any(m in text for m in _FORCED_MARKERS):
        return "critique_forced"
    if any(m in text for m in _OFFSCENE_MARKERS):
        return "critique_offscene"
    # 「〜って何」を先に（「グー」単独より意味質問を優先）
    if any(m in text for m in _MEANING_MARKERS):
        return "ask_meaning"
    if any(m in text for m in _GIBBERISH_MARKERS):
        return "critique_gibberish"
    # 句・川柳・俳句への明示参照
    if any(m in text for m in ("句", "川柳", "俳句", "せんりゅう", "詠ん", "よんだ")):
        return "other_haiku"
    return None


def render_workshop_reply(
    kind: str,
    workshop: RecentHaikuWorkshop,
    *,
    player_text: str = "",
) -> str:
    """ルールベースの短い返事（LLM なし初版）。"""
    verse = workshop.display_line() or "（句なし）"
    materials = materials_speech_line(workshop)
    materials_bit = f"狙いは「{materials}」やったんやけどな。" if materials else ""

    if kind == "close":
        return "おけ、この句の話はここまでや。"
    if kind == "praise":
        return "ありがとうや。その句、残しとくで。"
    if kind == "ask_meaning":
        return (
            f"正直「{verse}」は読みにくいかもな。"
            f"{materials_bit}"
            "直すなら言ってな。"
        )
    if kind == "critique_forced":
        return (
            "せやな、詰め込みすぎたかもな。"
            f"{materials_bit}"
            "次は余白残すようにするわ。"
        )
    if kind == "critique_gibberish":
        return (
            f"うん、あれは読みにくいわ。「{verse}」やった。"
            f"{materials_bit}"
            "直しか、次の注意、どっちでもええで。"
        )
    if kind == "critique_offscene":
        return (
            "場とずれたな、悪かった。"
            f"{materials_bit}"
            "次は材料から外れんようにするわ。"
        )
    # other_haiku
    return (
        f"いまの句は「{verse}」や。"
        f"{materials_bit}"
        "気になるところあったら言ってな。"
    )
