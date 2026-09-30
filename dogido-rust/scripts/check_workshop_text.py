#!/usr/bin/env python3
"""Exercise the native text window, prompt changes and stdio MCP with a local fake model."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading

from workshop_text_client import call, speak
from check_workshop_runtime import step
from check_workshop_edits import edit

ROOT = Path(__file__).resolve().parents[2]


def main():
    seen = []
    proposal_text = 'じゃあ、のくさふゆむをくささむしに変更しよう！どう？'
    class Model(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            prompt = '\n'.join(m['content'] for m in body['messages'])
            question = re.search(r'今回のプレイヤー発話（会話理解用）: ([^\n]+)', prompt).group(1)
            reply = step(question, speech='静かな夜の草を思い浮かべた読みやな。')
            if question == proposal_text:
                reply = edit(question, index=2, replacement='くささむし', reference='のくさふゆむ', fragment='のくさふゆむ')
                reply['speech'] = '下五をその言葉に変えるんやな？'
                if '前の一手は実行していない' in prompt:
                    reply = step(question, 'ask', '下五をその言葉に変えるんやな？')
            if question in {'うん！変えて！', '変えないで！'}:
                evidence = question if 'evidenceは今回の発話から' in prompt or question == '変えないで！' else proposal_text
                reply = edit(evidence, index=2, replacement='くささむし', reference='のくさふゆむ', fragment='のくさふゆむ')
                reply['speech'] = 'うん、下五をくささむしに変えたで。'
                if '前の一手は実行していない' in prompt:
                    reply = step(question, 'respond', 'うん、まだ元の句のままにしとくな。')
            if question == '終了でいいよ':
                reply = step(question, 'close_workshop')
                reply['speech'] = 'うん、ここまでにしよか。また話そな。'
            response = {'id': 'test-reply', 'object': 'chat.completion', 'model': 'mock',
                        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(reply, ensure_ascii=False)}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130}}
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        def log_message(self, *_):
            pass

    with tempfile.TemporaryDirectory(prefix='dogido-text-check-') as folder:
        folder = Path(folder)
        source = folder / 'memory/sessions/test/long_term/haiku_entries.jsonl'
        source.parent.mkdir(parents=True)
        view = json.loads((ROOT / 'dogido-rust/scripts/fixtures/workshop_meaning_cases.json').read_text())['contexts']['grass']
        e = view['emission']
        original = json.dumps({'id': 'hk_20260930_014310_000000', 'kind': 'agent_haiku', 'author': 'dogido',
                              'created_at': '2026-09-30T01:43:10+00:00', 'text': e['reading_text'],
                              'surface_text': e['reading_text'], 'reading_text': e['reading_text'],
                              'lines': e['lines'], 'materials_snapshot': view['materials'],
                              'interpretation': e.get('interpretation'), 'preface': None, 'world': {}, 'trigger': {}}, ensure_ascii=False) + '\n'
        source.write_text(original)
        model = ThreadingHTTPServer(('127.0.0.1', 0), Model)
        thread = threading.Thread(target=model.serve_forever, daemon=True)
        thread.start()
        command = [str(ROOT / 'dogido-rust/target/release/examples/workshop_text'),
                   '--listen', '127.0.0.1:0', '--memory-dir', str(folder / 'memory'),
                   '--python', sys.executable, '--model', 'mock', '--base-url', f'http://127.0.0.1:{model.server_port}/v1',
                   '--prompt-file', str(folder / 'prompts.json')]
        with (folder / 'stderr.log').open('w') as error:
            process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=error, text=True)
            try:
                ready = process.stdout.readline()
                found = re.search(r'http://127.0.0.1:\d+', ready)
                assert found, (ready, (folder / 'stderr.log').read_text())
                base = found.group()
                poems = call('poems', base=base)
                assert len(poems) == 1
                sid = call('open', {'key': poems[0]['key']}, base)['session_id']
                stale_sid = call('open', {'key': poems[0]['key']}, base)['session_id']
                prompts = call('prompts', base=base)
                prompts['settings']['system'] += '\nテキスト検査の識別用指示。'
                result = call('prompts', {'settings': prompts['settings'], 'expected_version': 0}, base)
                assert result == {'version': 1, 'saved': True, 'warning': None}, result
                try:
                    call('prompts', {'settings': prompts['settings'], 'expected_version': 0}, base)
                    raise AssertionError('stale version accepted')
                except RuntimeError as exc:
                    assert '409' in str(exc)
                broken = dict(prompts['settings'], main=prompts['settings']['main'].replace('{{context}}', ''))
                try:
                    call('prompts', {'settings': broken, 'expected_version': 1}, base)
                    raise AssertionError('missing context accepted')
                except RuntimeError as exc:
                    assert '400' in str(exc)
                first = speak(sid, 'この句はどういう意味？', base)
                assert first['turn']['workshop_action'] == 'explain', first
                assert first['turn']['text_delivery_acknowledged']
                last = call('last-prompt', {'session_id': sid}, base)
                assert last['prompt_version'] == 1
                assert 'テキスト検査の識別用指示。' in last['request']['messages'][0]['content']
                assert last['request']['messages'] == seen[-1]['messages']
                second = speak(sid, 'その静かな夜ってどんな感じ？', base)
                assert second['turn']['text_delivery_acknowledged']
                assert '静かな夜の草を思い浮かべた読みやな。' in seen[-1]['messages'][1]['content']
                snapshot = call('snapshot', {'session_id': sid}, base)
                assert len(snapshot['dialogue']['sessions'][0]['workshop_history']) == 2
                assert all(r['playback_status'] == 'audio_disabled' and r['text_displayed'] for r in snapshot['dialogue']['utterances'])
                assert source.read_text() == original
                assert json.loads((folder / 'prompts.json').read_text()) == prompts['settings']

                async def mcp_check():
                    from mcp import ClientSession, StdioServerParameters
                    from mcp.client.stdio import stdio_client
                    parameters = StdioServerParameters(command=sys.executable,
                        args=[str(ROOT / 'dogido-rust/scripts/workshop_text_mcp.py')],
                        env={**os.environ, 'DOGIDO_WORKSHOP_URL': base})
                    async with stdio_client(parameters) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            listed = await session.list_tools()
                            assert len(listed.tools) == 10
                            result = await session.call_tool('list_poems', {})
                            assert not result.isError
                            result = await session.call_tool('inspect_last_prompt', {'session_id': sid})
                            assert not result.isError
                            assert 'テキスト検査の識別用指示。' in str(result)
                asyncio.run(mcp_check())
                discussed = speak(sid, proposal_text, base)
                assert discussed['turn']['workshop_action'] == 'ask', discussed
                assert discussed['workshop']['canonical_lines'][-1] == 'のくさふゆむ'
                handoff = folder / 'conversation.json'
                handoff.write_text(json.dumps([{'key': poems[0]['key'], 'session_id': sid,
                    'snapshot': call('snapshot', {'session_id': sid}, base)}], ensure_ascii=False))
                call('shutdown', {}, base)
                process.wait(timeout=10)
                process = subprocess.Popen(command + ['--resume', str(handoff)], cwd=ROOT,
                    stdout=subprocess.PIPE, stderr=error, text=True)
                ready = process.stdout.readline()
                found = re.search(r'http://127.0.0.1:\d+', ready)
                assert found, (ready, (folder / 'stderr.log').read_text())
                base = found.group()
                stale_sid = call('open', {'key': poems[0]['key']}, base)['session_id']
                refused = speak(sid, '変えないで！', base)
                assert refused['workshop']['canonical_lines'][-1] == 'のくさふゆむ', refused
                changed = speak(sid, 'うん！変えて！', base)
                assert changed['turn']['workshop_outcome'] == 'player_edit_saved', changed
                assert changed['turn']['text_delivery_acknowledged'], changed
                assert changed['workshop']['canonical_lines'][-1] == 'くささむし', changed
                assert source.read_text() == original
                revision_file = source.with_name('haiku_revisions.jsonl')
                saved_rows = [json.loads(line) for line in revision_file.read_text().splitlines()]
                assert len(saved_rows) == 1 and saved_rows[0]['haiku_id'] == poems[0]['id']
                assert saved_rows[0]['revised_text'].splitlines()[-1] == 'くささむし'
                assert call('poems', base=base)[0]['text'].splitlines()[-1] == 'くささむし'
                assert call('poems', base=base)[0]['original_text'].splitlines()[-1] == 'のくさふゆむ'
                speak(stale_sid, proposal_text, base)
                stale = speak(stale_sid, 'うん！変えて！', base)
                assert stale['turn']['workshop_outcome'] == 'pending_save_failed', stale
                assert stale['turn']['workshop_reason'] == 'saved_poem_changed', stale
                assert stale['workshop']['canonical_lines'][-1] == 'のくさふゆむ', stale
                assert stale['workshop']['pending_lines'][-1] == 'くささむし', stale
                assert len(revision_file.read_text().splitlines()) == 1
                close = speak(sid, '終了でいいよ', base)
                assert close['turn']['workshop_action'] == 'close_workshop', close
                assert close['turn']['text_delivery_acknowledged'], close
                closed = call('snapshot', {'session_id': sid}, base)
                assert closed['workshop']['state'] == 'closed'
                assert closed['dialogue']['utterances'][-1]['playback_status'] == 'audio_disabled'
                assert closed['dialogue']['utterances'][-1]['text_displayed']
                handoff.write_text(json.dumps([{'key': poems[0]['key'], 'session_id': sid, 'snapshot': closed}, {'key': poems[0]['key'], 'session_id': stale_sid, 'snapshot': call('snapshot', {'session_id': stale_sid}, base)}], ensure_ascii=False))
                call('shutdown', {}, base)
                process.wait(timeout=10)
                process = subprocess.Popen(command + ['--resume', str(handoff)], cwd=ROOT,
                    stdout=subprocess.PIPE, stderr=error, text=True)
                ready = process.stdout.readline()
                found = re.search(r'http://127.0.0.1:\d+', ready)
                assert found, (ready, (folder / 'stderr.log').read_text())
                base = found.group()
                restored = call('snapshot', {'session_id': sid}, base)
                assert restored['workshop']['state'] == 'closed'
                assert restored['text_state']['lines'][-1]['reading_text'] == 'くささむし'
                restored_pending = call('snapshot', {'session_id': stale_sid}, base)
                assert restored_pending['workshop']['canonical_lines'][-1] == 'のくさふゆむ'
                assert restored_pending['workshop']['pending_lines'][-1] == 'くささむし'
                assert len(restored['dialogue']['utterances']) == len(closed['dialogue']['utterances'])
                reopened = call('open', {'key': poems[0]['key']}, base)['session_id']
                current = call('snapshot', {'session_id': reopened}, base)
                assert current['workshop']['canonical_lines'][-1] == 'くささむし'
                assert current['text_state']['revision_id'] == saved_rows[0]['id']
                assert source.read_text() == original
                assert len(revision_file.read_text().splitlines()) == 1
                call('shutdown', {}, base)
                process.wait(timeout=10)
                assert process.returncode == 0
                print(json.dumps({'passed': ['native_workshop_reply', 'displayed_reply_history',
                    'saved_source_unchanged', 'prompt_applied_and_exact_request_visible',
                    'prompt_persistence', 'stale_version_rejected', 'missing_context_rejected',
                    'stdio_mcp_ten_tools_and_readback', 'question_retains_player_candidate', 'restore_recovers_discussed_candidate_without_editing',
                    'short_refusal_keeps_original', 'confirmed_revision_saved_once', 'poem_book_reads_latest', 'stale_window_preserves_pending',
                    'closed_conversation_restored', 'unsaved_pending_restored', 'adopted_poem_reopened_after_restart',
                    'model_close_display_acknowledged', 'owned_server_shutdown']}, ensure_ascii=False, indent=2))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                model.shutdown()
                model.server_close()


if __name__ == '__main__':
    main()
