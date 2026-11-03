"""macOS Bonjour DNS-SD. Advertisements are discovery hints, never credentials."""
from __future__ import annotations

import ctypes as C
from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import select
import socket
import struct
import time

SERVICE = b"_dogido._tcp"
DOMAIN = b"local."
MAX_SERVICES = 12
Ref = C.c_void_p
U32, I32, U16, Chars = C.c_uint32, C.c_int32, C.c_uint16, C.c_char_p
RegisterCB = C.CFUNCTYPE(None, Ref, U32, I32, Chars, Chars, Chars, Ref)
BrowseCB = C.CFUNCTYPE(None, Ref, U32, U32, I32, Chars, Chars, Chars, Ref)
ResolveCB = C.CFUNCTYPE(None, Ref, U32, U32, I32, Chars, Chars, U16, U16, Ref, Ref)
AddrCB = C.CFUNCTYPE(None, Ref, U32, U32, I32, Chars, Ref, U32, Ref)


class BonjourError(OSError):
    pass


class PermissionDenied(BonjourError):
    pass


def check(code):
    if code:
        if code in (-65570, -65571):
            raise PermissionDenied("Bonjourが許可されていません。macOSのローカルネットワーク設定を確認してください。")
        raise BonjourError(f"Bonjourが利用できません（{code}）。ネットワーク設定を確認してください。")


def library():
    # DNSService* is exported by libSystem on macOS (no standalone libdns_sd).
    lib = C.CDLL("/usr/lib/libSystem.B.dylib")
    signatures = {
        "DNSServiceRegister": [C.POINTER(Ref), U32, U32, Chars, Chars, Chars, Chars, U16, U16, Ref, RegisterCB, Ref],
        "DNSServiceBrowse": [C.POINTER(Ref), U32, U32, Chars, Chars, BrowseCB, Ref],
        "DNSServiceResolve": [C.POINTER(Ref), U32, U32, Chars, Chars, Chars, ResolveCB, Ref],
        "DNSServiceGetAddrInfo": [C.POINTER(Ref), U32, U32, U32, Chars, AddrCB, Ref],
        "DNSServiceRefSockFD": [Ref],
        "DNSServiceProcessResult": [Ref],
        "DNSServiceRefDeallocate": [Ref],
    }
    for name, args in signatures.items():
        func = getattr(lib, name)
        func.argtypes = args
        func.restype = None if name == "DNSServiceRefDeallocate" else I32
    return lib


@contextmanager
def operation(lib, function, *args):
    ref = Ref()
    check(getattr(lib, function)(C.byref(ref), *args))
    try:
        yield ref
    finally:
        lib.DNSServiceRefDeallocate(ref)


def process(lib, ref, timeout):
    fd = lib.DNSServiceRefSockFD(ref)
    if fd < 0:
        raise BonjourError("Bonjourの接続が終了しました。")
    if not select.select([fd], [], [], max(timeout, 0))[0]:
        return False
    check(lib.DNSServiceProcessResult(ref))
    return True


def txt_pin(data):
    """DNS-SD TXT is a sequence of length-prefixed entries."""
    while data:
        length, data = data[0], data[1:]
        if length > len(data):
            return ""
        field, data = data[:length], data[length:]
        if field.startswith(b"pin=") and len(field) == 68:
            pin = field[4:].decode("ascii", errors="replace")
            if all(c in "0123456789abcdef" for c in pin):
                return pin
    return ""


@dataclass(frozen=True)
class Peer:
    name: str
    address: tuple[str, int]
    pin: str


class Bonjour:
    def __init__(self):
        self.lib = library()

    @contextmanager
    def advertise(self, interface, port, pin):
        field = f"pin={pin}".encode("ascii")
        txt = bytes([len(field)]) + field
        errors = []
        callback = RegisterCB(lambda _r, _f, err, *_: errors.append(err))
        with operation(self.lib, "DNSServiceRegister", 0, interface,
                       "ドギド共有Mac".encode(), SERVICE, DOMAIN, None,
                       socket.htons(port), len(txt), C.cast(C.c_char_p(txt), Ref), callback, None) as ref:
            def poll(timeout):
                process(self.lib, ref, timeout)
                if errors:
                    check(errors.pop(0))
            yield poll

    def discover(self, timeout=1.6):
        found, errors = {}, []

        def changed(_ref, flags, iface, err, name, kind, domain, _ctx):
            if err:
                errors.append(err)
                return
            key = (iface, name, kind, domain)
            if flags & 2:  # kDNSServiceFlagsAdd
                if len(found) < MAX_SERVICES:
                    found[key] = None
            else:
                found.pop(key, None)

        callback = BrowseCB(changed)
        deadline = time.monotonic() + timeout
        with operation(self.lib, "DNSServiceBrowse", 0, 0, SERVICE, DOMAIN, callback, None) as ref:
            while time.monotonic() < deadline and process(self.lib, ref, deadline - time.monotonic()):
                if errors:
                    check(errors[0])
        peers = []
        lookup_deadline = time.monotonic() + 3
        for service in found:
            if time.monotonic() >= lookup_deadline:
                break
            peers.extend(self._resolve(service, lookup_deadline))
        return peers

    def _resolve(self, service, deadline):
        iface, name, kind, domain = service
        results = []

        def resolved(_ref, _flags, interface, err, _full, host, port, length, data, _ctx):
            if not err:
                pin = txt_pin(C.string_at(data, length))
                if pin:
                    results.append((interface, host, socket.ntohs(port), pin))

        callback = ResolveCB(resolved)
        with operation(self.lib, "DNSServiceResolve", 0, iface, name, kind, domain, callback, None) as ref:
            process(self.lib, ref, min(1.0, deadline - time.monotonic()))
        peers = []
        for interface, host, port, pin in results:
            def address(_ref, flags, _if, err, _host, sockaddr, _ttl, _ctx):
                if err or not (flags & 2) or not sockaddr:
                    return
                # Darwin sockaddr_in starts with sin_len, sin_family, sin_port.
                length, family = struct.unpack("BB", C.string_at(sockaddr, 2))
                if length >= 8 and family == socket.AF_INET:
                    ip = socket.inet_ntoa(C.string_at(sockaddr, 8)[4:8])
                    addr = ipaddress.IPv4Address(ip)
                    if addr.is_private and not (addr.is_loopback or addr.is_link_local or addr.is_unspecified):
                        peers.append(Peer(name.decode("utf-8", "replace"), (ip, port), pin))

            callback_addr = AddrCB(address)
            with operation(self.lib, "DNSServiceGetAddrInfo", 0, interface, 1, host, callback_addr, None) as ref:
                process(self.lib, ref, min(1.0, deadline - time.monotonic()))
        return peers
