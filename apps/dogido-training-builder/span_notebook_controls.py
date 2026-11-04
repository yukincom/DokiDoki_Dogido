"""語句抽出の学習・評価・一件試行・結果保存をColabのボタンへ接続する。"""
import html
import json
from pathlib import Path
import subprocess
import sys

SPLIT_NAMES = {"test": "テスト", "regression": "既知実例の確認", "dev": "調整", "train": "学習内確認"}


def score(value, percentage=False):
    return "—" if value is None else (f"{value:.1%}" if percentage else f"{value:.3f}")


def spans_text(spans):
    names = {"from": "変更元", "to": "変更先"}
    return " / ".join(f"{names[s['role']]}={s['text']!r} [{s['start']}:{s['end']}]" for s in spans) or "なし"


def result_html(output):
    output = Path(output).resolve()
    summary = json.loads((output / "evaluation-summary.json").read_text(encoding="utf-8"))
    evaluation = (output / summary["evaluation"]).resolve()
    if not evaluation.is_relative_to(output):
        raise ValueError("評価の保存場所が不正です。")
    done = summary["training_complete"]
    pieces = [f"<p>学習 {done['epochs_completed']} 周、保存した最良モデルは {summary['selected_epoch']} 周目です。</p>",
              "<p>調整はモデル選択に使用。テストと既知実例は学習・選択の対象外です。学習内確認は未知例の成績ではありません。</p>",
              "<table><tr><th>用途</th><th>完全一致</th><th>一致率</th><th>範囲の適合率</th><th>再現率</th><th>F1</th></tr>"]
    for split, title in SPLIT_NAMES.items():
        if split not in summary["metrics"]:
            continue
        m = summary["metrics"][split]["all"]
        pieces.append(f"<tr><td>{title}</td><td>{m['exact']}/{m['count']}</td><td>{score(m['exact_rate'], True)}</td>"
                      f"<td>{score(m['span_precision'], True)}</td><td>{score(m['span_recall'], True)}</td><td>{score(m['span_f1'])}</td></tr>")
    pieces.append("</table>")
    for split, title in SPLIT_NAMES.items():
        if split not in summary["metrics"]:
            continue
        result = summary["metrics"][split]
        pieces.append(f"<details><summary>{title}：変更元・変更先の成績と失敗例</summary><table>"
                      "<tr><th>役割</th><th>適合率</th><th>再現率</th><th>F1</th></tr>")
        for role, name in (("from", "変更元"), ("to", "変更先")):
            m = result["by_role"][role]
            pieces.append(f"<tr><td>{name}</td><td>{score(m['span_precision'], True)}</td>"
                          f"<td>{score(m['span_recall'], True)}</td><td>{score(m['span_f1'])}</td></tr>")
        pieces.append("</table>")
        wrong = [json.loads(line) for line in (evaluation / (split + "-predictions.jsonl")).read_text(encoding="utf-8").splitlines()
                 if line.strip() and not json.loads(line)["exact"]]
        for row in wrong:
            text = "現在句: " + row.get("current_verse", "") + "\n発話: " + row["text"] + "\n正解: " + spans_text(row["spans"]) + "\n予測: " + spans_text(row["predicted"])
            pieces.append("<pre style='white-space:pre-wrap'>" + html.escape(text) + "</pre>")
        if not wrong:
            pieces.append("<p>失敗例なし</p>")
        pieces.append("</details>")
    return "".join(pieces)


def show_controls(root, output):
    import ipywidgets as widgets
    from IPython.display import display
    from google.colab import files
    from span_train_colab import completed, digest, inside

    root, output = Path(root).resolve(), Path(output).resolve()
    train = widgets.Button(description="学習する", button_style="primary")
    test = widgets.Button(description="評価・テストする", button_style="success")
    download = widgets.Button(description="結果ZIPを保存")
    current_verse = widgets.Textarea(description="現在の句", placeholder="3行の句。分からなければ空欄", layout=widgets.Layout(width="95%", height="90px"))
    utterance = widgets.Textarea(description="発話原文", placeholder="抽出を試したい発話を、誤字・改行も含めてそのまま入力", layout=widgets.Layout(width="95%", height="80px"))
    try_one = widgets.Button(description="1件試す")
    log = widgets.Output()
    report = widgets.HTML()
    single_result = widgets.HTML()
    buttons = (train, test, download, try_one)

    def refresh():
        ready = (output / "active-run.json").is_file()
        test.disabled = not ready
        try_one.disabled = not ready
        download.disabled = not (ready and (output / "evaluation-summary.json").is_file())

    def command(*extra):
        return [sys.executable, "-u", str(root / "span_train_colab.py"), "--root", str(root), "--output", str(output), *extra]

    def execute(evaluate):
        for button in buttons:
            button.disabled = True
        log.clear_output()
        report.value = ""
        try:
            with subprocess.Popen(command(*(["--evaluate-only"] if evaluate else [])), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding="utf-8") as process:
                for line in process.stdout:
                    log.append_stdout(line)
                if process.wait():
                    raise RuntimeError("処理に失敗しました。上のログを確認してください。中断した試行は保持されます。")
            if evaluate:
                report.value = result_html(output)
                log.append_stdout("評価完了。全予測・失敗例・学習履歴・モデルは結果ZIPに入っています。\n")
            else:
                log.append_stdout("学習完了。「評価・テストする」を押してください。\n")
        except Exception as exc:
            log.append_stderr(str(exc) + "\n")
        finally:
            train.disabled = False
            refresh()

    def save_result(_):
        try:
            completed(root, output)
            summary = json.loads((output / "evaluation-summary.json").read_text(encoding="utf-8"))
            archive = inside(output, summary["archive"])
            if digest(archive) != summary["archive_sha256"]:
                raise ValueError("結果ZIPが変更されています。評価・テストをやり直してください。")
            files.download(str(archive))
        except Exception as exc:
            log.append_stderr(str(exc) + "\n")

    def predict(_):
        for button in buttons:
            button.disabled = True
        single_result.value = ""
        try:
            request = {"text": utterance.value, "current_verse": current_verse.value}
            process = subprocess.run(command("--predict-stdin"), input=json.dumps(request, ensure_ascii=False),
                                     capture_output=True, text=True, encoding="utf-8")
            if process.returncode:
                raise RuntimeError(process.stderr or process.stdout)
            result = json.loads(process.stdout)
            single_result.value = "<pre style='white-space:pre-wrap'>" + html.escape(spans_text(result["predicted"])) + "</pre>"
        except Exception as exc:
            log.append_stderr(str(exc) + "\n")
        finally:
            train.disabled = False
            refresh()

    train.on_click(lambda _: execute(False))
    test.on_click(lambda _: execute(True))
    download.on_click(save_result)
    try_one.on_click(predict)
    refresh()
    display(widgets.HBox([train, test, download]), report, log,
            widgets.HTML("<h4>保存した最良モデルで1件試す</h4><p>この入力は教材・評価成績へ追加されません。</p>"),
            current_verse, utterance, try_one, single_result)
    return {"train": train, "test": test, "download": download, "try_one": try_one,
            "current_verse": current_verse, "utterance": utterance, "log": log, "report": report, "single_result": single_result}
