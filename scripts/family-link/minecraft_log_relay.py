"""Tail only Dogido-owned Minecraft log records and relay them without blocking play."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
import uuid


HEADER = re.compile(r'^\[\d{2}:\d{2}:\d{2}\] \[[^\]]+/(?:TRACE|DEBUG|INFO|WARN|ERROR)\]')
FABRIC_MESSAGE = re.compile(
    r'^\[\d{2}:\d{2}:\d{2}\] \[[^\]]+/(?:TRACE|DEBUG|INFO|WARN|ERROR)\]:\s*(?P<message>.*)$'
)
DOGIDO_LOGGER = re.compile(
    r'\]\s+(?:\((?:dogido(?:[-_.][A-Za-z0-9_-]+)?)\)|\[(?:dogido(?:[-_.][A-Za-z0-9_-]+)?)\])\s+',
    re.IGNORECASE,
)
STACK_CONTINUATION = re.compile(
    r'^(?:\s+at\s|\s*Caused by:|\s*Suppressed:|\s*\.\.\. \d+ more|'
    r'[A-Za-z_$][A-Za-z0-9_$.]*(?:Exception|Error|Throwable)(?::|$))'
)
DOGIDO_MESSAGE = re.compile(
    r'^(?:Dogido\s|Body view\s|Poem body view\s|Capture retention\s|Temporary image cleanup\s|'
    r'Capture readiness\s|Image target sampling\s|Body capture\s|Unpublished capture cleanup\s|'
    r'Body sensory capture\s|Body [A-Za-z0-9_.-]+ observation incomplete\s|'
    r'Could not load character placement\b|Character placement changed for this session\b|'
    r'villager profession unresolved\b)',
    re.IGNORECASE,
)


class DogidoLogFilter:
    """Keep a Dogido logger record and only its immediately following stack lines."""
    def __init__(self):
        self.keep_continuation = False

    def accepts(self, line: str) -> bool:
        if HEADER.match(line):
            message = FABRIC_MESSAGE.match(line)
            self.keep_continuation = (
                DOGIDO_LOGGER.search(line) is not None
                or (message is not None and DOGIDO_MESSAGE.match(message.group('message')) is not None)
            )
            return self.keep_continuation
        if line.startswith('['):
            # A new record in an unknown format must never inherit the prior
            # Dogido stack decision.
            self.keep_continuation = False
            return False
        return self.keep_continuation and STACK_CONTINUATION.match(line) is not None


def _same_file(path: Path, opened) -> bool:
    try:
        return os.fstat(opened.fileno()).st_ino == path.stat().st_ino
    except (FileNotFoundError, OSError):
        return False


def follow(path: Path, emit, stopped: threading.Event, *, poll_seconds: float = 0.25) -> None:
    """Follow latest.log across creation and rotation; never replay a pre-existing file."""
    opened = None
    filter_ = DogidoLogFilter()
    skip_existing = path.exists()
    try:
        while not stopped.is_set():
            if opened is None:
                try:
                    opened = path.open('r', encoding='utf-8', errors='replace')
                    if skip_existing:
                        opened.seek(0, os.SEEK_END)
                        skip_existing = False
                    filter_ = DogidoLogFilter()
                except FileNotFoundError:
                    stopped.wait(poll_seconds)
                    continue
            line = opened.readline()
            if line:
                value = line.rstrip('\r\n')
                if filter_.accepts(value):
                    emit(value)
                continue
            try:
                truncated = path.stat().st_size < opened.tell()
            except (FileNotFoundError, OSError):
                truncated = True
            if truncated or not _same_file(path, opened):
                opened.close()
                opened = None
                skip_existing = False
                continue
            stopped.wait(poll_seconds)
    finally:
        if opened is not None:
            opened.close()


def run(root: Path) -> int:
    sys.path.insert(0, str(root / 'client_tools'))
    from log_relay import LOG_RETENTION_SECONDS, Relay, managed_log, prune_logs

    source = root / 'minecraft/logs/latest.log'
    log_dir = root / 'logs/family-client'
    log_dir.mkdir(parents=True, exist_ok=True)
    prune_logs(log_dir, '.log')
    target = log_dir / (datetime.now().strftime('%Y%m%d-%H%M%S-minecraft-')
                        + uuid.uuid4().hex[:6] + '.log')
    stopped = threading.Event()
    relay = Relay('minecraft')
    next_maintenance = time.monotonic() + LOG_RETENTION_SECONDS

    def stop(*_):
        stopped.set()

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)

    with managed_log(target) as local:
        local_failed = False
        def emit(line: str) -> None:
            nonlocal local_failed, next_maintenance
            if time.monotonic() >= next_maintenance:
                prune_logs(log_dir, '.log', exclude=(target,))
                next_maintenance = time.monotonic() + LOG_RETENTION_SECONDS
            print(line, flush=True)
            if not local_failed:
                try:
                    local.write(line + '\n')
                    local.flush()
                except OSError:
                    local_failed = True
                    failure = '[ログ保存] Minecraft限定ログを保存できません。画面表示とLAN転送は継続します。'
                    print(failure, flush=True)
                    relay.put(failure)
            relay.put(line)

        print('[Minecraftログ] Dogidoが付けた行だけを待っています。録音や全latest.logは送りません。', flush=True)
        try:
            follow(source, emit, stopped)
        finally:
            relay.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run(Path(os.environ.get('DOGIDO_CLIENT_KIT', Path(__file__).resolve().parents[1]))))
    except (OSError, ValueError) as error:
        raise SystemExit(f'Minecraftログを確認できません: {error}') from error
