"""国語補助は入口の一往復だけ。Pythonの資料検索へ戻さない。"""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from language_helper import handle, run


@pytest.mark.parametrize("command", ["interpretation", "reply", "prompt", "lookup"])
def test_helper_no_longer_owns_generation_validation_or_lookup(command):
    with pytest.raises(ValueError, match="unsupported language helper command"):
        handle({"command": command})


def test_language_entry_exchanges_once_without_importing_python_reader(monkeypatch):
    import dogido_server.language_dialogue.retrieval as retrieval
    monkeypatch.setattr(retrieval.LocalDialogueSearch, "search", lambda *a, **k: pytest.fail("Python lookup called"))
    frames = []
    def exchange(frame):
        frames.append(frame)
        return {"command": "done", "status": "answer", "text": "一学年やで。"}
    assert run(exchange)["text"] == "一学年やで。"
    assert frames == [{"op": "language", "stage": "start"}]


def test_old_lookup_protocol_cannot_reintroduce_python_search():
    with pytest.raises(ValueError, match="unsupported language helper command"):
        run(lambda frame: {"command": "lookup", "interpretation": {}})
