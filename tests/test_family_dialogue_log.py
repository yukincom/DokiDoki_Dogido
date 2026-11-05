"""見込みは既存snapshotの投影で対話だけを表示できること。合格条件は各ケースに記す。
模擬snapshot・一時DB・loopback HTTPを使用し、実ゲームの発話品質や実LANは扱わない。
"""
import importlib.util
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('dialogue_test_' + name, ROOT / 'scripts/family-link' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


relay = load('log_relay')
gateway = load('gateway')


def row(turn, **fields):
    return dict(session_id='ses_test', turn_id=turn, source='typed',
                created_at='2026-11-05T03:00:00Z') | fields


# 見込みはASR原文の専用IDが再振分け・叫声を一回にまとめること。
# 合格は同文でも別input_idを二回表示し、voice由来の重複行・生成中本文は出さない。模擬snapshot。
def test_voice_original_and_confirmed_reply_are_shown_once():
    projector = relay.DialogueRows()
    snapshot = {'sessions': [{'session_id': 'ses_test', 'voice_inputs': [
        {'input_id': 'a', 'text': 'こんにちは'}, {'input_id': 'b', 'text': 'こんにちは'}]}],
        'utterances': [row('r', source='voice')]}
    snapshot['utterances'] = [dict(row('r'), source='voice', player_input_text='こんにちは', text='こんにちはやで')]
    assert [json.loads(line)['text'] for line in projector.take(snapshot)] == ['こんにちは', 'こんにちは']
    snapshot['utterances'][0].update(delivery_confirmed=True, playback_status='failed')
    assert [json.loads(line)['text'] for line in projector.take(snapshot)] == ['こんにちはやで']
    assert projector.take(snapshot) == []


# 見込みは受付と転送先のID照合で、BERT処理中のsnapshotでもtyped入力が重複しないこと。
# 合格は親と子の間で取得しても一回、同文の独立した次入力は追加される。本文一致は一時保留だけ。
def test_typed_forwarding_race_and_same_text_new_turn():
    projector = relay.DialogueRows()
    parent = row('request', category='routing', playback_status='not_selected', player_input_text='何が見える？')
    child = row('turn', category='speech', player_input_text='何が見える？')
    snapshot = {'utterances': [parent, child]}
    assert len(projector.take(snapshot)) == 1
    parent['forwarded_input'] = {'turn_id': 'turn'}
    assert projector.take(snapshot) == []
    snapshot['utterances'].append(row('next', category='speech', player_input_text='何が見える？'))
    assert len(projector.take(snapshot)) == 1


# 見込みは警告へ複写されたplayer_inputを除くと匂い照会等の元入力が増えないこと。
# 合格はIDのない転送結果でも元入力一回・確定返答一回。模擬snapshotの警告経路を確認する。
def test_copied_warning_input_is_not_a_second_user_turn():
    snapshot = {'utterances': [
        row('request', category='routing', player_input_text='何のにおい？', forwarded_input={'accepted': True}),
        row('warning', source='player_input', player_input_text='何のにおい？', text='花のにおいや。', delivery_confirmed=True)]}
    assert [json.loads(line)['speaker'] for line in relay.DialogueRows().take(snapshot)] == ['子供', 'ドギド']


# 見込みは再生確認を共通条件にして自発発話・WS・句を表示できること。
# 合格はカテゴリを問わず確定文だけ、取消済み未確認文は出さず、明示errorは一度。長文JSONは分割可能。
def test_spontaneous_workshop_and_long_text():
    projector = relay.DialogueRows()
    rows = [row(kind, category=kind, text=kind, delivery_confirmed=True)
            for kind in ('speech', 'callout', 'haiku', 'workshop')]
    rows.append(row('cancelled', text='未配送', playback_status='cancelled'))
    rows.append(row('failed', error='音声合成に失敗しました。'))
    long = '句の本文\\"\n' * 900
    rows.append(row('long', text=long, delivery_confirmed=True))
    output = projector.take({'utterances': rows})
    parsed = [json.loads(line) for line in output]
    assert [r['text'] for r in parsed[:5]] == ['speech', 'callout', 'haiku', 'workshop', '音声合成に失敗しました。']
    assert ''.join(r['text'] for r in parsed[5:]) == long
    assert all(len(line) < 8192 for line in output)
    assert projector.take({'utterances': rows}) == []


# 見込みは共有側の表示選択を保存から分離すると、画面の通信ノイズだけを除けること。
# 合格は対話再送を一回、エラーを表示し、全診断行を一時JSONLへ保存。制御文字は画面へ流れない。
def test_host_filters_technical_lines_and_deduplicates_retries(tmp_path, capsys):
    display = gateway.DialogueDisplay()
    log = gateway.DebugLog(tmp_path, display=display)
    line = json.dumps({'id': 's:t:assistant', 'speaker': 'ドギド', 'text': '元気やで。\x1b[31m',
                       'time': '2026-11-05T12:00:00+09:00'})
    log.emit('クライアント/server', 'event="voice_input"')
    log.emit('クライアント/minecraft', 'Dogido status_snapshot OK')
    log.emit('モデル', '生成開始')
    log.emit('クライアント/voice', '[VOICE] {"level":"info","reason":"aec_levels"}')
    log.emit('クライアント/dialogue', line)
    log.emit('クライアント/dialogue', line)
    log.emit('クライアント/server', '[ERROR] 接続エラー')
    log.emit('クライアント/voice', '[VOICE] {"level":"error","detail":"マイクがありません"}')
    log.emit('クライアント/voice', '[本体終了] voice / exit=1')
    log.emit('クライアント/server', '[本体終了] server / exit=0')
    log.close()
    output = capsys.readouterr().out
    assert output.count('元気やで。') == 1
    assert '接続エラー' in output and 'マイクがありません' in output
    assert 'status_snapshot' not in output and 'aec_levels' not in output and '生成開始' not in output
    assert 'exit=1' in output and 'exit=0' not in output
    assert '\x1b' not in output and len(output.splitlines()) == 4
    assert len(log.path.read_text().splitlines()) == 10


# 見込みは既存GETと既存ログPOSTだけで対話を運べること。
# 合格は模擬本体へのGET認証と202受付、時刻付き表示を確認。ACK・モデル要求・正本保存は実行しない。
def test_snapshot_to_existing_log_endpoint(tmp_path, capsys):
    requests = []
    class Snapshot(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            requests.append((self.path, self.headers.get('Authorization')))
            body = json.dumps({'utterances': [row('t', text='こんにちは。', delivery_confirmed=True)]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    log = gateway.DebugLog(tmp_path, display=gateway.DialogueDisplay())
    source = ThreadingHTTPServer(('127.0.0.1', 0), Snapshot)
    receiver = gateway.make_proxy(port=0, upstream_port=1, event=log.emit)
    servers = [source, receiver]
    workers = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for worker in workers:
        worker.start()
    sender = relay.Relay('dialogue', port=receiver.server_port)
    stop = threading.Event()
    def emit(line):
        sender.put(line)
        stop.set()
    try:
        relay.relay_dialogue(stop, f'http://127.0.0.1:{source.server_port}', 'test-auth', emit)
        sender.close()
    finally:
        for server, worker in zip(servers, workers):
            server.shutdown()
            server.server_close()
            worker.join()
        log.close()
    assert requests == [('/api/v1/rust-dialogue/snapshot', 'Bearer test-auth')]
    assert 'ドギド　こんにちは。' in capsys.readouterr().out
