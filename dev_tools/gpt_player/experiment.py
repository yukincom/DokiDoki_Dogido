#!/usr/bin/env python3
"""GPTプレイヤー比較の条件表・独立した句コピー・対話記録を作る。

入力は既存の保存句と同一のプレイヤー指示。prepareはオフラインだけで動く。
相談は既存のテキスト相談室とworkshop_text_clientへ渡し、句の採否・保存を
この補助で代行しない。GPT自体の呼出しは親エージェントが一会話ずつ担当する。
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def fixed_file(path, value):
    """最初の条件を比較基準として保存し、後の条件で実行設定が変わったら開始しない。"""
    if path.exists():
        if read(path) != value:
            raise ValueError(f'条件間で固定する設定が変わりました: {path.name}')
    else:
        write(path, value)


def prepare(source, output, design, prompt):
    """最新の原句を確定し、全条件へ同じ原句を独立コピーする。既存出力は上書きしない。"""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError('実験出力と通常記憶は独立した場所を指定してください。')
    if output.exists():
        raise FileExistsError('既存の実験は上書きしません。新しい出力先を指定してください。')
    entries = []
    for path in sorted(source.glob('**/long_term/haiku_entries.jsonl')):
        for number, line in enumerate(path.read_bytes().splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('author') != 'dogido' or row.get('kind') != 'agent_haiku' or len(row.get('lines', [])) != 3:
                continue
            date = dt.datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
            entries.append((date, str(path.relative_to(source)), number, row, sha(line)))
    # 同じ原句IDを二重抽出しない。採用済みrevisionや人間の講評を選句条件に使わない。
    selected, ids = [], set()
    for item in sorted(entries, key=lambda x: (x[0], x[1], x[2]), reverse=True):
        if item[3]['id'] not in ids:
            ids.add(item[3]['id'])
            selected.append(item)
        if len(selected) == design['poem_count']:
            break
    if len(selected) < design['poem_count']:
        raise ValueError('三行を持つ保存句が不足しています。')
    output.mkdir(parents=True)
    (output / 'player_prompt.txt').write_text(prompt)
    write(output / 'design.json', design)
    cases = []
    for index, (_, origin, number, entry, digest) in enumerate(selected, 1):
        case_id = f'p{index:02d}'
        cases.append({'case_id': case_id, 'entry_id': entry['id'], 'source_file': origin,
                      'source_line': number, 'source_record_sha256': digest,
                      'original_text': entry.get('surface_text') or entry['text']})
    rng = random.Random(design['order_seed'])
    runs = []
    for effort in design['reasoning_efforts']:
        block = []
        for repeat in range(1, design['repetitions'] + 1):
            for case, item in zip(cases, selected):
                for label, model in design['models'].items():
                    run_id = f"{case['case_id']}-{label}-{effort}-r{repeat:02d}"
                    run = output / 'runs' / run_id
                    memory = run / 'memory' / 'sessions' / run_id / 'long_term'
                    memory.mkdir(parents=True)
                    (memory / 'haiku_entries.jsonl').write_text(json.dumps(item[3], ensure_ascii=False) + '\n')
                    record = {'run_id': run_id, 'case_id': case['case_id'], 'model_label': label,
                              'player_model_requested': model, 'reasoning_effort': effort,
                              'repetition': repeat, 'entry_id': case['entry_id'],
                              'poem_key': f"{run_id}:{case['entry_id']}",
                              'original_text': case['original_text'],
                              'player_prompt_sha256': sha(prompt.encode()),
                              'max_player_turns': design['max_player_turns'], 'status': 'prepared'}
                    write(run / 'run.json', record)
                    block.append(record)
        rng.shuffle(block)
        runs.extend(block)
    manifest = {'created_at': now(), 'status': 'prepared_not_run', 'selection': 'latest_unique_original_entries',
                'source_memory': str(source), 'prompt_review': design['prompt_review'],
                'player_prompt_sha256': sha(prompt.encode()), 'cases': cases, 'ordered_runs': runs}
    write(output / 'manifest.json', manifest)
    return manifest


def client():
    """公開済みの相談クライアントをそのまま使う。新しい会話・保存経路を作らない。"""
    spec = importlib.util.spec_from_file_location('workshop_text_client', ROOT / 'dogido-rust/scripts/workshop_text_client.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def log(run, event):
    with (run / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps({'at': now(), **event}, ensure_ascii=False) + '\n')


def bind(run, port):
    record = read(run / 'run.json')
    if port in (5055, 5057, 8080) or not 1024 <= port <= 65535:
        raise ValueError('実験専用ポートを使ってください。既定は5067です。')
    api, base = client(), f'http://127.0.0.1:{port}'
    poems = api.call('poems', base=base)
    # コピー先のrun IDを含むキーで照合し、通常相談室や他条件へ書き込まない。
    if len(poems) != 1 or poems[0]['key'] != record['poem_key']:
        raise ValueError('この条件専用の相談室ではありません。')
    return record, api, base


def snapshot(run, record, api, base):
    value = api.call('snapshot', {'session_id': record['session_id']}, base)
    write(run / 'snapshot.json', value)
    return value


def packet(run, record, value):
    """GPTへ渡すのは句・表示された案・会話だけ。内部プロンプトや人間の添削は含めない。"""
    events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
    transcript = []
    for event in events:
        if event['event'] == 'player_sent':
            transcript.append({'role': 'player', 'text': event['text']})
        elif event['event'] == 'dogido_reply' and event.get('text'):
            transcript.append({'role': 'dogido', 'text': event['text']})
    view = value['workshop']
    stored_lines = (value.get('text_state') or {}).get('lines')
    canonical = [line['surface_text'] for line in stored_lines] if stored_lines else view['canonical_lines']
    public = {'original_poem': record['original_text'], 'current_poem': '\n'.join(canonical),
              'pending_poem': '\n'.join(view.get('pending_lines') or []), 'conversation': transcript}
    write(run / 'player_input.json', public)
    return public


def interact(args):
    run = args.run_dir.resolve()
    record, api, base = bind(run, args.port)
    if args.command == 'open':
        if record['status'] != 'prepared':
            raise ValueError('開始済みの条件です。snapshotで状態を確認してください。')
        prompts = api.call('prompts', base=base)
        fixed_file(run.parents[1] / 'dogido_prompt_baseline.json', prompts['settings'])
        record['session_id'] = api.call('open', {'key': record['poem_key']}, base)['session_id']
        record.update(status='open', player_agent_id=args.player_agent_id,
                      player_model_reported=args.player_model_reported, opened_at=now())
        write(run / 'run.json', record)
        write(run / 'dogido_prompts.json', prompts)
        log(run, {'event': 'opened', 'session_id': record['session_id']})
    if 'session_id' not in record:
        raise ValueError('先にopenしてください。')
    if args.command == 'say':
        if record['status'] != 'open':
            raise ValueError('未完了・終了した条件には送信しません。snapshotを確認してください。')
        sent = sum(json.loads(line)['event'] == 'player_sent' for line in (run / 'events.jsonl').read_text().splitlines())
        if sent >= record['max_player_turns']:
            raise ValueError('最大会話数に達しました。finishで記録を確定してください。')
        if api.call('prompts', base=base)['settings'] != read(run / 'dogido_prompts.json')['settings']:
            raise ValueError('相談中にドギドのプロンプトが変わりました。この条件を中止してください。')
        text = args.text_file.read_text().strip()
        if not text or len(text) > 1000:
            raise ValueError('発言は1〜1000文字です。')
        # 送信前に記録し、不確かな送信結果を自動再送して二重編集にしない。
        record['status'] = 'awaiting_reply'
        write(run / 'run.json', record)
        log(run, {'event': 'player_sent', 'text': text})
        started = time.monotonic()
        try:
            reply = api.speak(record['session_id'], text, base)
        except Exception as error:
            log(run, {'event': 'transport_error', 'error': str(error), 'auto_retry': False})
            raise
        log(run, {'event': 'dogido_reply', 'text': reply.get('turn', {}).get('text'),
                  'elapsed_seconds': time.monotonic() - started, 'response': reply})
        record['status'] = 'open' if reply.get('turn') and reply['turn'].get('playback_status') not in {'failed', 'cancelled'} else 'failed'
        write(run / 'run.json', record)
        write(run / 'last_dogido_request.json', api.call('last-prompt', {'session_id': record['session_id']}, base))
    value = snapshot(run, record, api, base)
    public = packet(run, record, value)
    if args.command == 'finish':
        summary = {'run_id': record['run_id'], 'original_poem': record['original_text'],
                   'final_poem': public['current_poem'], 'pending_poem': public['pending_poem'],
                   'changed': public['current_poem'] != record['original_text'],
                   'workshop_state': value['workshop']['state'],
                   'text_state': value.get('text_state'), 'ended_by': args.reason}
        summary['changed_lines'] = [i + 1 for i, (old, new) in enumerate(zip(record['original_text'].splitlines(), public['current_poem'].splitlines())) if old != new]
        write(run / 'summary.json', summary)
        log(run, {'event': 'finished', 'summary': summary})
        api.call('close', {'session_id': record['session_id']}, base)
        record.update(status='finished', finished_at=now())
        write(run / 'run.json', record)
    print(json.dumps(public, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--source-memory', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--design', type=Path, default=HERE / 'design.json')
    p.add_argument('--prompt', type=Path, default=HERE / 'player_prompt.txt')
    for name in ['launch', 'open', 'say', 'snapshot', 'finish']:
        p = commands.add_parser(name)
        p.add_argument('--run-dir', type=Path, required=True)
        p.add_argument('--port', type=int, default=5067)
        if name == 'launch':
            p.add_argument('--prompt-reviewed', action='store_true')
        if name == 'open':
            p.add_argument('--player-agent-id', required=True)
            p.add_argument('--player-model-reported', default='unreported')
        if name == 'say':
            p.add_argument('--text-file', type=Path, required=True)
        if name == 'finish':
            p.add_argument('--reason', choices=['player_done', 'max_turns', 'failure', 'operator_stop'], required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.source_memory, args.output, read(args.design), args.prompt.read_text())
        print(json.dumps({'output': str(args.output), 'cases': len(result['cases']), 'runs': len(result['ordered_runs']), 'status': result['status']}, ensure_ascii=False))
    elif args.command == 'launch':
        if not args.prompt_reviewed:
            parser.error('プレイヤー指示文の確認後に --prompt-reviewed を指定してください。')
        run = args.run_dir.resolve()
        record = read(run / 'run.json')
        if record['status'] != 'prepared' or args.port in (5055, 5057, 8080):
            parser.error('未実行の条件と実験専用ポートを指定してください。')
        if sha((run.parents[1] / 'player_prompt.txt').read_bytes()) != record['player_prompt_sha256']:
            parser.error('条件表を作った後にプレイヤー指示が変わっています。新しい実験を準備してください。')
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        from dogido_server.config import get_settings
        settings = get_settings()
        fixed = {'binary_sha256': sha((ROOT / 'dogido-rust/target/release/examples/workshop_text').read_bytes()),
                 'chat_model': settings.llm_chat_model or settings.llm_model or 'default_model',
                 'chat_endpoint_sha256': sha((settings.llm_chat_base_url or settings.llm_base_url or 'http://127.0.0.1:8080/v1').encode()),
                 'chat_max_tokens': settings.llm_chat_max_tokens or settings.llm_max_tokens,
                 'chat_timeout': settings.llm_chat_timeout_sec or settings.llm_timeout_sec,
                 'haiku_model': settings.llm_haiku_model or settings.llm_model,
                 'haiku_endpoint_sha256': sha((settings.llm_haiku_base_url or settings.llm_base_url or '').encode()),
                 'haiku_max_tokens': settings.llm_haiku_max_tokens or settings.llm_max_tokens,
                 'haiku_timeout': settings.llm_haiku_timeout_sec or settings.llm_timeout_sec,
                 'haiku_generation_strategy': settings.haiku_generation_strategy,
                 'haiku_max_regeneration_rounds': settings.haiku_max_regeneration_rounds}
        fixed_file(run.parents[1] / 'dogido_runtime_baseline.json', fixed)
        write(run / 'launch.json', {'at': now(), 'port': args.port, 'prompt_reviewed': True,
                                  'settings': fixed})
        os.execv(sys.executable, [sys.executable, str(ROOT / 'dogido-rust/scripts/launch_workshop_text.py'),
                                 '--settings-dir', str(ROOT), '--port', str(args.port),
                                 '--memory-dir', str(run / 'memory')])
    else:
        interact(args)


if __name__ == '__main__':
    main()
