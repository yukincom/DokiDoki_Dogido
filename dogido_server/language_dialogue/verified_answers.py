"""限定した計算・表照会の答え。新しい検索器や音数計算器は持たない。"""

import hashlib
import re
import unicodedata

from dogido_server.llm.haiku import count_japanese_sounds
from dogido_server.tts_reading import katakana_to_hiragana
from .contracts import GroundedReply
from .retrieval import grade_character


def count_explicit_kana(target, evidence_texts):
    """発話内にある確定かなだけ。辞書読み・STTの音近傍補正は呼ばない。"""
    surface = unicodedata.normalize("NFKC", target).strip()
    if not surface or not any(surface in unicodedata.normalize("NFKC", text) for text in evidence_texts):
        return None
    reading = katakana_to_hiragana(surface)
    # 計数器は句読点・漢字も数えるため、ここではかな一続きに限定する。
    # 歴史的仮名・踊り字・特殊な発音も確定読みの確認へ返す。
    if re.fullmatch(r"[ぁ-ゔー]+", reading) is None or any(ch in reading for ch in "ゐゑ"):
        return None
    if reading[0] in "ぁぃぅぇぉゃゅょゎー" or "ゎ" in reading:
        return None
    value = count_japanese_sounds(reading)
    return {
        "id": "calculation:mora:" + hashlib.sha256(reading.encode()).hexdigest()[:16],
        "title_ja": "確定かなの音数", "text_ja": f"『{surface}』は{value}音。確定読み『{reading}』を既存の拍計数で計算。",
        "claim_status": "verified_calculation", "sources": [],
        "calculation": {"operation": "mora_count", "surface": surface, "reading": reading, "value": value},
    }


def verified_reply(interpretation, facts):
    """同じ検索・計算結果だけから返す。意味説明などへ対象を広げない。"""
    if interpretation.facet == "mora_count":
        for fact in facts:
            calculation = fact.get("calculation", {})
            if calculation.get("operation") == "mora_count" and calculation.get("surface") == unicodedata.normalize("NFKC", interpretation.target).strip():
                return GroundedReply(status="answer", text=f"「{calculation['surface']}」は{calculation['value']}音やで。",
                    fact_ids=[fact["id"]], application="発話にある確定かなを既存の音数計算で数えた。", missing="")
    if interpretation.facet == "grade":
        character = grade_character(interpretation.target)
        if len(character) != 1:
            return None  # 熟語全体の学年と、一字の配当を同一視しない。
        for fact in facts:
            allocation = fact.get("allocation", {})
            grade = allocation.get("school_grade")
            if allocation.get("character") == character and allocation.get("scope") == "character_only" and type(grade) is int and 1 <= grade <= 6:
                return GroundedReply(status="answer", text=f"「{character}」は小学{grade}年生で習う漢字やで。",
                    fact_ids=[fact["id"]], application="既に検索した学年別漢字配当表の字種の配当。", missing="")
    return None
