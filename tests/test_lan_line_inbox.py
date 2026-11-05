"""共有ホストのLINE用Unixソケット。専用の一時ディレクトリと模擬配送で要求・結果・所有権を確認する。"""
import importlib.util
import json
from pathlib import Path
import socket
import stat
import tempfile
import unittest
from unittest.mock import Mock


MODULE = Path(__file__).resolve().parents[1] / "scripts/lan/line_inbox.py"
spec = importlib.util.spec_from_file_location("line_inbox", MODULE)
inbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inbox)


class LineSocketTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "family-line.sock"
        self.request = {"source_id": "line-" + "a" * 32, "sender": "お父さん", "text": "ごはんだよ"}

    def send(self, request):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(self.path))
            client.settimeout(2)
            client.sendall(json.dumps(request).encode() + b"\n")
            with client.makefile("rb") as reader:
                return json.loads(reader.readline())

    # 送信者・原文・IDが受付担当へ届く。成功とゲーム停止時の個別の応答、ソケットの権限・後片付けを実際の通信で見る。
    def test_delivery_and_status(self):
        deliver = Mock(side_effect=[None, "no_active_game", RuntimeError()])
        self.request["text"] = "𠮷" * 5000  # ASCIIエスケープで約60kBのLINE最大長。
        with inbox.serve(self.root, deliver):
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            self.assertEqual(self.send(self.request), {"accepted": True})
            self.assertEqual(self.send(self.request), {"accepted": False, "reason": "no_active_game"})
            self.assertEqual(self.send(self.request), {"accepted": False, "reason": "delivery_unknown"})
        deliver.assert_called_with(**self.request)
        self.assertFalse(self.path.exists())

    # 不正な通知をゲームへ入れない。本文の改行と自由な送り主IDが明示エラーになり、模擬配送は0件。
    def test_invalid(self):
        deliver = Mock()
        with inbox.serve(self.root, deliver):
            for change in ({"text": "一\n二"}, {"source_id": "元のLINE ID"}):
                self.assertEqual(self.send(self.request | change), {"accepted": False, "reason": "invalid_message"})
        deliver.assert_not_called()

    # 同名ファイルは置き換えない。起動失敗後も元の内容が残る。
    def test_foreign_file(self):
        self.path.write_text("既存")
        with self.assertRaises(ValueError), inbox.serve(self.root, Mock()):
            self.fail("既存ファイルの上では起動できない")
        self.assertEqual(self.path.read_text(), "既存")
