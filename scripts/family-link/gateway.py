"""Foreground LAN gateway for Dogido's fixed local model. Never edits macOS settings."""
from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import contextmanager
from datetime import datetime
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import sys
import threading
import time
import zipfile
import uuid

SSH_PORT = 22080
PROXY_PORT = 22081
MAX_LOG_BODY = 1024 * 1024
MAX_CHAT_BODY = 2 * 1024 * 1024
MAX_JSON_RESPONSE = 2 * 1024 * 1024
MAX_STREAM_RESPONSE = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 256 * 1024
MAX_IMAGE_BASE64 = (MAX_IMAGE_BYTES + 2) // 3 * 4
LOG_RETENTION_SECONDS = 24 * 60 * 60
MAX_ACTIVE_GENERATIONS = 2
MAX_WAITING_GENERATIONS = 6
GENERATION_WAIT_SECONDS = 180
MAX_REQUEST_THREADS = 16
NEW_STATE_NAME = 'Dogido-LAN-Server'
LEGACY_STATE_NAME = 'Dogido-Qwen-Link'


def default_state(home=None):
    """Keep an existing host installation's keys; use the generic name for new installs."""
    application_support = (Path.home() if home is None else Path(home)) / 'Library/Application Support'
    current = application_support / NEW_STATE_NAME
    legacy = application_support / LEGACY_STATE_NAME
    if current.exists() or not legacy.exists():
        return current
    return legacy


STATE = default_state()
TOOL_PARAMETERS = {
    'inspect_verse': {
        'type': 'object',
        'properties': {
            'checks': {'type': 'array', 'items': {'type': 'string', 'enum': ['reading', 'source']},
                       'minItems': 1, 'maxItems': 2, 'uniqueItems': True},
            'inspect_text': {'type': 'string', 'minLength': 1, 'maxLength': 160},
            'inspect_line_index': {'type': 'integer', 'minimum': 0, 'maximum': 2},
        },
        'required': ['checks'],
        'additionalProperties': False,
    },
    'stage_player_edit': {
        'type': 'object',
        'properties': {
            'replacement_text': {
                'type': 'string',
                'description': '変更後の一行全体。部分変更でも残す言葉を含め、行IDや他の二行は含めない。元の行と同じかどうかはコードが比較する。',
            },
            'evidence': {'type': 'string', 'description': '今回の変更依頼を解釈した根拠。記録用。'},
            'confidence': {'type': 'number', 'description': '任意の自己申告値。記録用。'},
        },
        'required': ['replacement_text'],
        'additionalProperties': False,
    },
}


def log_text(value):
    # Child log text is data, never terminal control sequences or paths to write.
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(value))
    return ''.join(char if char >= ' ' and not '\x7f' <= char <= '\x9f' else ' '
                   for char in value)


def prune_logs(directory, suffix, *, now=None, exclude=()):
    """Delete only expired regular files owned by this gateway's log directory."""
    now = time.time() if now is None else now
    excluded = {Path(path) for path in exclude}
    if directory.is_symlink() or not directory.is_dir():
        return 0
    removed = 0
    for path in directory.glob('*' + suffix):
        if path in excluded or path.is_symlink():
            continue
        try:
            stat = path.stat()
            if path.is_file() and now - stat.st_mtime > LOG_RETENTION_SECONDS:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            pass
    return removed


class DebugLog:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        prune_logs(directory, '.jsonl')
        self.path = directory / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6] + '.jsonl')
        self.file = os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')
        self.lock = threading.Lock()
        self.next_maintenance = time.monotonic() + LOG_RETENTION_SECONDS

    def maintain(self):
        if time.monotonic() < self.next_maintenance:
            return
        prune_logs(self.path.parent, '.jsonl', exclude=(self.path,))
        self.next_maintenance = time.monotonic() + LOG_RETENTION_SECONDS

    def emit(self, source, message, **details):
        self.maintain()
        record = {'time': datetime.now().astimezone().isoformat(timespec='milliseconds'),
                  'source': source, 'message': log_text(message), **details}
        with self.lock:
            suffix = ' '.join(f'{key}={log_text(value)}' for key, value in details.items())
            try:
                print(f"[{record['time'][11:23]}][{source}] {record['message']} {suffix}".rstrip(), flush=True)
            except OSError:
                pass  # A closing Terminal must not prevent shutdown.
            if self.file.closed:
                return
            try:
                self.file.write(json.dumps(record, ensure_ascii=False) + '\n')
                self.file.flush()
            except OSError:
                try:
                    self.file.close()
                except OSError:
                    pass
                try:
                    print('ログ保存に失敗しました。ターミナル表示のみ継続します。', flush=True)
                except OSError:
                    pass

    def close(self):
        with self.lock:
            try:
                self.file.close()
            except OSError:
                pass


def checked_logs(body):
    if not isinstance(body, dict) or set(body) != {'source', 'lines'}:
        raise ValueError('Invalid log payload')
    if body['source'] not in ('server', 'voice', 'minecraft'):
        raise ValueError('Invalid log source')
    lines = body['lines']
    if (not isinstance(lines, list) or not 1 <= len(lines) <= 32
            or any(not isinstance(line, str) or len(line) > 8192 for line in lines)):
        raise ValueError('Invalid log lines')
    return body['source'], lines


def checked_image_url(value):
    if not isinstance(value, str) or not value.startswith('data:image/png;base64,'):
        raise ValueError('Only inline PNG images are permitted')
    encoded = value.removeprefix('data:image/png;base64,')
    if not encoded or len(encoded) > MAX_IMAGE_BASE64:
        raise ValueError('Invalid image size')
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError('Invalid PNG encoding') from error
    if (len(payload) > MAX_IMAGE_BYTES or len(payload) < 33
            or payload[:8] != b'\x89PNG\r\n\x1a\n'
            or payload[8:12] != (13).to_bytes(4, 'big') or payload[12:16] != b'IHDR'):
        raise ValueError('Invalid PNG image')
    width = int.from_bytes(payload[16:20], 'big')
    height = int.from_bytes(payload[20:24], 'big')
    if not 1 <= width <= 192 or not 1 <= height <= 192:
        raise ValueError('PNG dimensions exceed Dogido input')
    return value


def checked_tools(value):
    if value is None:
        return [], set()
    if not isinstance(value, list) or not 1 <= len(value) <= len(TOOL_PARAMETERS):
        raise ValueError('Invalid tools')
    result, names = [], set()
    for tool in value:
        if not isinstance(tool, dict) or set(tool) != {'type', 'function'} or tool['type'] != 'function':
            raise ValueError('Invalid tool definition')
        function = tool['function']
        if not isinstance(function, dict) or set(function) != {'name', 'description', 'parameters'}:
            raise ValueError('Invalid tool function')
        name = function['name']
        description = function['description']
        if (name not in TOOL_PARAMETERS or name in names or not isinstance(description, str)
                or not 1 <= len(description) <= 4096
                or function['parameters'] != TOOL_PARAMETERS[name]):
            raise ValueError('Unsupported tool definition')
        names.add(name)
        result.append({'type': 'function', 'function': {
            'name': name, 'description': description, 'parameters': TOOL_PARAMETERS[name]}})
    return result, names


def checked_tool_calls(value, allowed_tools):
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError('One known tool call is required')
    calls = []
    for call in value:
        if not isinstance(call, dict) or set(call) != {'id', 'type', 'function'} or call['type'] != 'function':
            raise ValueError('Invalid tool call')
        function = call['function']
        if not isinstance(function, dict) or set(function) != {'name', 'arguments'}:
            raise ValueError('Invalid tool call function')
        call_id, name, arguments = call['id'], function['name'], function['arguments']
        if (not isinstance(call_id, str) or not 1 <= len(call_id) <= 128
                or name not in allowed_tools or not isinstance(arguments, str) or len(arguments) > 32768):
            raise ValueError('Unsupported tool call')
        calls.append({'id': call_id, 'type': 'function',
                      'function': {'name': name, 'arguments': arguments}})
    return calls


def checked_messages(messages, allowed_tools):
    if not isinstance(messages, list) or not messages:
        raise ValueError('messages required')
    result, image_count = [], 0
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get('role'), str):
            raise ValueError('Invalid message')
        role = message['role']
        if role in {'system', 'developer'}:
            if set(message) != {'role', 'content'} or not isinstance(message['content'], str):
                raise ValueError('Invalid instruction message')
            result.append({'role': role, 'content': message['content']})
        elif role == 'user':
            if set(message) != {'role', 'content'}:
                raise ValueError('Invalid user message')
            content = message['content']
            if isinstance(content, str):
                result.append({'role': role, 'content': content})
                continue
            if not isinstance(content, list) or not 1 <= len(content) <= 16:
                raise ValueError('Invalid multimodal content')
            parts = []
            for part in content:
                if not isinstance(part, dict) or part.get('type') not in {'text', 'image_url'}:
                    raise ValueError('Invalid message part')
                if part['type'] == 'text':
                    if set(part) != {'type', 'text'} or not isinstance(part['text'], str):
                        raise ValueError('Invalid text part')
                    parts.append({'type': 'text', 'text': part['text']})
                else:
                    if set(part) != {'type', 'image_url'} or not isinstance(part['image_url'], dict) or set(part['image_url']) != {'url'}:
                        raise ValueError('Invalid image part')
                    image_count += 1
                    if image_count > 2:
                        raise ValueError('Too many images')
                    parts.append({'type': 'image_url', 'image_url': {
                        'url': checked_image_url(part['image_url']['url'])}})
            result.append({'role': role, 'content': parts})
        elif role == 'assistant':
            if set(message) == {'role', 'content'} and isinstance(message['content'], str):
                result.append({'role': role, 'content': message['content']})
            elif set(message) == {'role', 'content', 'tool_calls'} and message['content'] in (None, ''):
                result.append({'role': role, 'content': message['content'],
                               'tool_calls': checked_tool_calls(message['tool_calls'], allowed_tools)})
            else:
                raise ValueError('Invalid assistant message')
        elif role == 'tool':
            if set(message) != {'role', 'tool_call_id', 'name', 'content'}:
                raise ValueError('Invalid tool result')
            if (message['name'] not in allowed_tools or not isinstance(message['tool_call_id'], str)
                    or not 1 <= len(message['tool_call_id']) <= 128 or not isinstance(message['content'], str)
                    or len(message['content']) > 131072):
                raise ValueError('Unsupported tool result')
            result.append({key: message[key] for key in ('role', 'tool_call_id', 'name', 'content')})
        else:
            raise ValueError('Unsupported message role')
    return result


def checked_payload(body):
    if not isinstance(body, dict):
        raise ValueError('JSON object required')
    allowed = {'model', 'messages', 'temperature', 'max_tokens', 'stream', 'stream_options',
               'chat_template_kwargs', 'tools', 'tool_choice'}
    if set(body) - allowed:
        raise ValueError('Unsupported request fields')
    if body.get('model', 'default_model') != 'default_model':
        raise ValueError('Model switching is not permitted')
    stream = body.get('stream', False)
    if type(stream) is not bool:
        raise ValueError('Invalid stream setting')
    if stream:
        if body.get('stream_options') != {'include_usage': True}:
            raise ValueError('Usage-bearing stream required')
    elif 'stream_options' in body:
        raise ValueError('Unexpected stream options')
    if body.get('chat_template_kwargs', {}) not in ({}, {'enable_thinking': False}):
        raise ValueError('Custom templates are not permitted')
    tools, tool_names = checked_tools(body.get('tools'))
    tool_choice = body.get('tool_choice')
    if tools:
        if tool_choice not in {'auto', 'none'}:
            raise ValueError('Known tool choice required')
    elif tool_choice is not None:
        raise ValueError('Tool choice without tools')
    messages = checked_messages(body.get('messages'), tool_names)
    tokens = body.get('max_tokens')
    if type(tokens) is not int or not 1 <= tokens <= 8192:
        raise ValueError('Positive max_tokens required')
    temperature = body.get('temperature', 0.7)
    if (type(temperature) not in (int, float) or not math.isfinite(temperature)
            or not 0 <= temperature <= 2):
        raise ValueError('Invalid temperature')
    # Reconstruct instead of forwarding arbitrary model-loader or template options.
    result = {'model': 'default_model', 'draft_model': None, 'adapters': None,
            'messages': messages, 'max_tokens': tokens, 'temperature': temperature,
            'stream': stream, 'chat_template_kwargs': {'enable_thinking': False}}
    if stream:
        result['stream_options'] = {'include_usage': True}
    if tools:
        result['tools'] = tools
        result['tool_choice'] = tool_choice
    return result


class SseFilter:
    """Rewrite only model identifiers and observe usage without retaining generated text."""
    def __init__(self):
        self.buffer = b''
        self.usage = {}
        self.done = False

    def _line(self, line):
        ending = b''
        if line.endswith(b'\n'):
            ending, line = b'\n', line[:-1]
            if line.endswith(b'\r'):
                ending, line = b'\r\n', line[:-1]
        if not line.startswith(b'data:'):
            return line + ending
        payload = line[5:].strip()
        if payload == b'[DONE]':
            if self.done:
                raise ValueError('Duplicate upstream SSE terminator')
            self.done = True
            # Hold the terminal marker until upstream EOF. This preserves
            # streaming text while preventing a later duplicate/data record
            # from being presented to Rust as a successful completion.
            return b''
        if self.done:
            raise ValueError('Upstream SSE data after terminator')
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError('Invalid upstream SSE') from error
        if not isinstance(value, dict):
            raise ValueError('Invalid upstream SSE object')
        if 'model' in value:
            value['model'] = 'default_model'
        if isinstance(value.get('usage'), dict):
            self.usage = value['usage']
        return b'data: ' + json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode() + ending

    def feed(self, chunk):
        self.buffer += chunk
        if len(self.buffer) > 1024 * 1024 and b'\n' not in self.buffer:
            raise ValueError('Oversized upstream SSE line')
        output = bytearray()
        while b'\n' in self.buffer:
            index = self.buffer.index(b'\n') + 1
            output.extend(self._line(self.buffer[:index]))
            self.buffer = self.buffer[index:]
        return bytes(output)

    def finish(self):
        output = b''
        if self.buffer:
            output, self.buffer = self._line(self.buffer), b''
        if not self.done:
            raise ValueError('Upstream SSE ended before terminator')
        return output + b'data: [DONE]\n\n'


class GenerationBusy(Exception):
    pass


class GenerationGate:
    """Bound active model calls and the number of callers allowed to wait."""
    def __init__(self, active, waiting, wait_seconds):
        if active < 1 or waiting < 0:
            raise ValueError('Invalid generation limits')
        self.capacity = threading.BoundedSemaphore(active + waiting)
        self.active = threading.BoundedSemaphore(active)
        self.wait_seconds = wait_seconds

    @contextmanager
    def claim(self):
        if not self.capacity.acquire(blocking=False):
            raise GenerationBusy('Generation queue is full')
        acquired = False
        try:
            acquired = self.active.acquire(timeout=self.wait_seconds)
            if not acquired:
                raise GenerationBusy('Generation wait timed out')
            yield
        finally:
            if acquired:
                self.active.release()
            self.capacity.release()


class LimitedThreadingHTTPServer(ThreadingHTTPServer):
    """Cap slow/incomplete HTTP connections before they create unbounded threads."""
    daemon_threads = True

    def __init__(self, server_address, handler, max_threads):
        self._request_slots = threading.BoundedSemaphore(max_threads)
        super().__init__(server_address, handler)

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def make_proxy(port=PROXY_PORT, upstream_port=8080, event=None, *,
               max_active_generations=MAX_ACTIVE_GENERATIONS,
               max_waiting_generations=MAX_WAITING_GENERATIONS,
               generation_wait_seconds=GENERATION_WAIT_SECONDS,
               max_request_threads=MAX_REQUEST_THREADS):
    event = event or (lambda *args, **kwargs: None)
    generation_gate = GenerationGate(
        max_active_generations, max_waiting_generations, generation_wait_seconds)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass  # No conversation text, headers, or credentials in logs.

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def respond(self, code, payload):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def upstream_response(self, method, path, body=None):
            # Fixed loopback upstream; no redirect following or caller-selected URLs.
            connection = http.client.HTTPConnection('127.0.0.1', upstream_port, timeout=180)
            try:
                data = None if body is None else json.dumps(body).encode()
                connection.request(method, path, body=data,
                                   headers={'Content-Type': 'application/json'})
                response = connection.getresponse()
                return connection, response
            except Exception:
                connection.close()
                raise

        def upstream_json(self, method, path, body=None):
            connection, response = self.upstream_response(method, path, body)
            try:
                raw = response.read(MAX_JSON_RESPONSE + 1)
                if response.status != 200 or len(raw) > MAX_JSON_RESPONSE:
                    raise ValueError('Upstream response rejected')
                return json.loads(raw)
            finally:
                connection.close()

        def upstream_stream(self, body):
            connection, response = self.upstream_response('POST', '/v1/chat/completions', body)
            stream_filter = SseFilter()
            total = 0
            try:
                content_type = response.getheader('Content-Type', '')
                if response.status != 200 or not content_type.lower().startswith('text/event-stream'):
                    response.read(MAX_JSON_RESPONSE + 1)
                    raise ValueError('Upstream stream rejected')
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'close')
                self.end_headers()
                self._dogido_stream_started = True
                while chunk := response.read1(64 * 1024):
                    total += len(chunk)
                    if total > MAX_STREAM_RESPONSE:
                        raise ValueError('Upstream stream exceeds response limit')
                    filtered = stream_filter.feed(chunk)
                    if filtered:
                        self.wfile.write(filtered)
                        self.wfile.flush()
                final = stream_filter.finish()
                if final:
                    self.wfile.write(final)
                    self.wfile.flush()
                return stream_filter.usage
            finally:
                connection.close()

        def do_GET(self):
            if self.path != '/v1/models':
                self.respond(404, {'error': 'Endpoint not available'})
                return
            try:
                result = self.upstream_json('GET', '/v1/models')
                if not isinstance(result, dict) or not isinstance(result.get('data'), list):
                    raise ValueError('Unexpected upstream')
                self.respond(200, {'object': 'list', 'data': [
                    {'id': 'default_model', 'object': 'model', 'owned_by': 'dogido'}]})
            except (OSError, ValueError, http.client.HTTPException):
                self.respond(502, {'error': 'LANサーバーPCのモデルが応答していません'})

        def do_POST(self):
            if self.path not in ('/v1/chat/completions', '/dogido/client-logs'):
                self.respond(404, {'error': 'Endpoint not available'})
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                content_type = self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                if (self.headers.get('Transfer-Encoding') or len(lengths) != 1
                        or content_type != 'application/json'):
                    raise ValueError('Content-Length required')
                size = int(lengths[0])
                limit = MAX_LOG_BODY if self.path == '/dogido/client-logs' else MAX_CHAT_BODY
                if not 0 < size <= limit:
                    raise ValueError('Request too large or empty')
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('Incomplete request')
                decoded = json.loads(raw)
                if self.path == '/dogido/client-logs':
                    source, lines = checked_logs(decoded)
                    for line in lines:
                        event('クライアント/' + source, line)
                    self.respond(202, {'accepted': len(lines)})
                    return
                body = checked_payload(decoded)
            except (ValueError, OSError, RecursionError):
                event('接続', '許可されていないリクエストを拒否', endpoint=self.path)
                self.respond(400, {'error': '許可されていないリクエストです'})
                return
            try:
                with generation_gate.claim():
                    request_id = uuid.uuid4().hex[:8]
                    started = time.monotonic()
                    event('モデル', '生成開始', request_id=request_id,
                          max_tokens=body['max_tokens'], stream=body['stream'])
                    self._dogido_stream_started = False
                    try:
                        if body['stream']:
                            usage = self.upstream_stream(body)
                        else:
                            result = self.upstream_json('POST', '/v1/chat/completions', body)
                            if not isinstance(result, dict) or not isinstance(result.get('choices'), list):
                                raise ValueError('Unexpected completion')
                            result['model'] = 'default_model'
                            usage = result.get('usage', {})
                            self.respond(200, result)
                        event('モデル', '生成完了', request_id=request_id,
                              elapsed_ms=round((time.monotonic() - started) * 1000), usage=usage)
                    except (BrokenPipeError, ConnectionResetError):
                        event('モデル', 'クライアント切断', request_id=request_id,
                              elapsed_ms=round((time.monotonic() - started) * 1000))
                    except (OSError, ValueError, http.client.HTTPException):
                        event('モデル', '生成失敗', request_id=request_id,
                              elapsed_ms=round((time.monotonic() - started) * 1000))
                        if not self._dogido_stream_started:
                            self.respond(502, {'error': 'LANサーバーPCのモデル生成に失敗しました'})
            except GenerationBusy:
                event('モデル', '混雑のため受付拒否')
                self.respond(429, {'error': '生成の待ち行列が満員です。少し待ってからもう一度お試しください'})

    server = LimitedThreadingHTTPServer(('127.0.0.1', port), Handler, max_request_threads)
    return server


def ssh_config(state, address, user, port=SSH_PORT, proxy_port=PROXY_PORT):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', user):
        raise ValueError('Unexpected local account name')
    ipaddress.IPv4Address(address)
    if any(c in str(state) for c in '\n\r"'):
        raise ValueError('Unsupported state path')
    return f'''# Dogido dedicated endpoint. Not /etc/ssh/sshd_config.
ListenAddress {address}
AddressFamily inet
Port {port}
HostKey "{state / 'host_ed25519'}"
AuthorizedKeysFile "{state / 'authorized_keys'}"
PidFile "{state / 'sshd.pid'}"
UsePAM no
StrictModes yes
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
HostbasedAuthentication no
GSSAPIAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
AllowUsers {user}
MaxSessions 0
AllowTcpForwarding local
PermitOpen 127.0.0.1:{proxy_port}
PermitListen none
AllowStreamLocalForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitTTY no
PermitTunnel no
PermitUserRC no
GatewayPorts no
LoginGraceTime 30
ClientAliveInterval 15
ClientAliveCountMax 3
LogLevel VERBOSE
'''


def lan_address():
    for interface in ('en1', 'en0', 'en2', 'en3'):
        result = subprocess.run(['/usr/sbin/ipconfig', 'getifaddr', interface],
                                text=True, capture_output=True)
        try:
            address = ipaddress.IPv4Address(result.stdout.strip())
        except ValueError:
            continue
        if address.is_private and not address.is_loopback and not address.is_link_local:
            return str(address)
    raise RuntimeError('家庭内LANのIPv4アドレスが見つかりません。Wi-Fi接続を確認してください。')


def key_pair(path):
    if not path.exists():
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                        '-C', 'dogido-lan-model-only', '-f', str(path)], check=True)
    path.chmod(0o600)
    public = subprocess.check_output(['/usr/bin/ssh-keygen', '-y', '-f', str(path)], text=True).strip()
    return public


def finalized_client_manifest(entries):
    """Verify bundled static updater files and add hashes for generated link files."""
    link_names = {'link/identity', 'link/known_hosts', 'link/connection.json'}
    try:
        manifest = json.loads(entries['manifest.json'])
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError('接続更新のmanifestが壊れています') from error
    static_names = set(entries) - link_names - {'manifest.json'}
    if not isinstance(manifest, dict) or set(manifest) != static_names:
        raise RuntimeError('接続更新のmanifestが壊れています')
    for name in static_names:
        digest = hashlib.sha256(entries[name]).hexdigest()
        if manifest[name] != digest:
            raise RuntimeError(f'接続更新のhashが一致しません: {name}')
    for name in link_names:
        if name not in entries:
            raise RuntimeError(f'接続更新が不足しています: {name}')
        manifest[name] = hashlib.sha256(entries[name]).hexdigest()
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def prepare(state, bundle_dir, address):
    if state.is_symlink():
        raise RuntimeError('状態保存先がリンクになっています。停止しました。')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    user = pwd.getpwuid(os.getuid()).pw_name
    host_public = key_pair(state / 'host_ed25519')
    client_public = key_pair(state / 'client_ed25519')
    authorized = state / 'authorized_keys'
    # Server settings are authoritative; the key additionally rejects sessions if reused here.
    authorized.write_text('restrict,port-forwarding,command="/usr/bin/false",'
                          f'permitopen="127.0.0.1:{PROXY_PORT}" {client_public}\n')
    authorized.chmod(0o600)
    config = state / 'sshd_config'
    config.write_text(ssh_config(state, address, user))
    config.chmod(0o600)
    subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(config)], check=True)
    effective = subprocess.check_output(['/usr/sbin/sshd', '-G', '-f', str(config)], text=True)
    for required in ('maxsessions 0', 'passwordauthentication no', 'usepam no',
                     'allowtcpforwarding local', f'permitopen 127.0.0.1:{PROXY_PORT}',
                     'allowstreamlocalforwarding no', 'permitlisten none'):
        if required not in effective.splitlines():
            raise RuntimeError('専用SSHの制限検査に失敗: ' + required)
    package = bundle_dir / '息子Macへ渡す.zip'
    temporary = package.with_suffix('.zip.tmp')
    config_data = {'host': address, 'port': SSH_PORT, 'user': user,
                   'remote_port': PROXY_PORT, 'local_port': 8080}
    entries = {
        'link/connection.json': json.dumps(config_data, indent=2).encode(),
        'link/identity': (state / 'client_ed25519').read_bytes(),
        'link/known_hosts': f'[{address}]:{SSH_PORT} {host_public}\n'.encode(),
    }
    for source in (bundle_dir / 'client-package').rglob('*'):
        if source.is_file():
            entries[source.relative_to(bundle_dir / 'client-package').as_posix()] = source.read_bytes()
    entries['manifest.json'] = finalized_client_manifest(entries)
    old_mask = os.umask(0o077)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo('Dogido-接続更新/' + name)
                info.create_system = 3
                info.external_attr = (0o100700 if name.endswith('.command') else 0o100600) << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        os.replace(temporary, package)
        package.chmod(0o600)
    finally:
        os.umask(old_mask)
    return config, package


def process_snapshot():
    return {int(pid): (int(parent), birth, state, int(session))
            for line in subprocess.check_output(
                ['/bin/ps', '-axo', 'pid=,ppid=,sess=,stat=,lstart='], text=True).splitlines()
            for pid, parent, session, state, birth in [line.split(maxsplit=4)]}


def signal_owned(owned, sig):
    current = process_snapshot()
    for pid, birth in owned.items():
        if pid not in current or current[pid][1] != birth:
            continue
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def track_process_tree(root_pid, owned):
    """Retain PID birth identities while descendants still have traceable parents."""
    snapshot = process_snapshot()
    root_is_original = False
    if root_pid in snapshot:
        root_birth = snapshot[root_pid][1]
        if root_pid not in owned:
            owned[root_pid] = root_birth
            root_is_original = True
        else:
            root_is_original = owned[root_pid] == root_birth
    # The dedicated listener is a session leader. OpenSSH forwarding children
    # may change process groups or become reparented, but normally retain this
    # session identity, so recover them even after the listener has exited.
    session_is_original = root_is_original or any(
        pid in snapshot and snapshot[pid][1] == birth and snapshot[pid][3] == root_pid
        for pid, birth in owned.items()
    )
    if session_is_original:
        for pid, (_, birth, _, session) in snapshot.items():
            if session == root_pid:
                # The original session proves ownership even when a former
                # SSH child PID has been reused by a newer child in that same
                # still-live dedicated session.
                owned[pid] = birth
    while True:
        children = {pid for pid, (parent, birth, _, _) in snapshot.items()
                    if (parent in owned and parent in snapshot
                        and snapshot[parent][1] == owned[parent]
                        and owned.get(pid) != birth)}
        if not children:
            return owned
        for pid in children:
            owned[pid] = snapshot[pid][1]


def stop_sshd(process, known_owned=None, grace=5):
    if process is None:
        return
    # OpenSSH forwarding children can create their own process groups. Freeze
    # this dedicated listener and its descendants before terminating the tree;
    # killing only the listener's group leaves authenticated connections alive.
    owned = dict(known_owned or {})
    track_process_tree(process.pid, owned)
    if process.poll() is None:
        # Freeze every identity already observed, then discover again. Once the
        # listener is frozen it cannot fork a child between the final snapshot
        # and termination.
        while True:
            before = dict(owned)
            signal_owned(owned, signal.SIGSTOP)
            track_process_tree(process.pid, owned)
            # A same-session PID may have been refreshed after the first STOP;
            # freeze that current identity before deciding the tree is stable.
            signal_owned(owned, signal.SIGSTOP)
            if owned == before:
                break
    # `known_owned` also covers the unexpected-exit path, where poll() has
    # already reaped the listener and its forwarding child is reparented.
    signal_owned(owned, signal.SIGTERM)
    signal_owned(owned, signal.SIGCONT)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        process.poll()
        current = process_snapshot()
        if not any(pid in current and current[pid][1] == birth and 'Z' not in current[pid][2]
                   for pid, birth in owned.items()):
            break
        time.sleep(0.05)
    # The listener may already have exited while an authenticated forwarding
    # child remains reparented. Recheck every captured identity independently.
    signal_owned(owned, signal.SIGKILL)
    signal_owned(owned, signal.SIGCONT)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        snapshot = process_snapshot()
        if process.pid in snapshot:
            signal_owned({process.pid: snapshot[process.pid][1]}, signal.SIGKILL)
        process.wait(timeout=2)
    verify_deadline = time.monotonic() + 2
    while True:
        current = process_snapshot()
        remaining = {pid for pid, birth in owned.items()
                     if pid in current and current[pid][1] == birth and 'Z' not in current[pid][2]}
        if not remaining:
            break
        if time.monotonic() >= verify_deadline:
            raise RuntimeError('専用SSHの子接続を停止できませんでした')
        time.sleep(0.05)


def run(state, bundle_dir):
    if os.geteuid() == 0:
        raise RuntimeError('sudoは不要です。通常のユーザーで開いてください。')
    address = lan_address()
    proxy = None
    daemon = None
    worker = None
    debug = None
    ssh_reader = None
    sshd_owned = {}
    if state.is_symlink():
        raise RuntimeError('状態保存先がリンクになっています。停止しました。')
    lock = state / 'running.lock'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    import fcntl
    with lock.open('a+') as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('ドギド専用接続は既に起動中です。先に開いた画面を使ってください。')
        config, package = prepare(state, bundle_dir, address)
        try:
            debug = DebugLog(state / 'logs')
            proxy = make_proxy(event=debug.emit)
            worker = threading.Thread(target=proxy.serve_forever, daemon=True)
            worker.start()
            daemon = subprocess.Popen(['/usr/sbin/sshd', '-D', '-e', '-f', str(config)],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, errors='replace', start_new_session=True)
            def read_ssh():
                for line in daemon.stdout:
                    debug.emit('SSH', line.rstrip())
            ssh_reader = threading.Thread(target=read_ssh, daemon=True)
            ssh_reader.start()
            startup_deadline = time.monotonic() + 0.5
            while time.monotonic() < startup_deadline:
                track_process_tree(daemon.pid, sshd_owned)
                if daemon.poll() is not None:
                    raise RuntimeError('専用接続口を開始できません。上のSSHログを確認してください。')
                time.sleep(0.05)
            print('\nドギド専用接続を開始しました。', flush=True)
            print(f'接続口: {address}:{SSH_PORT} / 会話とデバッグログ専用。ファイル操作・モデル変更は禁止。')
            print(f'LANサーバーPCのログ保存先: {debug.path}')
            print(f'息子MacへAirDropするファイル: {package}')
            print('息子MacでZIPを展開し「息子Macで接続を設定.command」を開いてください。')
            print('この画面は開いたままにします。終了は Ctrl+C。共有モデル本体は止めません。', flush=True)
            while True:
                track_process_tree(daemon.pid, sshd_owned)
                if daemon.poll() is not None:
                    break
                time.sleep(0.25)
            raise RuntimeError('専用接続が終了しました。上のSSHログを確認してください。')
        finally:
            stop_sshd(daemon, sshd_owned)
            if proxy is not None:
                if worker is not None and worker.is_alive():
                    proxy.shutdown()
                proxy.server_close()
            if ssh_reader is not None:
                ssh_reader.join(timeout=1)
            if debug is not None:
                debug.emit('接続', '専用接続を停止しました')
                debug.close()
            print('\nドギド専用接続を停止しました。', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=STATE)
    args = parser.parse_args()
    print('LANサーバーPC用: 専用鍵を作り、固定の共有モデルだけをクライアントPCへ渡します。')
    print('sudo・OSアカウント追加・既存SSH/ファイル共有設定の変更はありません。')
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        run(args.state.absolute(), Path(__file__).resolve().parent)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'開始できませんでした: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
