"""模擬モデルで、宛名付きの指示、長めの返答、失敗時の原文配送を確認する。意味の保全と口調は実モデルで別に確かめる。"""
import http.client
from io import BytesIO
import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lan"))
from message_wording import render_message


@pytest.fixture
def model(monkeypatch):
    class Fake:
        def __init__(self):
            self.sent = []
            self.status = 200
            self.body = b""
            self.closed = False

        def request(self, method, path, body, headers):
            self.sent.append((method, path, json.loads(body), headers))

        def getresponse(self):
            if isinstance(self.body, Exception):
                raise self.body
            response = BytesIO(self.body)
            response.status = self.status
            return response

        def close(self):
            self.closed = True

    fake = Fake()

    def connect(host, port, timeout):
        assert (host, port) == ("127.0.0.1", 19286) and 0 < timeout <= 15
        return fake

    monkeypatch.setattr(http.client, "HTTPConnection", connect)
    return fake


def completion(content, reason="stop", **fields):
    message = {"role": "assistant", "content": content, **fields}
    return json.dumps({"choices": [{"finish_reason": reason, "message": message}]}).encode()


# 見込み: 呼称入り指示と伝言本文が別々に届けば、通常会話や操作へ混ぜず読み上げ文を作れる。
# 合格: 模擬の送信先と要求全文が一致し、40字を超える応答も切らず返す。呼び名は設定から注入。実モデルの解釈は別確認。
@pytest.mark.parametrize(("configured", "expected"), [("テスター", "テスター"), ("仮の呼び名", "仮の呼び名")])
def test_uses_approved_instruction_and_returns_complete_wording(model, configured, expected):
    source = "18時半までに来てね。"
    spoken = "おかあさんが、18時半までに来てほしいって言っとるで。" + "あと少しで時間やから、ぼちぼち準備しよか。"
    model.body = completion(spoken, reasoning_content="テスト用の内部文")
    assert render_message(19286, "おかあさん", configured, source) == "おかあさんからの伝言やで。" + spoken
    assert model.sent == [("POST", "/v1/chat/completions", {
        "model": "default_model",
        "messages": [
            {"role": "system", "content": f"おかあさんから{expected}へ伝言や！４０字くらいで{expected}に伝えてや。メッセージの内容の意味は変えたらあかんで！"},
            {"role": "user", "content": source},
        ],
        "max_tokens": 512, "stream": False, "chat_template_kwargs": {"enable_thinking": False},
    }, {"Content-Type": "application/json"})]
    assert model.closed


# 見込み: 私的な本文は生成失敗時にも欠落させず、送り主付きで届けられる。
# 合格: 通信・形式・中断エラーでは同じ原文を全文返し、一回だけ試行して接続を閉じる。送受信と失敗は模擬、標準出力・標準エラーに本文を残さない。
@pytest.mark.parametrize(("status", "body"), [
    (503, b""), (200, TimeoutError()), (200, b"not json"), (200, b'{"choices": []}'),
    (200, completion("読み上げの途中", "length")), (200, completion("  ")),
    (200, completion("<think>生成途中")), (200, completion(None)),
    (200, completion("あ" * 1001)), (200, completion("読み上げ不可\x00")),
    (200, completion("生成した本文", tool_calls=[{"id": "unexpected"}])),
])
def test_unavailable_or_broken_completion_falls_back_to_named_original(model, status, body, capsys):
    source = "テスト用の連絡。" * 9 + "最後の数字は17"
    model.status, model.body = status, body
    assert render_message(19286, "おじいちゃん", "テスター", source) == f"おじいちゃんから伝言や。「{source}」"
    assert len(model.sent) == 1 and model.closed
    assert capsys.readouterr() == ("", "")
