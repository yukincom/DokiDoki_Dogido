#!/usr/bin/env python3
"""Capture canonical routing contexts; no model, service or memory writes."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'dogido-rust/scripts'))
from dogido_server.player_input import routing
from dogido_server.service import DogidoService
from reading_overlay import apply_reading_snapshot

NOW = '2026-09-29T15:16:17.123456+09:00'


def generate():
    cases = json.loads((ROOT / 'dogido-rust/fixtures/input-policy.json').read_text())
    inputs = [(c['raw'], c['normalized'], '', []) for c in cases]
    inputs.extend([
        ('/say 静かにして', '/say 静かにして', '', []),
        ('/読み: 草地=くさち', '/読み: 草地=くさち', '句を思い出して', []),
        ('草地はくさち', '敵は何体？', '', []),
        ('県に持ち替えて', '県に持ち替えて', '剣に持ち替えて', []),
        ('枕詞のこと', '枕詞のこと', '枕詞って何？', []),
        ('剣に持ち替えて', '剣に持ち替えて', '枕詞って何？', []),
        ('句を思い出して', '今日の草地の句', '', []),
        ('冷帯の句', '冷帯の句', '', []),
        ('すばらしそうちの句', 'すばらしそうちの句', '', [{'surface':'草地','reading':'すばらしそうち'}]),
        ('くさちの句', 'くさちの句', '', [{'surface':'草地','reading':'すばらしそうち'}]),
    ])
    parse_range = routing.parse_haiku_time_range
    result = []
    with patch.object(routing, 'parse_haiku_time_range', lambda text: parse_range(text, now=datetime.fromisoformat(NOW))):
        for raw, normalized, interpreted, overlay in inputs:
            apply_reading_snapshot(overlay)
            context = routing.route_prepared_player_input(raw, normalized, interpreted)
            general = DogidoService._is_general_conversation_input(None, SimpleNamespace(haiku_workshop=None), context)
            result.append(dict(raw=raw, normalized=normalized, interpreted=interpreted, overlay=overlay,
                               now=NOW, expected=asdict(context), general=general))
    apply_reading_snapshot([])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    # Canonical date parsing uses the host timezone; pin it only within this generator process.
    os.environ['TZ'] = 'Asia/Tokyo'
    time.tzset()
    path = ROOT / 'dogido-rust/fixtures/input-context.json'
    result = generate()
    text = json.dumps(result, ensure_ascii=False, indent=2, default=lambda v: v.isoformat()) + '\n'
    if args.check:
        assert path.read_text() == text
    else:
        path.write_text(text)
    print('input context fixtures:', len(result))


if __name__ == '__main__':
    main()
