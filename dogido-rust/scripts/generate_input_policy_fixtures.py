#!/usr/bin/env python3
"""Capture pure input guardrails from canonical Python; no runtime services."""
import argparse
import ast
import importlib.util
import inspect
import json
from pathlib import Path

NAMES = (
    'HUSH_KEYWORDS', 'HOSTILE_QUERY_KEYWORDS', 'HOSTILE_COUNT_QUERY_KEYWORDS',
    'DRAGON_KEYWORDS', 'DIRECTION_QUERY_KEYWORDS', 'HOSTILE_DIRECTION_REFERENTS',
    'HAIKU_SAVE_PREFIXES', 'HAIKU_REVISE_PREFIXES', 'SAVE_LAST_HAIKU_KEYWORDS',
    'INVENTORY_TOPIC_KEYWORDS', 'INVENTORY_ITEM_HINT_KEYWORDS', 'POSSESSION_HINT_KEYWORDS',
    '_SOUND_TOPIC_MARKERS', '_SOUND_QUERY_SHAPES', '_MUSIC_TOPIC_MARKERS',
)
FLAGS = ('wants_quiet', 'should_block_ambient', 'asks_hostile_count',
         'asks_hostile_direction', 'asks_dragon_direction', 'asks_save_last_haiku',
         'asks_inventory', 'asks_about_sound')


def load_source(repo):
    path = repo / 'dogido_server/player_input/guardrails.py'
    spec = importlib.util.spec_from_file_location('canonical_input_guardrails', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patterns(function, operation):
    tree = ast.parse(inspect.getsource(function))
    return [ast.literal_eval(node.args[0]) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 're'
            and node.func.attr == operation]


def policy(source):
    value = {name: list(getattr(source, name)) for name in NAMES}
    # Python's equal-length set iteration order is irrelevant to this boolean regex;
    # sort the alternatives so the checked-in asset is deterministic.
    first, rest = source._SHORT_INVENTORY_QUERY.pattern.split(r')\s*', 1)
    value['short_inventory'] = '(?:' + '|'.join(sorted(first[3:].split('|'))) + r')\s*' + rest
    value['reading_matches'] = patterns(source.extract_reading_correction, 'match')
    value['reading_searches'] = patterns(source.extract_reading_correction, 'search')
    value['explicit_matches'] = patterns(source.is_explicit_reading_correction, 'match')
    value['direction_strip'] = patterns(source.asks_hostile_direction, 'sub')[0]
    return value


def projection(source, raw, normalized):
    value = {name: getattr(source, name)(normalized) for name in FLAGS}
    value['player_haiku_text'] = source.extract_player_haiku(raw)
    value['revised_haiku_text'] = source.extract_revised_haiku(raw)
    explicit = source.is_explicit_reading_correction(raw)
    value['is_explicit_reading_correction'] = explicit
    correction = source.extract_reading_correction(raw)
    value['reading_correction'] = None
    if correction:
        surface, reading, wrong = correction
        value['reading_correction'] = dict(surface=surface, reading=reading,
            wrong_reading=wrong, explicit=explicit or wrong is not None)
    return value


def examples(repo, source):
    samples = set()
    # Keep existing tests' utterances without importing their test/runtime dependencies.
    for name in ('test_player_input.py', 'test_player_chat.py', 'test_haiku_feedback.py', 'test_survival_chat_presence.py'):
        path = repo / 'tests' / name
        if path.is_file():
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and 0 < len(node.value) <= 240:
                    samples.add(node.value)
    samples.update(('', ' ', '\u001c', 'おはようさん', '/静かにして', 'うるさい', 'ウルサイ',
        '静かにしてとは言ってない', '黙れと言われた', '敵何体？', 'モブ何体？', 'モンスター何体？',
        'あと何匹', '残り', 'ドラゴンどこ？', 'どらごんはどっち', 'あいつの方向', 'どっち…？',
        'どっち,', 'どこにいる？', '川柳をさっきのに保存', '今の句保存しない',
        '松明ある？', '石炭じゃない？', '石炭はもうないの？', '石炭あるいは丸石',
        '読み: 草地=くさち', '草地はくさち', '草地の読みはくさち', 'おはようさん',
        'そうちじゃなくてくさち', 'ソウチジャナクテクサチ', '読み: 草地=クサチ',
        '草地はくさちです', '草地の読みは くさち です', '草地はくさち？',
        '「草地はくさち」', '広がる緑は、にしてはどうでした', '広がる緑はにしてはどうでした',
        '読み: 川\n柳=せんりゅう', '読み:\n草地=くさち', '草地は\nくさち',
        '読み: 草地=くさち\n余白', '川柳: 一行\n二行\n三行\n四行', '直し: 一行／二行／三行',
        '川柳: 一行/二行\n三行', '川柳: ///', '句直し:\u001c一行\u001f/二行|三行',
        '川柳：\n', '「川柳: 一行/二行/三行」', '音\t\t\t', '音\n\n\n\n\n', '音だけを話題にする長い文'))
    for words in (source.HUSH_KEYWORDS, source.HOSTILE_QUERY_KEYWORDS, source.DRAGON_KEYWORDS,
                  source.INVENTORY_ITEM_HINT_KEYWORDS, source.INVENTORY_TOPIC_KEYWORDS,
                  source._SOUND_TOPIC_MARKERS):
        for word in words:
            for suffix in ('', '？', 'ない', '何体', 'どこ', 'は何本？', 'あるいは', 'ある？', '持ってる？'):
                samples.add(word + suffix)
    for word in ('石炭', 'セキタン', '松明', '剣', '何', 'なにか'):
        for space in ('', ' ', '\t', '\n', '\r\n', '\u001c', '\u0085', '\u00a0', '\u2003', '\u3000'):
            for suffix in ('ある', 'ない？', '何本？', 'もう 少し ありますか。', 'あるいは何か'):
                samples.add(word + space + suffix)
    for prefix in source.HAIKU_SAVE_PREFIXES + source.HAIKU_REVISE_PREFIXES:
        for payload in ('', '一行', '一行 二行 三行', '一行\n二行\n三行\n四行', '一行/二行/三行',
                        '一行／二行', '一行|二行', '一行｜二行', '///', '\n/\n', '「一行」\n「二行」\n「三行」'):
            samples.add(prefix + payload)
    result = []
    for raw in sorted(samples):
        normalized = ' '.join(raw.replace('\u3000', ' ').split())
        result.append((raw, normalized))
    # Classifier must trust caller's normalized surface; raw keeps its own parsing role.
    result.extend([('草地はくさち', '敵は何体？'), ('川柳: 一行\n二行\n三行', '川柳: 違う句'),
                   ('音', '音\n\n\n\n\n'), ('', ' '), ('', '\u001c'), ('黙れ', ''), ('', '石炭\nある？')])
    return [dict(raw=raw, normalized=normalized, expected=projection(source, raw, normalized))
            for raw, normalized in result]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    source = load_source(args.repo)
    root = args.output_dir
    artifacts = {
        (root / 'input-policy.json' if root else args.repo/'dogido-rust/src/input-policy.json'): policy(source),
        (root / 'input-policy-fixtures.json' if root else args.repo/'dogido-rust/fixtures/input-policy.json'): examples(args.repo, source),
    }
    for path, value in artifacts.items():
        rendered = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
        if args.check:
            assert path.read_text() == rendered, f'stale {path.name}'
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered)
        print(path.name, len(value))


if __name__ == '__main__':
    main()
