"""ドギドのLAN接続。hostだけがLANで待受け、clientは既存のloopback窓口を提供する。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from functools import partial
import errno
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import queue
import signal
import sqlite3
import socket
import subprocess
import sys
import threading
import time

from discovery import Bonjour, BonjourError, PermissionDenied, lan_ipv4
import transport
import message_client
import shared_readings
from family_messages import MessageBox, TIMEOUT, Unavailable

STATE = Path.home() / "Library/Application Support/Dogido LAN"
PAIR_SECONDS = 120


def dialog(script, args):
    # Arguments are data, including user-visible service names supplied by DNS-SD.
    result = subprocess.run(["/usr/bin/osascript", "-e", script, *args],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        if "(-128)" in result.stderr:  # AppleScript user cancelled
            return ""
        raise RuntimeError("確認画面を表示できませんでした。")
    return result.stdout.strip()


def choose(prompt, labels):
    if not labels:
        return None
    answer = dialog("""
        on run argv
            set choices to items 2 thru -1 of argv
            set answer to choose from list choices with title "ドギド LAN" with prompt (item 1 of argv)
            if answer is false then return ""
            return item 1 of answer
        end run
    """, [prompt, *labels])
    return labels.index(answer) if answer in labels else None


def confirm(code):
    answer = dialog("""
        on run argv
            set r to display dialog ("もう一台にも同じ番号が出ていますか？" & return & return & item 1 of argv) with title "ドギドのペアリング" buttons {"取り消す", "番号が一致"} default button "取り消す" giving up after 90
            if gave up of r then return ""
            return button returned of r
        end run
    """, [code])
    return answer == "番号が一致"


@contextmanager
def serving(server):
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def lan_interfaces():
    """Wi-Fi・有線の全ポートを列挙する。既定ルートやインターネットの有無に依存しない。"""
    indices = {name: index for index, name in socket.if_nameindex() if name.startswith("en")}
    # 定期確認のたびに各ポートでプロセスを起動せず、OSのIPv4一覧を一度だけ受け取る。
    listing = subprocess.run(["/sbin/ifconfig", "-a", "inet"], text=True, capture_output=True, check=True)
    active = set()
    index = None
    for line in listing.stdout.splitlines():
        if line and not line[0].isspace():
            index = indices.get(line.partition(":")[0])
            continue
        fields = line.split()
        if index is not None and len(fields) >= 2 and fields[0] == "inet":
            ip = ipaddress.IPv4Address(fields[1])
            if lan_ipv4(ip):
                active.add((index, str(ip)))
                index = None
    return active


def gateway_module():
    # The installed host has a copy of the fixed-model HTTP handler in lan/.
    if not (Path(__file__).with_name("gateway.py")).is_file():
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "family-link"))
    import gateway
    return gateway


def read_controls(commands):
    try:
        for line in sys.stdin:
            commands.put((line.rstrip('\r\n'), time.time()))
    except (OSError, ValueError):
        return


def report_to_yuno(result):
    """共有画面の配送結果だけを同一ユーザーのユノへ渡す。停止中でも共有側の結果は確定する。"""
    if result.get("accepted") and not result.get("activity"):
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(str(STATE / "yuno/status.sock"))
            client.sendall(json.dumps(result).encode() + b"\n")
    except OSError:
        print("ユノに伝言の状態を通知できませんでした。", flush=True)


def manage(host, command, received=None):
    if command == "":
        selection = choose("端末の登録または解除を選んでください。", ["端末を追加", "登録を解除"])
        command = {0: "pair", 1: "revoke"}.get(selection, "")
    if command == "pair":
        host.open_pairing(PAIR_SECONDS)
        print("2分間、端末登録を受け付けます。遊ぶMacでドギドを起動してください。", flush=True)
    elif command.startswith("message "):
        try:
            if received is not None and time.time() - received >= TIMEOUT:
                raise ValueError("共有Macの接続を待つ間に送信期限を過ぎました。")
            message = json.loads(command[len("message "):])
            if isinstance(message, str):
                host.messages.enqueue(message)
            elif isinstance(message, dict) and set(message) == {"text", "sender"}:
                # 共有画面が送信時に確定した管理者名を、既存の送り主検査・保存・読み上げへ渡す。
                host.messages.enqueue(message["text"], sender=message["sender"])
            else:
                raise ValueError("メッセージの本文と送り主を確認してください。")
        except Unavailable as error:
            report_to_yuno({"accepted": False, "reason": error.reason})
            print(f"伝言を受け付けられませんでした。{error}", flush=True)
            return
        except (ValueError, sqlite3.Error) as error:
            print(f"伝言を受け付けられませんでした。{error}", flush=True)
            return
        print("伝言を預かりました。ゲームでの表示または再生を待っています。", flush=True)
    elif command == "recipient":
        devices = sorted(host.devices())
        if not devices:
            print("先に遊ぶMacを登録してください。", flush=True)
            return
        selected = choose("この画面とLINEの伝言を届けるMacの登録IDを選んでください。", devices)
        if selected is not None:
            try:
                host.messages.select(devices[selected])
            except ValueError as error:
                print(error, flush=True)
                return
            print(f"伝言の宛先を {devices[selected]} に設定しました。", flush=True)
    elif command == "revoke":
        devices = sorted(host.devices())
        if not devices:
            print("登録済みの端末はありません。", flush=True)
            return
        selected = choose("登録を解除する端末を選んでください。", devices)
        if selected is not None:
            host.revoke(devices[selected])
            print("選んだ端末の登録を解除しました。", flush=True)


@contextmanager
def advertised_port(discovery, listen, interface, preferred_port):
    index, address = interface
    try:
        server = listen((address, preferred_port))
    except OSError as error:
        if not preferred_port or error.errno != errno.EADDRINUSE:
            raise
        server = listen((address, 0))
    with serving(server), discovery.advertise(index, server.server_port, server.pin) as poll:
        yield server, poll


def serve_lan(discovery, listen, commands):
    # 各経路の待受けと広告を一緒に閉じる。一方の抜き差しでは他方の接続や端末登録を閉じない。
    active = {}
    next_check = 0
    try:
        while True:
            if time.monotonic() >= next_check:
                interfaces = lan_interfaces()
                if not interfaces and (active or next_check == 0):
                    print("LANへの接続を待っています。", flush=True)
                for entry in active.keys() - interfaces:
                    active.pop(entry)[0].close()
                for entry in sorted(interfaces - active.keys()):
                    existing = next(iter(active.values()), None)
                    port = existing[1].server_port if existing else 0
                    stack = ExitStack()
                    try:
                        server, poll = stack.enter_context(advertised_port(discovery, listen, entry, port))
                    except PermissionDenied:
                        raise
                    except OSError:
                        # 抜き差しの途中やBonjour復旧中はこの経路だけ次回再試行する。
                        continue
                    active[entry] = stack, server, poll
                    print("ドギド共有受付を開始しました。", flush=True)
                next_check = time.monotonic() + 3
            if active and not commands.empty():
                manage(next(iter(active.values()))[1], *commands.get())
            for entry, (stack, _, poll) in list(active.items()):
                try:
                    poll(0)
                except PermissionDenied:
                    raise
                except BonjourError:
                    active.pop(entry)
                    stack.close()
            time.sleep(0.2)
    finally:
        for stack, _, _ in active.values():
            stack.close()


@contextmanager
def background(target, *args):
    stop = threading.Event()
    worker = threading.Thread(target=target, args=(stop, *args), daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join()


def prepare_messages(stop, messages, render):
    while not stop.is_set():
        try:
            if messages.prepare_next(render):
                continue
        except (OSError, ValueError, sqlite3.Error):
            print("伝言の保管先を確認できません。少し待って再試行します。", flush=True)
        stop.wait(0.3)


def run_host(state):
    discovery = Bonjour()
    commands = queue.SimpleQueue()
    print("新しいMacを登録するときは「端末を追加」を押してください。", flush=True)
    threading.Thread(target=read_controls, args=(commands,), daemon=True).start()
    # ユノの受信部品と文面生成は共有アプリにだけ同梱する。
    from line_inbox import serve
    from message_wording import render_message
    gateway = gateway_module()
    display = gateway.DialogueDisplay()
    messages = MessageBox(state, lambda: (transport._read(state / "devices.json") or {}).keys(),
                          on_ui_result=report_to_yuno, on_event=display.show)
    debug = gateway.DebugLog(state / "logs", display=display)
    try:
        with serving(gateway.make_proxy(port=0, upstream_port=8080, event=debug.emit)) as proxy, \
                serve(state, messages.deliver_line), \
                background(prepare_messages, messages, partial(render_message, proxy.server_port)):
            listen = transport.host_listener(state, proxy.server_port, confirm, messages,
                                             shared_readings.parent_export())
            serve_lan(discovery, listen, commands)
    finally:
        debug.close()


class PeerRoute:
    """Only a successful pinned+authenticated probe selects the forwarding destination."""
    def __init__(self):
        self._address = None
        self.message = ""
        self._lock = threading.Lock()

    def probe(self, peers, credentials, authenticate):
        selected = None
        revoked = False
        message = "共有Macを待っています。"
        # Bonjourが一度見失っても、直前のアドレスで鍵と端末登録を確認できる間は使い続ける。
        # Wi-Fiと有線で同じアドレスの候補が重なった場合も一回だけ検査する。
        addresses = dict.fromkeys([self._address] if self._address else [])
        addresses.update((peer.address, None) for peer in peers if peer.pin == credentials["pin"])
        for address in addresses:
            try:
                authenticate(address, credentials)
            except transport.RevokedError:
                revoked = True
                message = "共有Macでこの端末の登録が解除されています。"
                continue
            except transport.IdentityError:
                message = "登録した相手を確認できません。共有側の登録を確認してください。"
                continue
            except OSError:
                continue
            selected = address
            message = "登録済みの共有Macに接続しました。"
            break
        with self._lock:
            self._address = selected
        if message != self.message:
            print(message, flush=True)
            self.message = message
        return selected is None and revoked

    def resolve(self):
        with self._lock:
            if self._address is None:
                raise OSError("共有Macを待っています。")
            return self._address


def discover_peers(discovery):
    try:
        return discovery.discover()
    except PermissionDenied:
        raise
    except BonjourError:
        return []


def wait_for_pair(discovery, state):
    print("同じLANのドギド共有Macを探しています。", flush=True)
    while True:
        peers = discover_peers(discovery)
        if peers:
            by_pin = {}
            for peer in peers:
                by_pin.setdefault(peer.pin, []).append(peer)
            choices = list(by_pin.values())
            labels = [f"{i + 1}. {group[0].name} （{group[0].address[0]}）" for i, group in enumerate(choices)]
            index = choose("初回登録する共有Macを選んでください。", labels)
            if index is None:
                raise RuntimeError("ペアリングを取り消しました。")
            address = transport.pairing_address(choices[index])
            if address is not None:
                return transport.pair(state, address, confirm)
            print("選んだ共有Macへの接続を待っています。", flush=True)
        time.sleep(2)


def run_client(state):
    discovery = Bonjour()
    while True:
        credentials = transport.load_pair(state) or wait_for_pair(discovery, state)
        replica = shared_readings.client_replica(credentials)
        route = PeerRoute()
        if 'device' in credentials:
            print(f"このMacの伝言用の登録ID: {credentials['device']}", flush=True)
        with serving(transport.make_client_relay(route.resolve, credentials, port=8080)), background(
                message_client.run, state, route.resolve, credentials), background(
                shared_readings.run, replica, route.resolve, credentials):
            while not route.probe(discover_peers(discovery), credentials, transport.probe):
                time.sleep(3)
        choice = choose("共有Macで登録が解除されました。登録し直すには共有側で「端末を追加」を押してください。",
                        ["終了する", "このMacの登録を消して再ペアリング"])
        if choice != 1:
            return
        transport.forget_pair(state)


@contextmanager
def role_lock(state):
    if state.is_symlink():
        raise ValueError("LAN端末設定の場所がリンクになっています。")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    fd = os.open(state / "active.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("同じ接続がすでに起動しています。") from error
        yield
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("client", "host"))
    parser.add_argument("--state", type=Path)
    parser.add_argument("--forget", action="store_true",
                        help="この遊ぶMacの登録だけを解除する")
    args = parser.parse_args(argv)
    state = args.state or STATE / args.role
    def interrupt(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGHUP, interrupt)
    with role_lock(state):
        if args.forget:
            if args.role != "client":
                parser.error("--forget は client のみ")
            transport.forget_pair(state)
        elif args.role == "host":
            run_host(state)
        else:
            run_client(state)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"接続を開始できませんでした: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
