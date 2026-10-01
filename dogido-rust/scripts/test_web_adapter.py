"""Web用stdioの休眠・明示終了をデバイス/ネットワークなしで検証する。"""
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
from queue import Queue
import threading
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("dogido_web_adapter", Path(__file__).with_name("web_adapter.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class Input:
    def __init__(self): self.queue = Queue()
    def readline(self, limit): return self.queue.get(timeout=3)
    def send(self, **frame): self.queue.put(json.dumps(frame) + "\n")
    def eof(self): self.queue.put("")
class Output:
    def __init__(self): self.queue = Queue()
    def write(self, text): self.queue.put(json.loads(text))
    def flush(self): pass
    def receive(self): return self.queue.get(timeout=3)
@dataclass
class Result:
    status: str = "page_opened"
    child_status: str = "opened"
    search_url: str = "https://www.google.com/search?q=fixture"
class Provider:
    def __init__(self, blocked=False):
        self.client = SimpleNamespace(close=self.close)
        self.called = threading.Event()
        self.closed = False
        self.blocked = blocked
    def close(self): self.closed = True
    def search(self, target, terms, facet, **kw):
        self.called.set()
        if self.blocked:
            for _ in range(500):
                if kw['cancelled'](): return Result(status="interrupted")
                threading.Event().wait(.001)
            raise AssertionError("cancel was not delivered")
        assert target == "狐"
        return Result()

def launch(provider):
    input, output, factories = Input(), Output(), []
    def factory():
        factories.append(True)
        return provider, SimpleNamespace(available=True, reason="ready")
    @dataclass
    class Availability:
        available: bool = True
        reason: str = "ready"
    worker=threading.Thread(target=module.serve,args=(input,output),kwargs={"factory":factory,"inspect":Availability})
    worker.start()
    return input, output, factories, worker

def proposal():
    return dict(question="狐の語源",target="狐",facet="etymology",search_terms=["狐"],web_query="狐の語源",trigger_reason="explicit_request",known_urls=[])

def test_inspection_is_dormant_search_is_once_and_session_close_reaps():
    provider=Provider()
    input, output, factories, worker=launch(provider)
    try:
        input.send(op="inspect",request_id="inspect")
        assert output.receive()["availability"] == dict(available=True,reason="ready")
        assert factories == [] and not provider.called.is_set()
        input.send(op="search",request_id="search",proposal=proposal())
        result=output.receive()
        assert result["result"]["status"] == "page_opened"
        assert factories == [True]
        # 検索終了後もsessionが閉じるまでclientを維持する。
        assert not provider.closed
    finally:
        input.send(op="close",request_id="close")
        worker.join(timeout=3)
    assert not worker.is_alive() and provider.closed

def test_eof_cancels_inflight_search_then_closes_owned_client():
    provider=Provider(blocked=True)
    input, output, factories, worker=launch(provider)
    input.send(op="search",request_id="search",proposal=proposal())
    assert provider.called.wait(timeout=3)
    input.eof()
    worker.join(timeout=3)
    assert not worker.is_alive() and provider.closed
    assert output.queue.empty()
