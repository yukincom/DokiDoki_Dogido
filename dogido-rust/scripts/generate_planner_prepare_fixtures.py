"""Golden request preparation from the unchanged Python planner; no model calls.

Pass --canonical-root when generating outside the repository checkout.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def configure(root: Path) -> None:
    sys.path.insert(0, str(root))
    logging.disable(logging.CRITICAL)


def expected(inputs: dict, model: str | None) -> dict:
    from dogido_server.dialogue.player_chat_planner import plan_player_chat

    class Capture:
        request = None
        calls = 0
        def generate_structured_json(self, request):
            self.calls += 1
            assert self.calls == 1
            self.request = request
            return {**request.fallback_value, "__dogido_status": "disabled"}

    kwargs = dict(conversation_turns=[], observation_summary="", observed_entities=[])
    kwargs.update(inputs)
    fallback = asdict(plan_player_chat(None, **kwargs))
    capture = Capture()
    plan_player_chat(capture if model is not None else None, **kwargs)
    request = None
    if capture.request is not None:
        assert capture.request.kind == "player_chat_plan"
        assert capture.request.route == "chat"
        assert capture.request.temperature == 0.0
        assert capture.request.max_tokens == 640
        request = dict(schema_version=1, model=model, enable_thinking=False,
                       details=capture.request.details, fallback=fallback)
    return json.loads(json.dumps(dict(fallback=fallback, request=request), ensure_ascii=False))


def cases():
    from dogido_server.dialogue.chat_policy import catalog_speech_labels
    from dogido_server.entry_catalog import structure_entries
    old = [{"turn_id":"old", "role":"user", "text":"1位になるのは無理やな"},
           {"turn_id":"old:reply", "role":"assistant", "text":"1位になれんでもええやん。"}]
    pending = old + [{"turn_id":"q", "role":"user", "text":"違う", "repair_action":"clarify_repair",
                     "repair_target_turn_id":"old:reply", "repair_target_quote":old[-1]["text"]},
                    {"turn_id":"q:reply", "role":"assistant", "text":"どういう意味やった？"}]
    def case(name, text, model="fixture-model", **kwargs):
        inputs = {"user_text":text, **kwargs}
        return dict(name=name, model=model, input=inputs, expected=expected(inputs,model))
    for text in ["", "  \n\t\u001c", "おはよう", "家に帰ろう", "家に帰る。ヤギがいない", "家に帰る。ヤギが見えない", "家に帰る。今の音は？", "石炭何個ある？", "今なんの音？", "猫はいる？"]:
        for inventory in [False,True]:
            for sound in [False,True]:
                for model in [None,"fixture-model"]:
                    yield case(f"priority:{text!r}:{inventory}:{sound}:{model}",text,model,inventory_question=inventory,sound_question=sound)
    histories = [[], [{"turn_id":"a", "role":"assistant", "text":"ヤギおるな"}],
        [{"turn_id":"a", "role":"assistant", "text":"猫おるな"}],
        [{"turn_id":"a", "role":"assistant", "text":"猫もヤギもおるな"}],
        [{"turn_id":"a", "role":"user", "text":"ヤギおるな"}]]
    denials = ["見えない", "見えん", "見えへん", "見当たらない", "見当たらん", "見当たらへん", "いない", "おらん", "おらへん"]
    for denial in denials:
        for prefix in ["", "ヤギが", "ヤギも今は", "ヤギーなんか全然", "いや、", "それ", "でも今は", "ヤギの角が", "猫とヤギが", "ヤギがいるけど猫が", "もしヤギが", "『ヤギがいる』でも", "「ヤギがいる」ヤギが", '"猫"ヤギが']:
            for suffix in ["", "よ！", "んじゃない？", "絵", "なら", "って聞いた", "わけじゃない", "のかもしれない", "\n"]:
                text=prefix+denial+suffix
                for i, history in enumerate(histories):
                    yield case(f"denial:{text!r}:{i}",text,conversation_turns=history)
    for label in catalog_speech_labels():
        for suffix in ["が見えない", "がおらん", "がいる", "はいる？"]:
            yield case(f"label:{label}:{suffix}",label+suffix,conversation_turns=[{"role":"assistant", "text":label+"おるな"}])
    for sid, entry in structure_entries().items():
        label=str(entry.get("label") or sid)
        for suffix in ["はある？", "がある", "見えるかな", "がない", "ないか", "は見える！"]:
            yield case(f"structure:{sid}:{suffix}",label+suffix)
    for prefix in ["ラバ", "ヤギ", "何", "誰", "気配", "家", "こんな場所", "これ"]:
        for suffix in ["がいる", "がまだいる", "が今もいる", "がいまもおる", "がもうない", "おらん", "まだおる", "今も気配", "いまも居る", "いるかな", "おるん", "いるの", "いないの", "いないかな", "いないか", "あるか", "何？", "とは"]:
            yield case(f"presence:{prefix}:{suffix}",prefix+suffix)
    for i, history in enumerate([old,pending,pending[:-1],pending+[dict(role="user",text="別件")],pending[:-1]+[dict(role="assistant",turn_id="wrong",text="どういう意味？")]]):
        for text in ["違う、仲間になるのは無理ってこと", "そういう意味じゃない", "『違う』というキャラ", "仲間になるのは無理", "うん", "違う？"]:
            for enabled in [True,False]:
                for raw in [None,"", "原文には訂正なし", "違う", "訂正"*499+"違うよ"]:
                    yield case(f"repair:{i}:{text}:{enabled}:{raw!r}",text,conversation_turns=history,repair_enabled=enabled,raw_user_text=raw)
    for history in [None,{},"history",[None,[],{},False,3],
            [dict(role="user", text=str(n)) for n in range(20)],
            [dict(role="user",text="valid")]+[None]*10,
            [None,dict(role=" user\u001c",text=" abc\n  def ",turn_id="\u001c t\n1"),dict(role="system",text="skip")],
            [dict(role="assistant",text=17,turn_id=False,repair_action=None,repair_target_turn_id=True,repair_target_quote=["a",None,True],repair_signal_quote={"b":2,"a":1},repair_replacement_quote="\n長"*200)],
            [dict(role="user",text="漢🐈"*200,turn_id="T🐈"*200)],
            [dict(role="user",text=False),dict(role="assistant",text=[1,True,None],turn_id=" ")]]:
        yield case("history:"+repr(history),"確認",conversation_turns=history)
    for rows in [[],[{}], [{"entity_id":" minecraft:GoAt\n ","label":" "},{"entity_id":"goat","label":"second"},{"entity_id":"Minecraft:CAT","label":"猫"}],
            [{"entity_id":"goat"}]*16+[{"entity_id":"cat"}],
            [{}]*16+[{"entity_id":"cat"}],
            [{"entity_id":"🐈"*100,"label":"漢"*100}],
            [{"entity_id":None,"label":"none"},{"entity_id":False},{"entity_id":True,"label":17},{"entity_id":["b",None]},{"entity_id":{"b":2,"a":1}}],
            [{"entity_id":str(n)} for n in range(20)]]:
        yield case("observed:"+repr(rows),"いる？",observed_entities=rows)
    yield case("all-bounds","漢🐈 \n"*300,raw_user_text=" \n🐈"*500,observation_summary="景🐈 "*300,hearing_summary="音🐈 "*200,look_target_label="目🐈 "*100)
    yield case("python-spaces", "\u001c犬\u001d 猫\u0085山\u200b\u2028野\u2029空\u001f", conversation_turns=[dict(role="user\u001f",text="\u001c話\u001d犬")],observed_entities=[dict(entity_id="\u001cminecraft:Goat\u001f",label="\u0085山\u001d羊")])
    yield case("literal-slots", '{{history}} @@DOGIDO_SLOT:observations@@ 🐈', observation_summary='current: {{raw_text}}',raw_user_text="raw\n{{current}}")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--canonical-root",type=Path,default=ROOT.parent)
    parser.add_argument("--output",type=Path,default=ROOT/"src/planner/prepare/fixtures.json")
    args=parser.parse_args(); configure(args.canonical_root)
    rows=list(cases())
    args.output.write_text(json.dumps(rows,ensure_ascii=False,separators=(",",":"))+"\n")
    print(f"canonical request preparation cases: {len(rows)}")

if __name__=="__main__":main()
