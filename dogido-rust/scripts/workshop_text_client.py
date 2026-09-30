#!/usr/bin/env python3
"""Local API client for text-workshop maintenance and verification."""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_BASE = 'http://127.0.0.1:5057'


def call(path, body=None, base=DEFAULT_BASE):
    if urlsplit(base).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('The text workshop client accepts localhost only.')
    request = Request(base.rstrip('/') + '/api/' + path,
                      data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
                      headers={} if body is None else {'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f'HTTP {error.code}: {error.read().decode()}') from error


def speak(session_id, text, base=DEFAULT_BASE, timeout=120):
    accepted = call('input', {'session_id': session_id, 'text': text}, base)
    if not accepted.get('accepted'):
        return accepted
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = call('snapshot', {'session_id': session_id}, base)
        row = next((r for r in snapshot['dialogue']['utterances'] if r['turn_id'] == accepted['turn_id']), None)
        if row and row['playback_status'] not in {'routing', 'generating', 'queued', 'started', 'waiting_for_safety'}:
            if row['playback_status'] == 'audio_disabled' and row.get('text'):
                displayed = call('displayed', {'session_id': session_id, 'turn_id': row['turn_id']}, base)
                row['text_delivery_acknowledged'] = displayed['accepted']
            return {'turn': row, 'workshop': snapshot['workshop']}
        time.sleep(.25)
    raise TimeoutError('Reply is still pending. Read the snapshot or interrupt this turn; do not resend it automatically.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=DEFAULT_BASE)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['poems', 'sessions', 'prompts-get', 'shutdown']:
        commands.add_parser(name)
    opening = commands.add_parser('open')
    opening.add_argument('--key', required=True)
    for name in ['snapshot', 'last-prompt', 'interrupt', 'close']:
        sub = commands.add_parser(name)
        sub.add_argument('--session', required=True)
    say = commands.add_parser('say')
    say.add_argument('--session', required=True)
    say.add_argument('--text', required=True)
    setting = commands.add_parser('prompts-set')
    setting.add_argument('--file', type=Path, required=True)
    setting.add_argument('--expected-version', type=int)
    backup = commands.add_parser('backup')
    backup.add_argument('--file', type=Path, required=True)
    args = parser.parse_args()
    base = args.base_url
    if args.command == 'say':
        result = speak(args.session, args.text, base)
    elif args.command == 'open':
        result = call('open', {'key': args.key}, base)
    elif args.command == 'prompts-set':
        source = json.loads(args.file.read_text())
        settings = source.get('settings', source)
        version = args.expected_version
        if version is None:
            version = source.get('version', call('prompts', base=base)['version'])
        result = call('prompts', {'settings': settings, 'expected_version': version}, base)
    elif args.command == 'backup':
        poems = call('poems', base=base)
        sessions = call('snapshot', {'session_id': ''}, base)['dialogue']['sessions']
        records = []
        for session in sessions:
            sid = session['session_id']
            snapshot = call('snapshot', {'session_id': sid}, base)
            workshop = snapshot['workshop']
            state = snapshot.get('text_state') or {}
            candidates = [p for p in poems if p['key'] == state.get('key')] if state else [p for p in poems if p['text'].splitlines() == workshop['canonical_lines']]
            if len(candidates) != 1 or (not state and workshop['pending_lines']):
                raise RuntimeError('編集済み・未採用案・重複句があるため、会話を自動移行できません。')
            if any(r['playback_status'] in {'routing', 'waiting_for_safety', 'generating', 'queued', 'started'} for r in snapshot['dialogue']['utterances']):
                raise RuntimeError('返答を待ってから移行してください。')
            records.append({'key': candidates[0]['key'], 'session_id': sid, 'snapshot': snapshot})
        args.file.parent.mkdir(parents=True, exist_ok=True)
        args.file.write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
        result = {'sessions': len(records), 'file': str(args.file)}
    elif args.command == 'shutdown':
        result = call('shutdown', {}, base)
    elif args.command in {'snapshot', 'last-prompt', 'interrupt', 'close'}:
        result = call(args.command, {'session_id': args.session}, base)
    else:
        result = call('prompts' if args.command == 'prompts-get' else args.command, base=base)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
