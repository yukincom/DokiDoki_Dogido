"""現在の会話語彙だけを使う、音声認識の文脈補正。

誤変換の固定表とは別に、呼び出し元が渡した候補へだけ音の近いかな断片を
寄せる。全カタログ検索や一般文の校正はしないため、ゲーム状態・保存操作に
未知の語を作らない。候補の組み立てと音近傍エンジンを分け、workshop 以外の
会話にも再利用できる形にしている。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable


_SCRIPT_RUN_RE = re.compile(r"[ァ-ヺー]{3,}|[ぁ-ゖー]{4,}")
_KANA_RUN_RE = re.compile(r"[ぁ-ゖー]+|[ァ-ヺー]+")
# 語そのものと、短い助詞・詠嘆までを一つの STT token にされる場合だけ扱う。
_ALLOWED_SUFFIXES = ("", "や", "かな", "の", "が", "を", "に", "は", "も", "で", "と")


@dataclass(frozen=True, slots=True)
class ContextualASRCandidate:
    """その時点でプレイヤーが言いそうな、出典つきの正表記。"""

    surface: str
    readings: tuple[str, ...]
    source: str


@dataclass(frozen=True, slots=True)
class ContextualASRCorrection:
    original: str
    replacement: str
    candidate_surface: str
    candidate_source: str
    distance: int


def apply_candidate_asr_fixes(
    text: str,
    candidates: Iterable[ContextualASRCandidate],
    *,
    max_corrections: int = 2,
) -> tuple[str, tuple[ContextualASRCorrection, ...]]:
    """一意に最も近いかな断片だけを、現在候補の正表記へ直す。

    漢字の読みをここで推測しない。候補側に明示 reading があるか、surface に
    かな語が含まれる場合だけ比較する。距離が同じ別候補があれば fail-closed。
    """

    source_text = str(text or "")
    if not source_text:
        return source_text, ()
    prepared = _prepared_candidates(candidates)
    if not prepared:
        return source_text, ()

    proposals: list[tuple[int, int, ContextualASRCorrection]] = []
    for match in _SCRIPT_RUN_RE.finditer(source_text):
        heard = match.group(0)
        heard_kana = normalize_spoken_kana(heard)
        if not heard_kana:
            continue
        ranked: list[tuple[int, str, str, str]] = []
        for candidate, reading in prepared:
            for suffix in _ALLOWED_SUFFIXES:
                target = normalize_spoken_kana(reading + suffix)
                if not target or abs(len(heard_kana) - len(target)) > 2:
                    continue
                distance = _edit_distance(heard_kana, target)
                if distance > _maximum_distance(target):
                    continue
                replacement = candidate.surface + suffix
                # 表記も同じなら補正する必要はない。
                if heard == replacement:
                    continue
                ranked.append((distance, replacement, candidate.surface, candidate.source))
        if not ranked:
            continue
        best_distance = min(row[0] for row in ranked)
        best = {
            (replacement, surface, candidate_source)
            for distance, replacement, surface, candidate_source in ranked
            if distance == best_distance
        }
        # 同じ音から複数の正表記へ寄せられるときは選ばない。
        if len(best) != 1:
            continue
        replacement, surface, candidate_source = best.pop()
        proposals.append(
            (
                match.start(),
                match.end(),
                ContextualASRCorrection(
                    original=heard,
                    replacement=replacement,
                    candidate_surface=surface,
                    candidate_source=candidate_source,
                    distance=best_distance,
                ),
            )
        )

    if not proposals:
        return source_text, ()
    # span は正規表現上重ならない。後ろから置換して元の index を保つ。
    selected = proposals[: max(0, int(max_corrections))]
    result = source_text
    for start, end, correction in reversed(selected):
        result = result[:start] + correction.replacement + result[end:]
    return result, tuple(correction for _start, _end, correction in selected)


def workshop_asr_candidates(
    *,
    verse: str,
    materials: dict[str, object] | None,
) -> tuple[ContextualASRCandidate, ...]:
    """保存済み workshop 材料から、現在だけ有効な語彙候補を作る。"""

    payload = materials if isinstance(materials, dict) else {}
    rows: list[ContextualASRCandidate] = []

    def add(surface: object, readings: Iterable[object], source: str) -> None:
        label = str(surface or "").strip()
        normalized_readings = tuple(
            dict.fromkeys(
                str(reading or "").strip()
                for reading in readings
                if str(reading or "").strip()
            )
        )
        if not label or not normalized_readings:
            return
        rows.append(ContextualASRCandidate(label, normalized_readings, source))

    for index, line in enumerate(str(verse or "").splitlines()):
        cleaned = line.strip()
        if cleaned and _is_kana_text(cleaned):
            add(cleaned, (cleaned,), f"verse:{index}")

    catalog_readings: dict[str, str] = {}
    catalog_sources = payload.get("catalog_sources")
    if isinstance(catalog_sources, list):
        for index, raw in enumerate(catalog_sources):
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("label") or "").strip()
            reading = str(raw.get("reading") or "").strip()
            source_ref = str(raw.get("source_ref") or f"catalog:{index}")
            if label and reading:
                catalog_readings[source_ref] = reading
                add(label, (reading,), source_ref)
            # カタカナ名など、表記から機械的に読める部分は独立候補にする。
            for kana_term in _surface_kana_terms(label):
                add(kana_term, (kana_term,), source_ref)

    atoms = payload.get("source_atoms")
    if isinstance(atoms, list):
        for index, raw in enumerate(atoms):
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            source_ref = str(raw.get("source_ref") or f"atom:{index}")
            reading = catalog_readings.get(source_ref)
            if reading and str(raw.get("kind") or "") == "catalog_label":
                add(text, (reading,), source_ref)
            for kana_term in _surface_kana_terms(text):
                add(kana_term, (kana_term,), source_ref)

    # enum → 表示語・読みは観測値の投影であり、誤変換の固定表ではない。
    phase = str(payload.get("time_phase") or "").strip().lower()
    phase_terms = {
        "morning": (("朝", "あさ"),),
        "day": (("昼", "ひる"),),
        "evening": (("夕方", "ゆうがた"), ("夕暮れ", "ゆうぐれ")),
        "night": (("夜", "よる"),),
    }.get(phase, ())
    for surface, reading in phase_terms:
        add(surface, (reading,), f"time_phase:{phase}")

    # 将来、音源名・look target 等から同じ契約で候補を追加できる公開拡張点。
    explicit = payload.get("asr_context_terms")
    if isinstance(explicit, list):
        for index, raw in enumerate(explicit):
            if not isinstance(raw, dict):
                continue
            readings = raw.get("readings")
            if not isinstance(readings, list):
                readings = [raw.get("reading")]
            add(
                raw.get("surface"),
                readings,
                str(raw.get("source") or f"explicit:{index}"),
            )

    # 同じ正表記・読みを複数経路で拾っても、曖昧候補には数えない。
    unique: dict[tuple[str, tuple[str, ...]], ContextualASRCandidate] = {}
    for candidate in rows:
        unique.setdefault((candidate.surface, candidate.readings), candidate)
    return tuple(unique.values())


def normalize_spoken_kana(text: str) -> str:
    """比較用。半角・カタカナ・長音・小書きを読みの近いひらがなへ畳む。"""

    folded = unicodedata.normalize("NFKC", str(text or ""))
    chars: list[str] = []
    for char in folded:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            char = chr(code - 0x60)
        char = {"ぁ": "あ", "ぃ": "い", "ぅ": "う", "ぇ": "え", "ぉ": "お", "ゎ": "わ"}.get(char, char)
        if char == "ー":
            vowel = _last_vowel(chars[-1] if chars else "")
            if vowel:
                chars.append(vowel)
            continue
        if "ぁ" <= char <= "ゖ":
            chars.append(char)
    return "".join(chars)


def _prepared_candidates(
    candidates: Iterable[ContextualASRCandidate],
) -> tuple[tuple[ContextualASRCandidate, str], ...]:
    prepared: list[tuple[ContextualASRCandidate, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        surface = candidate.surface.strip()
        if not surface:
            continue
        for raw_reading in candidate.readings:
            reading = normalize_spoken_kana(raw_reading)
            if len(reading) < 3 or (surface, reading) in seen:
                continue
            seen.add((surface, reading))
            prepared.append((candidate, reading))
    return tuple(prepared)


def _surface_kana_terms(surface: str) -> tuple[str, ...]:
    return tuple(
        term
        for term in _KANA_RUN_RE.findall(str(surface or ""))
        if len(normalize_spoken_kana(term)) >= 3
    )


def _is_kana_text(text: str) -> bool:
    compact = re.sub(r"[\s／/|]+", "", str(text or ""))
    return bool(compact) and all("ぁ" <= char <= "ヿ" or char == "ー" for char in compact)


def _maximum_distance(target: str) -> int:
    if len(target) < 4:
        return 0
    return 2 if len(target) >= 8 else 1


def _edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for row_index, left_char in enumerate(left, start=1):
        current = [row_index]
        for column_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column_index] + 1,
                    previous[column_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _last_vowel(char: str) -> str:
    for vowel, group in (
        ("あ", "あかがさざただなはばぱまゃやらゎわ"),
        ("い", "いきぎしじちぢにひびぴみりゐ"),
        ("う", "うくぐすずつづぬふぶぷむゅゆるゔ"),
        ("え", "えけげせぜてでねへべぺめれゑ"),
        ("お", "おこごそぞとどのほぼぽもょよろを"),
    ):
        if char in group:
            return vowel
    return ""
