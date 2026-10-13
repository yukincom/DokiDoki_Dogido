"""依頼5分類データの読取り、入力整形、整合検査を行う独立補助。

入力はこのディレクトリのJSONLとラベル対応表、出力はモデル用のtext/labelsと
件数・ハッシュの検査結果。本体の判断・操作・モデル呼出しは担当しない。
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parent
SPLITS = ('train', 'dev', 'test', 'regression')
LABEL_TO_ID = json.loads((ROOT / 'labels.json').read_text(encoding='utf-8'))
FIELDS = {'id', 'group_id', 'text', 'context', 'label', 'needs_clarification',
          'tags', 'source', 'rationale'}
TAGS = {
    'ambiguous', 'cancellation', 'casual', 'context', 'correction', 'count',
    'direct', 'ellipsis', 'future', 'general_knowledge', 'hypothetical',
    'iruka_collision', 'location', 'multiple_requests', 'named_entity',
    'negation', 'orthography', 'past', 'politeness', 'presence', 'quotation',
    'question', 'reference', 'reported_speech', 'rhetorical', 'self_location', 'statement',
}


def read_splits(data_dir: Path = ROOT / 'data') -> dict[str, list[dict]]:
    """UTF-8の4分割を読む。空行や壊れた行は行番号付きで拒否する。"""
    result = {}
    for split in SPLITS:
        path = data_dir / f'{split}.jsonl'
        rows = []
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f'{path.name}:{number}: invalid JSON') from exc
        result[split] = rows
    return result


def model_example(row: dict) -> dict:
    """過去会話→現在発話の順で整形し、整数ラベルを教師値として分離する。

    id・group・tags・rationale・source・needs_clarificationは入力に含めない。
    履歴は古い順の最大4発話。切り詰めはここで行わず、学習時にも現在発話を残す。
    """
    names = {'user': 'ユーザー', 'assistant': 'ドギド'}
    history = '\n'.join(f"{names[t['role']]}: {t['text']}" for t in row['context'])
    text = f"履歴:\n{history or '（なし）'}\n現在のユーザー発話:\n{row['text']}"
    return {'text': text, 'labels': LABEL_TO_ID[row['label']]}


def normalized(text: str) -> str:
    """表記だけの重複検出に使う。モデルへ渡す原文は変更しない。"""
    text = unicodedata.normalize('NFKC', text).casefold()
    text = ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in text)
    return ''.join(c for c in text if not c.isspace() and unicodedata.category(c)[0] != 'P')


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(splits: dict[str, list[dict]]) -> None:
    """型、入力重複、分割混入、確認用注釈を検査し、不整合で停止する。

    自然文の意味やラベルの正しさは人が読む必要がある。この関数は分類しない。
    """
    require(set(splits) == set(SPLITS), 'four splits are required')
    require(list(LABEL_TO_ID.values()) == list(range(5)), 'label IDs must be 0..4 in order')
    ids, inputs, groups, utterances = set(), {}, {}, {}
    for split, rows in splits.items():
        require(isinstance(rows, list) and bool(rows), f'{split}: empty or invalid split')
        for index, row in enumerate(rows, 1):
            where = f'{split}:{index}'
            require(isinstance(row, dict) and set(row) == FIELDS, f'{where}: fields differ')
            require(isinstance(row['id'], str) and re.fullmatch(rf'{split}-[0-9]{{3}}', row['id']) is not None, f'{where}: invalid ID')
            where = row['id']
            require(where not in ids, f'{where}: duplicate ID')
            ids.add(where)
            require(isinstance(row['label'], str) and row['label'] in LABEL_TO_ID, f'{where}: unknown label')
            require(nonempty(row['text']), f'{where}: empty text')
            require(nonempty(row['rationale']), f'{where}: empty rationale')
            require(isinstance(row['group_id'], str) and re.fullmatch(r'[a-z0-9_]+', row['group_id']) is not None, f'{where}: invalid group')
            require(type(row['needs_clarification']) is bool, f'{where}: clarification must be bool')
            require(isinstance(row['tags'], list) and bool(row['tags']) and all(isinstance(t, str) for t in row['tags']), f'{where}: invalid tags')
            require(len(set(row['tags'])) == len(row['tags']) and set(row['tags']) <= TAGS, f'{where}: duplicate/unknown tags')
            require(row['source'] in ('synthetic', 'observed_text'), f'{where}: unknown source')
            require(row['source'] != 'observed_text' or split == 'regression', f'{where}: observed text outside regression')
            ambiguous = bool({'ambiguous', 'multiple_requests'} & set(row['tags']))
            require(row['needs_clarification'] == ambiguous, f'{where}: clarification annotation differs')
            require(not ambiguous or row['label'] == 'other_request', f'{where}: unresolved request must be other_request')
            require(isinstance(row['context'], list) and len(row['context']) <= 4, f'{where}: invalid context length')
            for turn in row['context']:
                require(isinstance(turn, dict) and set(turn) == {'role', 'text'}, f'{where}: invalid context fields')
                require(turn['role'] in ('user', 'assistant') and nonempty(turn['text']), f'{where}: invalid context turn')
            group = row['group_id']
            require(group not in groups or groups[group] == split, f'{where}: group crosses splits: {group}')
            groups[group] = split
            utterance = normalized(row['text'])
            require(bool(utterance), f'{where}: no lexical content')
            require(utterance not in utterances or utterances[utterance] == split, f'{where}: normalized utterance crosses splits')
            utterances[utterance] = split
            fingerprint = normalized(model_example(row)['text'])
            require(fingerprint not in inputs, f'{where}: duplicate input with {inputs.get(fingerprint)}')
            inputs[fingerprint] = where
        require(set(r['label'] for r in rows) == set(LABEL_TO_ID), f'{split}: a class is missing')
        require({'negation', 'quotation', 'ellipsis'} <= {t for r in rows for t in r['tags']}, f'{split}: required phenomena missing')


def report(splits: dict[str, list[dict]], data_dir: Path = ROOT / 'data') -> dict:
    """読み取ったレコードから件数を集計し、各正本ファイルのハッシュを添える。"""
    return {split: {
        'rows': len(rows),
        'labels': dict(sorted(Counter(r['label'] for r in rows).items())),
        'groups': len({r['group_id'] for r in rows}),
        'with_context': sum(bool(r['context']) for r in rows),
        'needs_clarification': sum(r['needs_clarification'] for r in rows),
        'sources': dict(sorted(Counter(r['source'] for r in rows).items())),
        'tags': dict(sorted(Counter(t for r in rows for t in r['tags']).items())),
        'max_input_characters': max(len(model_example(r)['text']) for r in rows),
        'sha256': hashlib.sha256((data_dir / f'{split}.jsonl').read_bytes()).hexdigest(),
    } for split, rows in splits.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--write-report', type=Path, help='検査後の件数・SHA-256を指定先に保存')
    parser.add_argument('--check-report', type=Path, help='保存済みの件数・SHA-256との一致を確認')
    args = parser.parse_args()
    splits = read_splits(args.data_dir)
    validate(splits)
    summary = report(splits, args.data_dir)
    if args.check_report:
        require(summary == json.loads(args.check_report.read_text(encoding='utf-8')), 'saved report differs')
    if args.write_report:
        args.write_report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
