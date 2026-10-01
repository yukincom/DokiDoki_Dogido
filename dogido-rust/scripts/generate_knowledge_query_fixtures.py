#!/usr/bin/env python3
"""知識質問の閉じた語彙・構文とPython正本の比較例を採取。DB検索・AI呼出しなし。"""
import ast
from dataclasses import asdict
import inspect
import json
from pathlib import Path
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server import knowledge_query as source


def expression(node):
    return eval(compile(ast.Expression(node), "<canonical grammar>", "eval"), vars(source))


def unicode_ranges(predicate):
    ranges = []
    for point in range(0x110000):
        if predicate(chr(point)):
            if ranges and ranges[-1][1] + 1 == point:
                ranges[-1][1] = point
            else:
                ranges.append([point, point])
    return ranges


def regex_class(ranges):
    return "[" + "".join(f"\\u{{{lo:x}}}" + (f"-\\u{{{hi:x}}}" if lo != hi else "") for lo, hi in ranges) + "]"


def grammar():
    tree = ast.parse(inspect.getsource(source._extract_subject))
    special = [expression(node.value.args[0]) for node in tree.body[0].body
               if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
               and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "fullmatch"]
    patterns = next(expression(node.value) for node in tree.body[0].body
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "patterns" for t in node.targets))
    strip_tree = ast.parse(inspect.getsource(source._strip_subject))
    prefixes = next(expression(node.value) for node in strip_tree.body[0].body
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "prefixes" for t in node.targets))
    names = {
        "question_cues": "_QUESTION_CUES", "generation_requests": "_GENERATION_REQUESTS",
        "japanese": "_JAPANESE_SUBJECT_KEYS", "poetry": "_POETRY_SUBJECT_KEYS",
        "minecraft_exact": "_MINECRAFT_EXACT_SUBJECT_KEYS",
        "minecraft_context": "_MINECRAFT_UNAMBIGUOUS_CONTEXT_MARKERS", "generic": "_GENERIC_SUBJECTS",
    }
    data = {key: sorted(getattr(source, name)) for key, name in names.items()}
    data.update(special=special, patterns=patterns, prefixes=prefixes, unicode_version=unicodedata.unidata_version)
    # Pythonのcasefold（複数字への展開を含む）を同じUnicode版で固定する。
    data["casefold"] = {c: c.casefold() for c in map(chr, range(0x110000)) if c != c.casefold()}
    # 新Unicodeの互換文字・数字を、旧版で未知だった文字へ勝手に適用しない。
    data["unassigned_ranges"] = unicode_ranges(lambda c: unicodedata.category(c) == "Cn")
    data["decimal_class"] = regex_class(unicode_ranges(str.isdecimal))
    data["word_class"] = regex_class(unicode_ranges(lambda c: c.isalnum() or c == "_"))
    return data


def cases():
    values = []
    for path in (ROOT / "tests/test_knowledge_query.py", ROOT / "tests/test_assist_select_sword.py"):
        values.extend(n.value for n in ast.walk(ast.parse(path.read_text()))
                      if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) <= 260)
    subjects = sorted(source._JAPANESE_SUBJECT_KEYS | source._POETRY_SUBJECT_KEYS)
    subjects += [json.loads(line)["title_ja"] for line in
                 (ROOT / "reference/language_education_and_poetry/data/normalized/grammar_patterns.jsonl").read_text().splitlines()]
    for subject in subjects:
        values.extend([f"{subject}って何？", f"{subject}の接続は？", f"『{subject}』とは？"])
    for subject in ["minecraft:diamond_sword", "examplemod:widget", "https://minecraft.net/test", "一", "3", "剣", "これ", "ChatGPT", "~たら？"]:
        for prefix in ["", "ドギド、", "Minecraftで", "MİNECRAFTで", "Mınecraftで", "Java Edition 1.21.11の", "国語で", "世界の詩形で"]:
            for suffix in ["って何？", "のIDは？", "の作り方を教えて", "は何年生で習うの？", "の読みを教えて", "を作って"]:
                values.append(prefix + subject + suffix)
    for separator in ["\n", "\r", "\t", "\x1c", "\x1f", "\u0085", "\u2003"]:
        values.extend([f"Minecraft{separator}1.21.11の石のIDは？", f"川柳{separator}の形式は？", separator + "枕詞って何？" + separator])
    values.extend(["ßって何？", "VERS LIBREって何？", "ｿﾈｯﾄとは？", "Ｍｉｎｅｃｒａｆｔで石のＩＤは？",
                   "一二三の四は何年生で習うの？", "数字の４の漢字って"])
    for char in ["\U0001ccd6", "\U0001e4f1", "\U0001cce5", "\U00011f02", "\u0301"]:
        values.extend([f"国語で{char}って何？", f"国語で{char}IDの意味を教えて",
                       f"Minecraft {char}.21.11の石のIDは？", f"国語でA{char}\u030aって何？"])
    values.extend("国語で" + "あ" * n + "って何？" for n in (232, 233, 234, 240, 241))
    return list(dict.fromkeys(values))


def main():
    data = grammar()
    target = ROOT / "dogido-rust/src/knowledge/query-grammar.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    rows = []
    for text in cases():
        query = source.extract_explicit_knowledge_query(text)
        rows.append({"text": text, "query": asdict(query) if query else None})
    (ROOT / "dogido-rust/fixtures/knowledge-query.json").write_text(
        "[\n" + ",\n".join("  " + json.dumps(row, ensure_ascii=False) for row in rows) + "\n]\n")
    print(f"Captured {len(rows)} query cases; {len(data['japanese'])} Japanese / {len(data['poetry'])} poetry keys; Unicode {data['unicode_version']}")


if __name__ == "__main__":
    main()
