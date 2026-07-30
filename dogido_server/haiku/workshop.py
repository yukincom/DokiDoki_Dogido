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
    """プレイヤー向けの短い狙い文。内部キー名（biome: 等）は絶対に出さない。"""
    cands = material_candidates_for_speech(workshop)
    if not cands:
        return ""
    # いちばん短い具体候補（長い対比文より「錆びた銅のランタン」等を優先）
    return min(cands, key=lambda s: (len(s) > 24, len(s)))


def materials_debug_line(workshop: RecentHaikuWorkshop) -> str:
    """ログ用。生 materials（メタキー込み）を短く。"""
    materials = workshop.materials or {}
    parts: list[str] = []
    interpretation = str(
        materials.get("interpretation") or workshop.interpretation or ""
    ).strip()
    if interpretation:
        parts.append(interpretation[:80])
    for key in ("biome", "structure", "time_phase", "place"):
        val = materials.get(key) or getattr(workshop, key, None)
        if val:
            parts.append(f"{key}={val}")
    return " / ".join(parts) if parts else ""


def material_candidates_for_speech(workshop: RecentHaikuWorkshop) -> list[str]:
    """「それは〇〇やで」用の候補。日本語の中身だけ（キー名なし）。

    短い具体物（モチーフ・biome_ja）を先に、長い解釈文は後。
    """
    materials = workshop.materials or {}
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str | None, *, max_len: int = 28) -> None:
        t = (s or "").strip().rstrip("。．.")
        t = t.replace("プレイヤー", "あんた")
        if not t or len(t) < 2:
            return
        if len(t) > max_len:
            t = _shorten_for_speech(t, max_chars=max_len)
        if not t or t in seen:
            return
        seen.add(t)
        out.append(t)

    # 1) 短い具体物を先に（発話の本命）
    motifs = materials.get("motifs") or materials.get("scene_motifs")
    if isinstance(motifs, (list, tuple)):
        for m in motifs:
            add(str(m) if m else None)

    for key in ("biome_ja", "structure_ja", "place_ja"):
        add(str(materials[key]) if materials.get(key) else None)

    phase = materials.get("time_phase") or workshop.time_phase
    phase_ja = {
        "morning": "朝",
        "day": "昼",
        "evening": "夕方",
        "night": "夜",
    }.get(str(phase or ""), None)
    add(phase_ja)

    # biome / structure は日本語ラベルへ
    try:
        from dogido_server.entry_catalog import biome_labels, structure_labels

        biome_id = materials.get("biome") or workshop.biome
        if biome_id:
            labels = biome_labels()
            add(labels.get(str(biome_id)) or labels.get(str(biome_id).removeprefix("minecraft:")))
        struct_id = materials.get("structure") or workshop.structure
        if struct_id:
            sl = structure_labels()
            sid = str(struct_id).removeprefix("minecraft:")
            add(sl.get(sid) or sl.get(str(struct_id)))
    except Exception:  # noqa: BLE001
        pass

    # 2) 解釈文は条片のみ（全文の長い対比は候補にしない＝「それは…やで」が講義になる）
    interpretation = str(
        materials.get("interpretation") or workshop.interpretation or ""
    ).strip()
    if interpretation:
        for chunk in _split_material_chunks(interpretation):
            add(chunk, max_len=22)

    return out


def pick_material_for_fragment(
    fragment: str | None,
    workshop: RecentHaikuWorkshop,
) -> str | None:
    """LLM 失敗時の超単純フォールバック。部分一致のみ（訓読みテーブルは使わない）。"""
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
    verse = workshop.display_line() or ""
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
    verse = workshop.display_line() or "（句なし）"
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

    # LLM なし / 失敗時: 部分一致だけ試す
    simple = pick_material_for_fragment(fragment, workshop)
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
# プレイヤー明示で lesson を緩める（workshop open 外でも可）
_CLEAR_LESSON_MARKERS = (
    "気にせんで",
    "気にしなくて",
    "気にしんで",
    "気にしなくていい",
    "気にしなくてええ",
    "もう気にせん",
    "注意いらない",
    "注意はいらない",
    "注意いらん",
    "注意はいらん",
    "縛らんで",
    "ゆるめて",
    "緩めて",
    "前の注意やめて",
    "前の注意いらない",
    "前の注意はいらん",
)


def wants_clear_haiku_lessons(user_text: str | None) -> bool:
    """「もう気にせんで」系。close（もうええ）とは別。"""
    text = (user_text or "").strip()
    if not text:
        return False
    return any(m in text for m in _CLEAR_LESSON_MARKERS)


def classify_workshop_intent(
    user_text: str,
    *,
    verse: str | None = None,
) -> str | None:
    """句関連なら kind、無関係なら None。

    kinds: close | praise | clear_lessons | critique_forced | critique_gibberish |
           critique_offscene | ask_meaning | ack | other_haiku

    verse を渡すと「晴れのバラ?」のように句断片＋疑問を ask_meaning にできる。
    """
    text = (user_text or "").strip()
    if not text:
        return None
    folded = text.lower()
    # 明示緩めを close より先に（「もう気にせんで」に「もう」が含まれるため）
    if wants_clear_haiku_lessons(text):
        return "clear_lessons"
    if any(m in text or m in folded for m in _CLOSE_MARKERS):
        return "close"
    if any(m in text for m in _PRAISE_MARKERS):
        return "praise"
    # 納得相槌は「意味」より先（「そういう意味か」誤爆防止）
    if any(m in text for m in _ACK_MARKERS):
        return "ack"
    if any(m in text for m in _FORCED_MARKERS):
        return "critique_forced"
    if any(m in text for m in _OFFSCENE_MARKERS):
        return "critique_offscene"
    # 「〜って何／とは何／何でしょう」
    if any(m in text for m in _MEANING_MARKERS):
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
    # 「AじゃなくB」「〜の方が〜」など好み・訂正（workshop 中のみ呼ばれる想定）
    if any(m in text for m in _PREFERENCE_MARKERS):
        return "other_haiku"
    # 句・川柳・俳句への明示参照（ジャンル語のみ）
    if any(m in text for m in ("句", "川柳", "俳句", "せんりゅう", "詠ん", "よんだ")):
        return "other_haiku"
    return None


def should_handle_as_workshop(
    user_text: str | None,
    *,
    verse: str | None = None,
) -> bool:
    """workshop open 中に service/SM が player_chat より先に扱うべきか。"""
    if extract_conversational_revise(user_text):
        return True
    return classify_workshop_intent(user_text or "", verse=verse) is not None


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
    （praise の可逆は memory 側の loosen 行で扱う）。
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


def loosen_lesson_for_praise() -> dict[str, object]:
    """ほめられたとき、既存 tighten を弱める（append-only の loosen 行）。"""
    return loosen_all_lessons()


def loosen_all_lessons() -> dict[str, object]:
    """全軸の soft lesson を抑止する loosen 行（praise / 明示「気にせんで」共用）。"""
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
    verse = workshop.display_line() or "（句なし）"
    verse_one_line = " ".join(verse.replace("\n", " ").split())
    materials = materials_speech_line(workshop)
    said = player_text or ""

    if kind == "close":
        return "おけ、この句の話はここまでや。"
    if kind == "clear_lessons":
        return "おけ、前の注意は気にせんでええわ。"
    if kind == "praise":
        return "ありがとうや。その句、残しとくで。前の注意は少し緩めるわ。"
    if kind == "ack":
        return "うん、そんな感じや。"
    if kind == "ask_meaning":
        # LLM なし経路: 部分一致テンプレ / soft_fail（本番は service が LLM 経由）
        reply, _path = finalize_ask_meaning_reply(workshop, said, None)
        return reply
    if kind == "critique_forced":
        return "せやな、詰め込みすぎたかもな。次は余白、ちょっと意識するわ。"
    if kind == "critique_gibberish":
        return f"うん、読みにくいわ。「{verse_one_line}」。直すでも次で気をつけるでもええで。"
    if kind == "critique_offscene":
        aim = f"狙いは{materials}寄りやったんやけどな。" if materials else ""
        return f"場とずれたな、悪かった。{aim}次は外れすぎんようにするわ。"
    # other_haiku（読み・好み・句への言及など）— 短く
    if any(m in said for m in _READING_META_MARKERS):
        return "せやな、読みの話やな。次は読みやすさ、ちょっと意識するわ。"
    if any(m in said for m in _PREFERENCE_MARKERS):
        return "なるほど、そっちの方がしっくりくるかもな。次に活かすわ。"
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
