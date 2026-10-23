"""共通23分類のIPCを模擬する。分類ラベルは各ケースが明示し、意味の代替判定をしない。"""
from contextlib import contextmanager
from pathlib import Path
import json
import sys
import tempfile

LABELS = [
    "not_target", "enemy_information", "equip_sword", "recall_mount", "dogido_recall",
    "senryu_line_upper", "senryu_line_middle", "senryu_line_lower",
    "senryu_change_upper", "senryu_change_middle", "senryu_change_lower",
    "look_direction", "senryu_end", "look_relative_right", "look_target", "look_player",
    "look_relative_front", "look_relative_back", "look_relative_left",
    "look_relative_front_left", "look_relative_front_right",
    "look_relative_back_left", "look_relative_back_right",
]
LIMITS = {"max_history_utterances": 0, "max_length": 128, "truncation": False}


@contextmanager
def mock_request_classifier(labels, *, extra_args=(), default_label="not_target"):
    """(serve-dialogue追加引数, 模擬分類器ディレクトリ)を返す。

    labels.json は原文→ラベルの明示表。実行中の変更も次入力から反映する。
    requests.jsonl に分類IPCを記録し、fail-inference を置くと推論失敗を模擬する。
    既存の --helper 配下の各プロセスは元の場所で動かし、token模擬と併用する。
    """
    if default_label not in LABELS or any(label not in LABELS for label in labels.values()):
        raise ValueError("fixture label outside adopted 23-class contract")
    scripts = Path(__file__).resolve().parent
    helper = scripts / "haiku_tokens.py"
    forwarded = []
    args = iter(extra_args)
    for arg in args:
        if arg == "--helper":
            helper = Path(next(args))
        elif arg == "--request-classifier-settings":
            next(args)
        elif arg.startswith("--helper="):
            helper = Path(arg.split("=", 1)[1])
        elif not arg.startswith("--request-classifier-settings="):
            forwarded.append(arg)
    with tempfile.TemporaryDirectory(prefix="dogido-request-classifier-") as temp:
        folder = Path(temp)
        sources = {source.name: source for source in scripts.glob("*.py")}
        sources.update({source.name: source for source in helper.parent.glob("*.py")})
        for name, source in sources.items():
            if name == "request_classifier_worker.py":
                continue
            if name == helper.name:
                # Rustの資料検索はhelperのcanonical pathを起点にする。
                (folder / name).symlink_to(source.resolve())
            else:
                # 模擬workerの__file__も元の場所を保ち、隣の制御ファイルを読めるようにする。
                (folder / name).write_text(
                    "import runpy,sys\n"
                    f"sys.path.insert(0, {str(source.parent)!r})\n"
                    f"globals().update(runpy.run_path({str(source)!r}, run_name=__name__))\n"
                )
        (folder / "labels.json").write_text(json.dumps(labels, ensure_ascii=False))
        (folder / "adopted-model.json").write_text(json.dumps({
            "schema_version": 1, "path_base": "repository_root", "evaluated_input": LIMITS,
        }))
        worker = '''import json,sys
from pathlib import Path
folder=Path(__file__).parent
for line in sys.stdin:
    frame=json.loads(line)
    reply={key:frame[key] for key in ("schema_version","request_id","manifest_sha256")}
    if frame["op"]=="load":
        reply.update(ready=True,labels=LABELS,limits=LIMITS)
    else:
        with (folder/"requests.jsonl").open("a") as out:
            out.write(json.dumps(frame,ensure_ascii=False)+"\\n")
        if (folder/"fail-inference").exists():
            reply["error"]="fixture request classification failure"
        else:
            raw=frame["text"].split("現在のユーザー発話:\\n",1)[1]
            label=json.loads((folder/"labels.json").read_text()).get(raw,DEFAULT_LABEL)
            index=LABELS.index(label)
            reply["prediction"]={"label":label,"probabilities":[int(i==index) for i in range(len(LABELS))],"token_count":30,"inference_ms":1}
    print(json.dumps(reply,ensure_ascii=False),flush=True)
'''
        (folder / "request_classifier_worker.py").write_text(
            f"LABELS={LABELS!r}\nLIMITS={LIMITS!r}\nDEFAULT_LABEL={default_label!r}\n" + worker
        )
        settings = {"model_root": str(folder), "python": sys.executable}
        yield (*forwarded, "--helper", str(folder / helper.name),
               "--request-classifier-settings", json.dumps(settings)), folder


def resolve_input_response(base, response):
    """BERTの非同期受付行を待ち、同じ入力から転送された操作・会話の応答を返す。"""
    if response.get("reason") != "request_classification":
        return response
    from check_dialogue import snapshot, wait_for
    turn = response["turn_id"]
    def routed():
        item = next((row for row in snapshot(base)["utterances"] if row["turn_id"] == turn), None)
        if item is None:
            return None
        if item.get("playback_status") in ("failed", "cancelled"):
            raise AssertionError(f"request classification did not forward: {item}")
        return item.get("forwarded_input")
    forwarded = wait_for(routed)
    assert forwarded["accepted"], forwarded
    return forwarded
