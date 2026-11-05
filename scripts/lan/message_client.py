"""遊ぶMacが登録先をポーリングし、同じ起動回のRustが確認した表示・再生結果を返す。"""
import hashlib
import http.client
import ipaddress
import json
import os
import re
import time
from urllib.parse import urlsplit

import transport
from family_messages import GAME_REASONS, TIMEOUT, check_activity, check_text


def local_request(url, key, auth, message=None):
    endpoint = urlsplit(url)
    host = endpoint.hostname
    if endpoint.scheme != 'http' or not host or not (
            host == 'localhost' or ipaddress.ip_address(host).is_loopback):
        raise ValueError('伝言にはこのMacの本体を指定してください。')
    conn = http.client.HTTPConnection(host, endpoint.port, timeout=3)
    headers = {'Content-Type': 'application/json', 'X-Dogido-Family-Key': key}
    if auth:
        headers['Authorization'] = 'Bearer ' + auth
    try:
        body = json.dumps(message) if message is not None else None
        conn.request('POST' if body else 'GET', '/api/v1/family-message', body, headers)
        response = conn.getresponse()
        if response.status != 200:
            raise OSError('遊ぶMacのドギド本体の状態を確認できません。')
        data = json.loads(response.read(4096))
        if not isinstance(data, dict):
            raise ValueError('ドギド本体の応答を確認できません。')
        return data
    finally:
        conn.close()


def poll_once(state, address, credentials, player, generation, local):
    try:
        readiness = local()
        reason = None if readiness.get('ready') is True else readiness.get('reason', 'server_stopping')
        if reason is not None and reason not in GAME_REASONS:
            reason = 'server_stopping'
    except (TimeoutError, ConnectionResetError, http.client.RemoteDisconnected):
        # 状態確認の応答が途切れただけでは本体停止を確定できない。伝言を受けたら同じ本体のPOSTで照合する。
        reason = None
    except (ValueError, OSError, http.client.HTTPException):
        reason = 'server_stopping'
    data = transport.message_request(address, credentials, transport.NEXT_MESSAGE,
                                     {'reason': reason, 'player': player})
    message = data['message']
    if message is None:
        return
    if (not isinstance(message, dict) or set(message) != {'id', 'text'}
            or not isinstance(message['id'], str) or not re.fullmatch('[a-f0-9]{32}', message['id'])):
        raise ValueError('伝言の形式を確認してください。')
    check_text(message['text'], 5200)
    mid = message['id']
    # 送信前にIDを保存。同じ本体には同一IDで再照会でき、再起動後は不明な結果を再発話にしない。
    path = state / 'family-receipt.json'
    receipts = transport._read(path) or {}
    now = time.time()
    if 'id' in receipts:  # 更新前は直前の一件だけを保存していた。
        receipts = {receipts['id']: dict(receipts, saved=now)}
    # 結果返信が切れた記録も、共有側の提示期限の間は全件を保持して再生を防ぐ。
    receipts = {mid: item for mid, item in receipts.items() if now - item['saved'] <= TIMEOUT * 2}
    previous = receipts.get(mid)
    mark = {'id': mid, 'device': credentials['device'], 'pin': credentials['pin']}
    same = previous and all(previous.get(k) == v for k, v in mark.items())
    activity = []
    if same and previous.get('result'):
        status = previous['result']
        activity = previous.get('activity', [])
    elif same and previous['generation'] != generation:
        status = 'delivery_unknown'
    else:
        mark.update(generation=generation, saved=now)
        if not same:
            receipts[mid] = mark
            transport._save(path, receipts)
        try:
            reply = local(message)
        except ConnectionRefusedError:
            # 初回の接続拒否ならまだ本体へ送っていない。先の受付可否が不明なら未達と断定できない。
            reply = {'accepted': False, 'reason': 'delivery_unknown' if same else 'server_stopping'}
        if reply.get('accepted') is not True:
            status = reply.get('reason')
            if status not in GAME_REASONS:
                status = 'delivery_unknown'
        elif reply.get('status') == 'queued':
            return not same
        else:
            status = {'delivered': 'delivered', 'failed': 'delivery_failed'}.get(reply.get('status'), 'delivery_unknown')
            if status == 'delivered':
                activity = check_activity(reply.get('activity', []))
    receipts[mid] = dict(mark, result=status, activity=activity, saved=now)
    transport._save(path, receipts)
    result = {'id': mid, 'status': status}
    if activity:
        result['activity'] = activity
    transport.message_request(address, credentials, transport.MESSAGE_RESULT, result)
    del receipts[mid]
    if receipts:
        transport._save(path, receipts)
    else:
        path.unlink(missing_ok=True)
    return status != 'game_busy'


def run(stop, state, resolve, credentials):
    url = os.environ.get('DOGIDO_FAMILY_LOCAL_URL')
    key = os.environ.get('DOGIDO_FAMILY_SESSION_KEY')
    if not url or not key:
        return
    auth = os.environ.get('DOGIDO_AUTH_TOKEN')
    player = os.environ.get('DOGIDO_FAMILY_PLAYER', '')
    generation = hashlib.sha256(key.encode()).hexdigest()

    def local(message=None):
        return local_request(url, key, auth, message)

    while not stop.is_set():
        try:
            if poll_once(state, resolve(), credentials, player, generation, local):
                continue
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            pass
        stop.wait(1.5)
