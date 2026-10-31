"""Synthetic fixtures. Expected results are never included in model messages."""
from copy import deepcopy

SAKURA = ["さくらのは", "くろいおのへと", "あさのいろ"]
EYES = ["くろいめが", "やみにひそんで", "こえひとつ"]


def edit(line, target, replacement):
    return {"line_id": f"line_{line}", "target_fragment": target, "replacement_text": replacement}


def frames_cases():
    cases = []
    specs = [
        ("earlier_after_detour", SAKURA, edit(1, "さくらのは", "さくらいろ"), edit(1, "さくらのは", "はるのいろ"),
         "最初に出した案にして", 0, ["朝の静かな雰囲気は残したいね", "色が出てくると景色を思い浮かべやすいね"]),
        ("latest_partial", EYES, edit(1, "くろいめ", "ひかるめ"), edit(1, "くろいめ", "あおいめ"),
         "最後に私が出した案にして", 1, []),
        ("invalid_idea_survives", SAKURA, edit(3, "あさのいろ", "あさのひかり"), edit(3, "あさのいろ", "よるのいろ"),
         "最初の案にして。音数はそのあと相談しよう", 0, []),
        ("same_words_different_line", SAKURA, edit(1, "さくらのは", "はるのいろ"), edit(3, "あさのいろ", "はるのいろ"),
         "はるのいろを上に入れる方の案にして", 0, []),
    ]
    for name, lines, first, second, selection, wanted, detours in specs:
        turns = []
        for proposal in (first, second):
            label = {"line_1": "上", "line_2": "真ん中", "line_3": "下"}[proposal["line_id"]]
            turns.append({"text": f"{label}の『{proposal['target_fragment']}』を『{proposal['replacement_text']}』にするのはどう？ まだ変えずに候補として残して。",
                          "expected_action": "offer", "expected_edit": proposal})
        turns += [{"text": t, "expected_action": "discuss", "expected_edit": None} for t in detours]
        turns.append({"text": selection, "expected_action": "select", "expected_edit": (first, second)[wanted]})
        cases.append({"name": name, "lines": lines, "turns": turns})
    return deepcopy(cases)


def patch_cases():
    return [
        {"name": "replacement_only", "lines": EYES,
         "previous": edit(1, "くろいめ", "あかるいめ"), "failure": "meter_mismatch",
         "text": "差し替え案の『あかるいめ』だけ『ひかるめ』に変えて。対象箇所はそのまま。",
         "expected": edit(1, "くろいめ", "ひかるめ")},
        {"name": "whole_line_repair", "lines": SAKURA,
         "previous": edit(1, "さくらのは", "さくらいろかな"), "failure": "meter_mismatch",
         "text": "その案、かなはいらないな。『さくらいろ』にして。",
         "expected": edit(1, "さくらのは", "さくらいろ")},
        {"name": "question_not_edit", "lines": EYES,
         "previous": edit(1, "くろいめ", "あかるいめ"), "failure": "meter_mismatch",
         "text": "『ひかるめ』に変えたらどうなる？ まだ変更しないで。", "expected": None},
        {"name": "target_fragment_only", "lines": EYES,
         "previous": edit(1, "くろい", "ひかるめ"), "failure": "meter_mismatch",
         "text": "置換前の文字列は『くろい』じゃなくて『くろいめ』にして。差し替え案の『ひかるめ』はそのまま。",
         "expected": edit(1, "くろいめ", "ひかるめ")},
        {"name": "line_only", "lines": [EYES[0], EYES[1], EYES[0]],
         "previous": edit(1, "くろいめ", "ひかるめ"), "failure": "player_corrected_target",
         "text": "変えるのは上じゃなく下。『くろいめ』を『ひかるめ』にして。",
         "expected": edit(3, "くろいめ", "ひかるめ")},
        {"name": "two_fields", "lines": EYES,
         "previous": edit(3, "こえ", "あかるいめ"), "failure": "player_corrected_target_and_text",
         "text": "対象は上の『くろいめ』、差し替えは『ひかるめ』にして。",
         "expected": edit(1, "くろいめ", "ひかるめ")},
    ]


def focus_cases():
    intro = [
        {"role": "user", "content": "上をさくらいろにする案c1と、はるのいろにする案c2を比べよう。"},
        {"role": "assistant", "content": "c1は桜の色、c2は春全体の色という感じや。"},
    ]
    side = [
        {"role": "user", "content": "春の色というのは桜だけのこと？"},
        {"role": "assistant", "content": "桜に限らず、草木の色も思い浮かべられるで。"},
        {"role": "user", "content": "じゃあ緑も含むんだね。"},
        {"role": "assistant", "content": "そうやな、緑も含めて考えられる。"},
    ]
    def case(name, main, side_history, text, action, candidate, open_=True):
        return {"name": name, "open": open_, "candidates": ["c1", "c2"],
                "history": intro + [main] + side_history, "main_question": main,
                "text": text, "expected_action": action, "expected_id": candidate}
    return [
        case("selection_after_meaning", {"role": "assistant", "content": "c2の案で試してみる？", "kind": "stage", "candidate_id": "c2"},
             side, "じゃあ、さっき聞いてくれたその案でお願い", "stage", "c2"),
        case("close_after_side_question", {"role": "assistant", "content": "この句の相談はここまででええ？", "kind": "close", "candidate_id": None},
             side, "説明はわかった。さっきの確認への返事は、うん、それでいいよ", "close", None),
        case("ack_is_not_selection", {"role": "assistant", "content": "c1の案で試してみる？", "kind": "stage", "candidate_id": "c1"},
             side[:-1] + [{"role": "assistant", "content": "その言葉の意味は分かった？"}], "うん", "discuss", None),
        case("decline_selection", {"role": "assistant", "content": "c1の案で試してみる？", "kind": "stage", "candidate_id": "c1"},
             side, "さっきの案を使うのはやめて、もう少し相談しよう", "continue", None),
        case("closed_does_not_reopen", {"role": "assistant", "content": "c1の案で試してみる？", "kind": "stage", "candidate_id": "c1"},
             [{"role": "user", "content": "この句の相談はもう終わり"}, {"role": "assistant", "content": "ここまでやな。"},
              {"role": "user", "content": "敵が来た"}, {"role": "assistant", "content": "気をつけてな。"}], "うん", "discuss", None, False),
        case("no_unique_main_reference", {"role": "assistant", "content": "c1とc2、どっちにする？", "kind": "choose", "candidate_id": None},
             side, "じゃあ、それで", "clarify", None),
    ]
