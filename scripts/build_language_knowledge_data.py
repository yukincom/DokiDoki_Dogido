"""公式配布データをドギド用の検索可能な JSON Lines へ正規化する。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "reference" / "language_education_and_poetry"
RAW_DIR = REFERENCE_DIR / "data" / "raw"
NORMALIZED_DIR = REFERENCE_DIR / "data" / "normalized"
INDEX_PATH = NORMALIZED_DIR / "index.json"
MANIFEST_PATH = REFERENCE_DIR / "data" / "manifest.json"

RUBY_RE = re.compile(r"〓([^〓〔〕]+)〔([^〔〕]+)〕")
STRIKETHROUGH_RE = re.compile(r"<s>(.*?)</s/?>")
CONTROL_Z = "\x1a"

RAW_DATASETS = (
    {
        "id": "ninjal_sentence_patterns_2026_01",
        "path": "ninjal/nihongo_bunkei_database20260126.zip",
        "source_id": "src.ninjal.sentence-patterns",
        "canonical_url": "https://repository.ninjal.ac.jp/records/2000610",
        "license": "CC BY 4.0",
        "version": "2026.01",
    },
    {
        "id": "ninjal_education_vocabulary_2009_a",
        "path": "ninjal/kyoikukihongoi_2009A.csv",
        "source_id": "src.ninjal.education-vocabulary",
        "canonical_url": "https://mmsrv.ninjal.ac.jp/brfvep/",
        "license": "CC BY 4.0",
        "version": "2009A",
    },
    {
        "id": "ninjal_education_vocabulary_2009_b",
        "path": "ninjal/kyoikukihongoi_2009B.csv",
        "source_id": "src.ninjal.education-vocabulary",
        "canonical_url": "https://mmsrv.ninjal.ac.jp/brfvep/",
        "license": "CC BY 4.0",
        "version": "2009B",
    },
    {
        "id": "ninjal_japanese_education_vocabulary_2009",
        "path": "ninjal/rokushutaisho.csv",
        "source_id": "src.ninjal.education-vocabulary",
        "canonical_url": "https://mmsrv.ninjal.ac.jp/brfvep/",
        "license": "CC BY 4.0",
        "version": "2009",
    },
    {
        "id": "unicode_character_database_17_0_0",
        "path": "unicode/UnicodeData-17.0.0.txt",
        "license_path": "unicode/LICENSE.txt",
        "source_id": "src.unicode.ucd",
        "canonical_url": "https://www.unicode.org/Public/17.0.0/ucd/UnicodeData.txt",
        "license": "Unicode License v3",
        "version": "17.0.0",
    },
    {
        "id": "mext_curriculum_codes_elementary_82v12",
        "path": "mext/curriculum_codes_elementary_82V12.csv",
        "source_id": "src.mext.curriculum-codes",
        "canonical_url": "https://www.mext.go.jp/a_menu/other/data_00002.htm",
        "license": "公共データ利用規約（第1.0版）準拠（文部科学省ウェブサイト利用規約）",
        "version": "82V12",
    },
    {
        "id": "mext_curriculum_codes_junior_high_83v11",
        "path": "mext/curriculum_codes_junior_high_83V11.csv",
        "source_id": "src.mext.curriculum-codes",
        "canonical_url": "https://www.mext.go.jp/a_menu/other/data_00002.htm",
        "license": "公共データ利用規約（第1.0版）準拠（文部科学省ウェブサイト利用規約）",
        "version": "83V11",
    },
    {
        "id": "mext_curriculum_codes_senior_high_84v10",
        "path": "mext/curriculum_codes_senior_high_84V10.csv",
        "source_id": "src.mext.curriculum-codes",
        "canonical_url": "https://www.mext.go.jp/a_menu/other/data_00002.htm",
        "license": "公共データ利用規約（第1.0版）準拠（文部科学省ウェブサイト利用規約）",
        "version": "84V10",
    },
    {
        "id": "bunka_joyo_kanji_index_2010",
        "path": "bunka/joyokanjisakuin.html",
        "source_id": "src.bunka.joyo-kanji-table",
        "canonical_url": "https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/kijun/naikaku/kanji/joyokanjisakuin/index.html",
        "license": "公共データ利用規約（第1.0版）準拠（文部科学省ウェブサイト利用規約）",
        "version": "平成22年内閣告示第2号",
    },
    {
        "id": "bunka_joyo_kanji_table_2010",
        "path": "bunka/joyokanjihyo_20101130.pdf",
        "source_id": "src.bunka.joyo-kanji-table",
        "canonical_url": "https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/kijun/naikaku/pdf/joyokanjihyo_20101130.pdf",
        "license": "公共データ利用規約（第1.0版）準拠（文部科学省ウェブサイト利用規約）",
        "version": "平成22年内閣告示第2号",
    },
    {
        "id": "mext_elementary_curriculum_2017",
        "path": "mext/elementary_curriculum_2017.pdf",
        "source_id": "src.mext.grade-level-kanji-table",
        "canonical_url": "https://www.mext.go.jp/content/20230120-mxt_kyoiku02-100002604_01.pdf",
        "license": "公共データ利用規約（第1.0版）準拠（文部科学省ウェブサイト利用規約）",
        "version": "平成29年文部科学省告示第63号",
    },
)

EDITORIAL_INPUTS = (
    {
        "id": "grade_level_kanji_allocation_transcription",
        "path": "grade_level_kanji_allocation_transcription.json",
        "source_id": "src.mext.grade-level-kanji-table",
        "role": "official_pdf_visual_transcription",
    },
)

ROMANIZED_KANA = {
    "A": "あ",
    "I": "い",
    "U": "う",
    "E": "え",
    "O": "お",
    "KA": "か",
    "KI": "き",
    "KU": "く",
    "KE": "け",
    "KO": "こ",
    "SA": "さ",
    "SI": "し",
    "SU": "す",
    "SE": "せ",
    "SO": "そ",
    "TA": "た",
    "TI": "ち",
    "TU": "つ",
    "TE": "て",
    "TO": "と",
    "NA": "な",
    "NI": "に",
    "NU": "ぬ",
    "NE": "ね",
    "NO": "の",
    "HA": "は",
    "HI": "ひ",
    "HU": "ふ",
    "HE": "へ",
    "HO": "ほ",
    "MA": "ま",
    "MI": "み",
    "MU": "む",
    "ME": "め",
    "MO": "も",
    "YA": "や",
    "YU": "ゆ",
    "YO": "よ",
    "RA": "ら",
    "RI": "り",
    "RU": "る",
    "RE": "れ",
    "RO": "ろ",
    "WA": "わ",
    "WI": "ゐ",
    "WE": "ゑ",
    "WO": "を",
    "N": "ん",
    "YE": "え",
    "WU": "う",
}


def normalize(value: object) -> str:
    return (
        unicodedata.normalize("NFKC", str(value))
        .replace("～", "~")
        .replace("〜", "~")
        .strip()
        .casefold()
    )


def compact(value: object) -> str:
    return "".join(normalize(value).split())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_output_path(path: Path) -> Path:
    """生成先にsymlinkを含めず、リポジトリ外への書込みを拒否する。"""

    lexical_root = ROOT.absolute()
    lexical_path = path.absolute()
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError as error:
        raise ValueError(f"output path is outside repository: {path}") from error
    cursor = lexical_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"output path contains symlink: {path}")
    resolved_root = ROOT.resolve()
    resolved_path = lexical_path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"output path escapes repository: {path}")
    return lexical_path


def _clean(value: object) -> str:
    return str(value or "").replace(CONTROL_Z, "").strip()


def _nonempty(values: Iterable[object]) -> list[str]:
    return [text for value in values if (text := _clean(value))]


def _split(value: object, separator: str = "・") -> list[str]:
    text = _clean(value)
    return [part.strip() for part in text.split(separator) if part.strip()]


def _annotated_text(value: object) -> dict[str, Any]:
    source = _clean(value)
    deleted_segments = [
        RUBY_RE.sub(lambda match: match.group(1), match.group(1)).replace("〓", "")
        for match in STRIKETHROUGH_RE.finditer(source)
    ]
    source = STRIKETHROUGH_RE.sub(
        lambda match: (
            "〔"
            + RUBY_RE.sub(lambda ruby: ruby.group(1), match.group(1)).replace("〓", "")
            + "を除く〕"
        ),
        source,
    )
    readings = [
        {"surface": match.group(1), "reading": match.group(2)}
        for match in RUBY_RE.finditer(source)
    ]
    plain = RUBY_RE.sub(lambda match: match.group(1), source).replace("〓", "")
    result: dict[str, Any] = {"text": plain, "readings": readings}
    if deleted_segments:
        result["deleted_segments"] = deleted_segments
    return result


def _plain_text(value: object) -> str:
    return _annotated_text(value)["text"]


def _zip_entry_name(info: zipfile.ZipInfo) -> str:
    """UTF-8フラグのない旧来のCP932ファイル名を表示用に戻す。"""

    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp932")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _element_text(parent: ET.Element, tag: str) -> str:
    child = parent.find(tag)
    return _clean("" if child is None else "".join(child.itertext()))


def _grammar_records() -> list[dict[str, Any]]:
    zip_path = RAW_DIR / "ninjal" / "nihongo_bunkei_database20260126.zip"
    records: list[dict[str, Any]] = []
    with zipfile.ZipFile(zip_path) as archive:
        entries = sorted(archive.infolist(), key=_zip_entry_name)
        for info in entries:
            xml_name = _zip_entry_name(info)
            root = ET.fromstring(archive.read(info))
            pattern_markup = _element_text(root, "SentencePattern")
            pattern = _plain_text(pattern_markup)
            reading = _element_text(root, "Reading")
            # 同じ表記に複数の意味区分があるため、配布ZIP内の項目名を安定IDの種にする。
            stable_hash = hashlib.sha256(xml_name.encode("utf-8")).hexdigest()[:16]
            senses: list[dict[str, Any]] = []
            indexed_terms = [pattern, reading]
            for sense in root.findall("Sense"):
                connections: list[dict[str, Any]] = []
                for connection in sense.findall("Connection"):
                    connection_type = _annotated_text(
                        _element_text(connection, "ConnectionType")
                    )
                    examples = []
                    for example_set in connection.findall("ExampleSet"):
                        example = {
                            "scene": _annotated_text(
                                _element_text(example_set, "SceneDescription")
                            ),
                            "example": _annotated_text(
                                _element_text(example_set, "Example")
                            ),
                            "note": _annotated_text(
                                _element_text(example_set, "ExampleNote")
                            ),
                        }
                        examples.append(example)
                    connections.append(
                        {"connection_type": connection_type, "examples": examples}
                    )
                    indexed_terms.append(connection_type["text"])
                orthography = _split(_element_text(sense, "Orthography"))
                alternative_forms = _split(_element_text(sense, "AlternativeForm"))
                similar = _split(_element_text(sense, "SimilarExpressions"))
                contrasting = _split(_element_text(sense, "ContrastingExpression"))
                categories = [
                    _plain_text(value)
                    for value in _split(_element_text(sense, "SenceCategory"), "｜")
                ]
                indexed_terms.extend(
                    [
                        *(_plain_text(value) for value in orthography),
                        *(_plain_text(value) for value in alternative_forms),
                        *(_plain_text(value) for value in similar),
                        *(_plain_text(value) for value in contrasting),
                        *categories,
                    ]
                )
                level_text = _element_text(sense, "Level")
                senses.append(
                    {
                        "categories": categories,
                        "difficulty_level": (
                            int(level_text) if level_text.isdigit() else level_text or None
                        ),
                        "usage": _annotated_text(_element_text(sense, "Usage")),
                        "usage_notes": _annotated_text(
                            _element_text(sense, "UsageNotes")
                        ),
                        "style": _annotated_text(_element_text(sense, "Style")),
                        "common_collocations": _annotated_text(
                            _element_text(sense, "CommonlyUsedWordsTogether")
                        ),
                        "orthography": [_annotated_text(value) for value in orthography],
                        "alternative_forms": [
                            _annotated_text(value) for value in alternative_forms
                        ],
                        "similar_expressions": [
                            _annotated_text(value) for value in similar
                        ],
                        "contrasting_expressions": [
                            _annotated_text(value) for value in contrasting
                        ],
                        "connections": connections,
                    }
                )
            records.append(
                {
                    "id": f"grammar.ninjal.{stable_hash}",
                    "kind": "grammar_pattern",
                    "title_ja": pattern,
                    "reading": reading,
                    "general_explanation": _annotated_text(
                        _element_text(root, "GeneralExplanation")
                    ),
                    "senses": senses,
                    "search_terms": sorted(
                        {term for term in _nonempty(indexed_terms) if len(term) <= 120}
                    ),
                    "source_id": "src.ninjal.sentence-patterns",
                    "source_version": "2026.01",
                    "source_creators": [
                        "パルデシ, プラシャント",
                        "砂川, 有里子",
                    ],
                    "source_doi": "10.15084/0002000610",
                    "source_locator": xml_name,
                    "license": "CC BY 4.0",
                    "license_url": "https://creativecommons.org/licenses/by/4.0/",
                }
            )
    return records


def _read_csv(path: Path, encodings: tuple[str, ...]) -> list[dict[str, str]]:
    last_error: UnicodeError | None = None
    for encoding in encodings:
        try:
            with path.open(encoding=encoding, newline="") as handle:
                return [
                    {str(key): _clean(value) for key, value in row.items() if key is not None}
                    for row in csv.DictReader(handle)
                ]
        except UnicodeError as error:
            last_error = error
    raise ValueError(f"文字コードを判定できません: {path}") from last_error


def _education_vocabulary_records() -> list[dict[str, Any]]:
    base = RAW_DIR / "ninjal"
    rows_a = _read_csv(
        base / "kyoikukihongoi_2009A.csv", ("utf-8-sig", "cp932")
    )
    rows_b = _read_csv(
        base / "kyoikukihongoi_2009B.csv", ("utf-8-sig", "cp932")
    )
    def by_unique_serial(
        rows: list[dict[str, str]], label: str
    ) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            serial = row.get("通し番号", "")
            if not serial.isdigit():
                continue
            if serial in result:
                raise ValueError(f"{label}の通し番号が重複しています: {serial}")
            result[serial] = row
        return result

    by_serial_a = by_unique_serial(rows_a, "教育基本語彙2009年版A")
    by_serial_b = by_unique_serial(rows_b, "教育基本語彙2009年版B")
    if by_serial_a.keys() != by_serial_b.keys():
        raise ValueError("教育基本語彙2009年版A/Bの通し番号が一致しません")
    records = []
    membership_fields = ("阪本", "新阪本", "田中", "池原", "児言研", "中央", "国語研")
    for serial in sorted(by_serial_b, key=int):
        old = by_serial_a[serial]
        new = by_serial_b[serial]
        if any(old.get(field) != new.get(field) for field in ("見出し", "表記", "品詞")):
            raise ValueError(f"教育基本語彙A/Bの基本項目が不一致です: {serial}")
        records.append(
            {
                "id": f"vocabulary.education.{int(serial)}",
                "kind": "education_vocabulary",
                "title_ja": new["見出し"],
                "headword": new["見出し"],
                "written_form": new.get("表記") or None,
                "part_of_speech_code": new.get("品詞") or None,
                "allocation_level": new.get("語彙配当") or None,
                "source_memberships": {
                    field: new[field] for field in membership_fields if new.get(field)
                },
                "occurrence_count": (
                    int(new["出現数"]) if new.get("出現数", "").isdigit() else None
                ),
                "word_origin_code": new.get("語種") or None,
                "classification_codes_1964": _nonempty(
                    old.get(f"分類番号{number}") or old.get(f"分類番号{'１２３４'[number - 1]}")
                    for number in range(1, 5)
                ),
                "classification_codes_2004": _nonempty(
                    new.get(f"分類番号{number}") for number in range(1, 11)
                ),
                "search_terms": _nonempty((new["見出し"], new.get("表記"))),
                "source_id": "src.ninjal.education-vocabulary",
                "source_version": "2009A+2009B",
                "license": "CC BY 4.0",
            }
        )
    return records


def _japanese_education_vocabulary_records() -> list[dict[str, Any]]:
    path = RAW_DIR / "ninjal" / "rokushutaisho.csv"
    rows = _read_csv(path, ("utf-8-sig", "cp932"))
    membership_fields = ("国語研", "初級500語", "七種対照", "工藤", "木幡", "玉村")
    records = []
    seen_serials: set[str] = set()
    for row in rows:
        serial = row.get("通し番号", "")
        if not serial.isdigit():
            continue
        if serial in seen_serials:
            raise ValueError(f"日本語教育基本語彙の通し番号が重複しています: {serial}")
        seen_serials.add(serial)
        records.append(
            {
                "id": f"vocabulary.japanese-education.{int(serial)}",
                "kind": "japanese_education_vocabulary",
                "title_ja": row["見出し"],
                "headword": row["見出し"],
                "written_form": row.get("表記") or None,
                "part_of_speech_code": row.get("品詞") or None,
                "word_origin_code": row.get("語種") or None,
                "allocation_level": row.get("語彙配当") or None,
                "source_memberships": {
                    field: row[field] for field in membership_fields if row.get(field)
                },
                "search_terms": _nonempty((row["見出し"], row.get("表記"))),
                "source_id": "src.ninjal.education-vocabulary",
                "source_version": "2009",
                "license": "CC BY 4.0",
            }
        )
    return records


def _historical_hiragana_records() -> list[dict[str, Any]]:
    """Unicodeの公式文字名から歴史的平仮名・変体仮名を抽出する。"""

    path = RAW_DIR / "unicode" / "UnicodeData-17.0.0.txt"
    records: list[dict[str, Any]] = []
    hentaigana_count = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        fields = raw_line.split(";")
        codepoint_hex, unicode_name, general_category = fields[:3]
        if unicode_name.startswith("HENTAIGANA LETTER "):
            category = "変体仮名"
            designator = unicode_name.removeprefix("HENTAIGANA LETTER ")
            designator = re.sub(r"-\d+$", "", designator)
            hentaigana_count += 1
        elif unicode_name in {
            "HIRAGANA LETTER ARCHAIC YE",
            "HIRAGANA LETTER ARCHAIC WU",
        }:
            category = "歴史的平仮名"
            designator = unicode_name.removeprefix("HIRAGANA LETTER ARCHAIC ")
        else:
            continue
        romanized_candidates = designator.split("-")
        unknown = [value for value in romanized_candidates if value not in ROMANIZED_KANA]
        if unknown:
            raise ValueError(f"未対応のUnicode仮名名です: {unicode_name}: {unknown}")
        name_component_candidates = list(
            dict.fromkeys(ROMANIZED_KANA[value] for value in romanized_candidates)
        )
        modern_candidates = list(
            dict.fromkeys(
                {"ゐ": "い", "ゑ": "え"}.get(value, value)
                for value in name_component_candidates
            )
        )
        codepoint = int(codepoint_hex, 16)
        character = chr(codepoint)
        codepoint_label = f"U+{codepoint:04X}"
        records.append(
            {
                "id": f"unicode.historical-hiragana.u{codepoint:x}",
                "kind": "historical_hiragana_character",
                "title_ja": f"{category}『{character}』",
                "character": character,
                "codepoint": codepoint_label,
                "codepoint_int": codepoint,
                "unicode_name": unicode_name,
                "unicode_general_category": general_category,
                "unicode_version": "17.0.0",
                "character_category_ja": category,
                "romanized_name_components": romanized_candidates,
                "modern_search_candidates": modern_candidates,
                "modern_search_candidates_status": "editorial_candidate_only",
                "candidate_only": True,
                "search_terms": list(
                    dict.fromkeys(
                        _nonempty((
                        character,
                        codepoint_label,
                        unicode_name,
                        category,
                        *modern_candidates,
                        ))
                    )
                ),
                "source_id": "src.unicode.ucd",
                "source_version": "17.0.0",
                "source_locator": f"UnicodeData.txt {codepoint_label}",
                "license": "Unicode License v3",
            }
        )
    if hentaigana_count != 285 or len(records) != 287:
        raise ValueError(
            "Unicode 17.0.0の変体仮名・歴史的平仮名件数が想定と異なります: "
            f"hentaigana={hentaigana_count}, total={len(records)}"
        )
    return records


def _curriculum_records() -> list[dict[str, Any]]:
    files = (
        ("小学校", "82V12", "curriculum_codes_elementary_82V12.csv"),
        ("中学校", "83V11", "curriculum_codes_junior_high_83V11.csv"),
        ("高等学校", "84V10", "curriculum_codes_senior_high_84V10.csv"),
    )
    records = []
    for stage, version, filename in files:
        path = RAW_DIR / "mext" / filename
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            next(reader)
            header = next(reader)
            for raw_row in reader:
                row = dict(zip(header, raw_row))
                if _clean(row.get("教科等")) != "国語":
                    continue
                code = _clean(row.get("学習指導要領コード"))
                text = _clean(row.get("学習指導要領テキスト"))
                records.append(
                    {
                        "id": f"curriculum.mext.{code.lower()}",
                        "kind": "curriculum_code",
                        "title_ja": text,
                        "school_stage": stage,
                        "version": version,
                        "subject": "国語",
                        "sequence_number": int(row["No"]),
                        "curriculum_text": text,
                        "curriculum_code": code,
                        "search_terms": [code, stage, "国語", text],
                        "source_id": "src.mext.curriculum-codes",
                        "license": "文部科学省ウェブサイト利用規約",
                    }
                )
    return records


class _JoyoIndexParser(HTMLParser):
    """文化庁の音訓索引表を、セル内の改行を保って読み取る。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.rows: list[list[str]] = []
        self._cells: list[str] = []
        self._buffer: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "urlist":
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.in_row = True
            self._cells = []
        elif self.in_row and tag == "td":
            self.in_cell = True
            self._buffer = []
        elif self.in_cell and tag == "br":
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.in_cell:
            self._cells.append("".join(self._buffer))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self._cells:
                self.rows.append(self._cells)
            self.in_row = False
        elif tag == "table" and self.in_table:
            self.in_table = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self._buffer.append(data)


def _source_text_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _assert_safe_official_text(value: str, *, locator: str) -> None:
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        if (
            character == "\ufffd"
            or 0xE000 <= codepoint <= 0xF8FF
            or category.startswith("C")
        ):
            raise ValueError(
                f"公式資料の文字に不正な符号位置があります: {locator}: "
                f"U+{codepoint:04X}"
            )


def _joyo_kanji_records() -> list[dict[str, Any]]:
    """文化庁の現行常用漢字表音訓索引を一字一件へ正規化する。"""

    path = RAW_DIR / "bunka" / "joyokanjisakuin.html"
    parser = _JoyoIndexParser()
    parser.feed(path.read_bytes().decode("cp932"))
    if len(parser.rows) != 2_136:
        raise ValueError(
            "常用漢字表の字種数が想定と異なります: "
            f"{len(parser.rows)} != 2136"
        )

    records: list[dict[str, Any]] = []
    seen_characters: set[str] = set()
    reading_counts = {"音読み": 0, "訓読み": 0}
    for source_order, cells in enumerate(parser.rows, 1):
        if len(cells) != 4:
            raise ValueError(
                f"常用漢字表の列数が不正です: row={source_order}, columns={len(cells)}"
            )
        source_display_label = "".join(_source_text_lines(cells[0]))
        if not source_display_label:
            raise ValueError(f"常用漢字表の字種欄が空です: row={source_order}")
        character = source_display_label[0]
        if (
            len(character) != 1
            or unicodedata.normalize("NFC", character) != character
            or not unicodedata.name(character, "").startswith("CJK ")
        ):
            raise ValueError(
                f"常用漢字表の字種を確定できません: row={source_order}: "
                f"{source_display_label!r}"
            )
        if character in seen_characters:
            raise ValueError(f"常用漢字表の字種が重複しています: {character}")
        seen_characters.add(character)

        reading_lines = _source_text_lines(cells[1])
        example_lines = _source_text_lines(cells[2])
        note_lines = _source_text_lines(cells[3])
        readings: list[dict[str, str]] = []
        for reading_order, reading in enumerate(reading_lines, 1):
            has_hiragana = any("\u3040" <= value <= "\u309f" for value in reading)
            has_katakana = any("\u30a0" <= value <= "\u30ff" for value in reading)
            if has_hiragana:
                reading_type_ja = "訓読み"
            elif has_katakana:
                reading_type_ja = "音読み"
            else:
                raise ValueError(
                    f"常用漢字表の音訓区分を確定できません: {character}: {reading}"
                )
            reading_counts[reading_type_ja] += 1
            readings.append(
                {
                    "order": reading_order,
                    "reading_type_ja": reading_type_ja,
                    "reading": reading,
                }
            )

        parenthesized_printed_forms = [
            value.strip()
            for value in re.findall(r"（([^）]+)）", source_display_label)
            if value.strip()
        ]
        codepoint = ord(character)
        codepoint_label = f"U+{codepoint:04X}"
        split_examples = [
            part.strip()
            for line in example_lines
            for part in re.split(r"[，、]", line)
            if part.strip()
        ]
        search_terms = list(
            dict.fromkeys(
                value
                for value in _nonempty(
                    (
                        character,
                        codepoint_label,
                        source_display_label,
                        *(row["reading"] for row in readings),
                        *example_lines,
                        *split_examples,
                        *note_lines,
                        *parenthesized_printed_forms,
                    )
                )
                if len(value) <= 120
            )
        )
        for value in (
            source_display_label,
            *reading_lines,
            *example_lines,
            *note_lines,
        ):
            _assert_safe_official_text(
                value, locator=f"常用漢字表の音訓索引 {character}"
            )
        records.append(
            {
                "id": f"kanji.joyo.u{codepoint:x}",
                "kind": "joyo_kanji_entry",
                "title_ja": character,
                "character": character,
                "codepoint": codepoint_label,
                "codepoint_int": codepoint,
                "source_order": source_order,
                "source_display_label": source_display_label,
                "parenthesized_printed_forms": parenthesized_printed_forms,
                "readings": readings,
                # 音訓と例の行数が一致しない字があるため、対応を推定しない。
                "example_lines": example_lines,
                "note_lines": note_lines,
                "search_terms": search_terms,
                "source_id": "src.bunka.joyo-kanji-table",
                "source_version": "平成22年内閣告示第2号",
                "source_locator": f"常用漢字表の音訓索引「{character}」",
                "license": "文部科学省ウェブサイト利用規約",
            }
        )
    if reading_counts != {"音読み": 2_352, "訓読み": 2_036}:
        raise ValueError(
            "常用漢字表の音訓件数が想定と異なります: "
            f"{reading_counts}"
        )
    return records


def _grade_level_kanji_records(
    joyo_characters: set[str],
) -> list[dict[str, Any]]:
    """現行の学年別漢字配当表を、配当学年だけを示す一字一件へ変換する。"""

    path = (
        REFERENCE_DIR
        / "data"
        / "editorial"
        / "grade_level_kanji_allocation_transcription.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_counts = {1: 80, 2: 160, 3: 200, 4: 202, 5: 193, 6: 191}
    grades = payload.get("grades")
    if (
        payload.get("schema_version") != 1
        or payload.get("source_id") != "src.mext.grade-level-kanji-table"
        or not isinstance(grades, list)
    ):
        raise ValueError("学年別漢字配当表の転記ファイルが不正です")

    records: list[dict[str, Any]] = []
    seen_characters: set[str] = set()
    for grade_row in grades:
        grade = grade_row.get("grade")
        characters = grade_row.get("characters")
        expected = expected_counts.get(grade)
        if (
            expected is None
            or grade_row.get("expected_count") != expected
            or not isinstance(characters, str)
            or unicodedata.normalize("NFC", characters) != characters
            or len(characters) != expected
            or len(set(characters)) != expected
        ):
            raise ValueError(f"第{grade}学年の配当漢字転記が不正です")
        for source_order, character in enumerate(characters, 1):
            _assert_safe_official_text(
                character, locator=f"学年別漢字配当表 第{grade}学年"
            )
            if character in seen_characters:
                raise ValueError(
                    f"学年別漢字配当表で字種が重複しています: {character}"
                )
            if character not in joyo_characters:
                raise ValueError(
                    f"学年別漢字配当表の字種が常用漢字表にありません: {character}"
                )
            seen_characters.add(character)
            codepoint = ord(character)
            codepoint_label = f"U+{codepoint:04X}"
            school_grade_ja = f"第{grade}学年"
            records.append(
                {
                    "id": f"kanji.grade-allocation.u{codepoint:x}",
                    "kind": "grade_level_kanji_allocation",
                    "title_ja": f"{character}（{school_grade_ja}）",
                    "character": character,
                    "codepoint": codepoint_label,
                    "codepoint_int": codepoint,
                    "school_stage_ja": "小学校",
                    "school_grade": grade,
                    "school_grade_ja": school_grade_ja,
                    "source_order_within_grade": source_order,
                    "allocation_scope": "character_only",
                    "joyo_kanji_id": f"kanji.joyo.u{codepoint:x}",
                    "search_terms": [
                        character,
                        codepoint_label,
                        school_grade_ja,
                        f"小学{grade}年",
                        "学年別漢字配当表",
                    ],
                    "source_id": "src.mext.grade-level-kanji-table",
                    "source_version": "平成29年文部科学省告示第63号",
                    "source_locator": f"別表「学年別漢字配当表」{school_grade_ja}",
                    "license": "文部科学省ウェブサイト利用規約",
                }
            )
    if len(records) != 1_026 or len(seen_characters) != 1_026:
        raise ValueError(
            "学年別漢字配当表の総字数が想定と異なります: "
            f"{len(records)} != 1026"
        )
    return records


def _serialize_jsonl(records: list[dict[str, Any]]) -> tuple[bytes, list[tuple[int, int]]]:
    chunks: list[bytes] = []
    locators: list[tuple[int, int]] = []
    offset = 0
    for record in records:
        chunk = (
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        chunks.append(chunk)
        locators.append((offset, len(chunk)))
        offset += len(chunk)
    return b"".join(chunks), locators


def _index_terms(record: dict[str, Any]) -> list[str]:
    return sorted(
        {
            value
            for value in [record["title_ja"], *record.get("search_terms", [])]
            if value
        }
    )


def build_payloads() -> tuple[dict[Path, bytes], dict[str, Any], dict[str, Any]]:
    joyo_kanji_records = _joyo_kanji_records()
    grade_level_kanji_records = _grade_level_kanji_records(
        {record["character"] for record in joyo_kanji_records}
    )
    datasets = (
        ("grammar_patterns", "grammar_patterns.jsonl", _grammar_records()),
        (
            "education_basic_vocabulary",
            "education_basic_vocabulary.jsonl",
            _education_vocabulary_records(),
        ),
        (
            "japanese_education_basic_vocabulary",
            "japanese_education_basic_vocabulary.jsonl",
            _japanese_education_vocabulary_records(),
        ),
        (
            "historical_hiragana_unicode",
            "historical_hiragana_unicode.jsonl",
            _historical_hiragana_records(),
        ),
        ("curriculum_japanese", "curriculum_japanese.jsonl", _curriculum_records()),
        ("joyo_kanji", "joyo_kanji.jsonl", joyo_kanji_records),
        (
            "grade_level_kanji_allocation",
            "grade_level_kanji_allocation.jsonl",
            grade_level_kanji_records,
        ),
    )
    files: dict[Path, bytes] = {}
    seen_ids: set[str] = set()
    dataset_rows = []
    for dataset_id, filename, records in datasets:
        body, locators = _serialize_jsonl(records)
        path = NORMALIZED_DIR / filename
        files[path] = body
        index_filename = filename.removesuffix(".jsonl") + ".index.jsonl"
        index_records: list[dict[str, Any]] = []
        for record, (offset, length) in zip(records, locators, strict=True):
            record_id = record["id"]
            if record_id in seen_ids:
                raise ValueError(f"duplicate normalized id: {record_id}")
            seen_ids.add(record_id)
            index_records.append(
                {
                    "id": record_id,
                    "dataset_id": dataset_id,
                    "dataset_path": filename,
                    "byte_offset": offset,
                    "byte_length": length,
                    "kind": record["kind"],
                    "title_ja": record["title_ja"],
                    "search_terms": _index_terms(record),
                }
            )
        index_body, _ = _serialize_jsonl(index_records)
        files[NORMALIZED_DIR / index_filename] = index_body
        dataset_rows.append(
            {
                "id": dataset_id,
                "path": filename,
                "index_path": index_filename,
                "record_count": len(records),
                "sha256": hashlib.sha256(body).hexdigest(),
                "index_sha256": hashlib.sha256(index_body).hexdigest(),
            }
        )
    index = {
        "schema_version": 2,
        "snapshot_date": "2026-09-01",
        "generator": "scripts/build_language_knowledge_data.py",
        "search_policy": "stream_dataset_indexes_explicit_terms_only",
        "datasets": dataset_rows,
    }
    raw_manifest = []
    for definition in RAW_DATASETS:
        path = RAW_DIR / definition["path"]
        if not path.is_file():
            raise FileNotFoundError(path)
        row = {
            **definition,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "downloaded_at": "2026-09-01",
        }
        if license_path := definition.get("license_path"):
            license_file = RAW_DIR / str(license_path)
            if not license_file.is_file():
                raise FileNotFoundError(license_file)
            row["license_sha256"] = sha256(license_file)
        raw_manifest.append(row)
    editorial_manifest = []
    editorial_root = REFERENCE_DIR / "data" / "editorial"
    for definition in EDITORIAL_INPUTS:
        path = editorial_root / definition["path"]
        if not path.is_file():
            raise FileNotFoundError(path)
        editorial_manifest.append(
            {
                **definition,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "reviewed_at": "2026-09-01",
            }
        )
    manifest = {
        "schema_version": 1,
        "snapshot_date": "2026-09-01",
        "source_policy": "official_and_public_institutions_only",
        "runtime_network_access": False,
        "raw_datasets": raw_manifest,
        "editorial_inputs": editorial_manifest,
        "normalized_datasets": dataset_rows,
        "attribution_ja": [
            "パルデシ・プラシャント、砂川有里子『日本語文型データベース（バージョン2026.01）』、国立国語研究所、DOI:10.15084/0002000610、CC BY 4.0（https://creativecommons.org/licenses/by/4.0/）。XMLをJSON Linesへ変換し、ルビ・削除記号を構造化した。",
            "国立国語研究所『教育基本語彙データベース・日本語教育基本語彙データベース』（2009年版）、CC BY 4.0。",
            "Unicode Consortium『Unicode Character Database 17.0.0』、Unicode License v3。歴史的平仮名・変体仮名を抽出し、公式文字名のローマ字構成要素を保持した上で、検索補助用の現代仮名候補を編集派生として付した。",
            "文部科学省『学習指導要領コードのコード表（全体版）』。正規化・国語科抽出を行った。",
            "文化庁『常用漢字表（平成22年内閣告示第2号）』および公式音訓索引。2,136字、音訓4,388件（音2,352件・訓2,036件）を一字一件へ正規化した。音訓と例欄の個別対応は推定していない。",
            "文部科学省『小学校学習指導要領（平成29年告示）』別表「学年別漢字配当表」。公式PDFで画像化された文字を転記・目視照合し、1,026字を配当学年だけを示す一字一件へ正規化した。",
        ],
    }
    return files, index, manifest


def _json_bytes(payload: dict[str, Any], *, compact_output: bool = False) -> bytes:
    if compact_output:
        return (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, body: bytes) -> None:
    """同一ディレクトリの一時ファイルから置換し、部分ファイルを残さない。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    _validate_output_path(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            _validate_output_path(temporary_path)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        # os.replaceはsymlink先へ書かず、宛先のディレクトリエントリを置換する。
        _validate_output_path(path)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    files, index, manifest = build_payloads()
    files[INDEX_PATH] = _json_bytes(index, compact_output=True)
    files[MANIFEST_PATH] = _json_bytes(manifest)
    for path in files:
        _validate_output_path(path)
    stale = [
        path
        for path, body in files.items()
        if not path.exists() or path.read_bytes() != body
    ]
    if args.check:
        if stale:
            for path in stale:
                print(f"stale: {path.relative_to(ROOT)}", file=sys.stderr)
            return 1
        print("normalized language knowledge data is current")
        return 0
    for path, body in files.items():
        _atomic_write(path, body)
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
