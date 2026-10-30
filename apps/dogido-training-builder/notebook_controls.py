"""Colabで学習・独立評価・結果保存をボタンへ接続する。"""
import html
import json
from pathlib import Path
import subprocess
import sys


def result_html(output):
    summary = json.loads((Path(output) / "evaluation-summary.json").read_text(encoding="utf-8"))
    rows = []
    for mode, result in summary.items():
        for split, name in (("test", "テスト"), ("dev", "調整"), ("train", "学習内確認")):
            m = result["metrics"][split]
            rows.append(f"<tr><td>{html.escape(mode)}</td><td>{name}</td><td>{m['correct']}/{m['n']}</td>"
                        f"<td>{m['accuracy']:.1%}</td><td>{m['macro_f1']:.3f}</td></tr>")
    tables = ["<table><tr><th>モード</th><th>用途</th><th>正解/件数</th><th>正解率</th><th>macro F1</th></tr>"
              + "".join(rows) + "</table>"]
    for mode, result in summary.items():
        tables.append(f"<h4>{html.escape(mode)}：テストの分類別成績</h4><table>"
                      "<tr><th>分類</th><th>件数</th><th>適合率</th><th>再現率</th><th>F1</th></tr>")
        for label, m in result["metrics"]["test"]["per_class"].items():
            scores = f"<td>{m['precision']:.1%}</td><td>{m['recall']:.1%}</td><td>{m['f1']:.3f}</td>" if m['support'] else "<td colspan='3'>評価例なし</td>"
            tables.append(f"<tr><td>{html.escape(label)}</td><td>{m['support']}</td>{scores}</tr>")
        tables.append("</table><h4>テストの誤分類</h4><ul>")
        predictions = [json.loads(line) for line in (Path(output) / mode / "test-predictions.jsonl").read_text(encoding="utf-8").splitlines()]
        wrong = [row for row in predictions if not row['correct']]
        for row in wrong:
            tables.append(f"<li>{html.escape(row['text'])}<br>正解：{html.escape(row['gold'])} ／ 予測：{html.escape(row['predicted'])}</li>")
        tables.append("</ul>" if wrong else "<li>なし</li></ul>")
    return "".join(tables)


def show_controls(root, output):
    # Jupyter公式のButton.on_click + Outputを使用する。
    # https://ipywidgets.readthedocs.io/en/latest/examples/Widget%20Events.html
    import ipywidgets as widgets
    from IPython.display import display
    from google.colab import files

    root, output = Path(root), Path(output)
    modes = json.loads((root / "training-protocol.json").read_text(encoding="utf-8"))["profiles"]
    train = widgets.Button(description="学習する", button_style="primary")
    test = widgets.Button(description="テストする", button_style="success")
    download = widgets.Button(description="結果ZIPを保存")
    log = widgets.Output()
    report = widgets.HTML()

    def refresh():
        test.disabled = not all((output / mode / "done.json").is_file() for mode in modes)
        download.disabled = not ((output / "evaluation-summary.json").is_file() and (output / "dogido-training-results.zip").is_file())

    def execute(evaluate):
        for button in (train, test, download):
            button.disabled = True
        log.clear_output()
        report.value = ""
        try:
            command = [sys.executable, "-u", str(root / "train_colab.py"), "--root", str(root), "--output", str(output)]
            if evaluate:
                command.append("--evaluate-only")
            # stdoutを明示的にOutputへ配送し、Colabのcallbackでもログを残す。
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8") as process:
                for line in process.stdout:
                    log.append_stdout(line)
                if process.wait() != 0:
                    raise RuntimeError("処理に失敗しました。上のログを確認してください。")
            if evaluate:
                report.value = result_html(output)
                log.append_stdout("テスト完了。分類別成績・全予測・誤分類は結果ZIPに入っています。\n")
            else:
                log.append_stdout("学習完了。「テストする」を押してください。\n")
        except Exception as exc:
            log.append_stderr(str(exc) + "\n")
        finally:
            train.disabled = False
            refresh()

    def save_result(_):
        files.download(str(output / "dogido-training-results.zip"))

    train.on_click(lambda _: execute(False))
    test.on_click(lambda _: execute(True))
    download.on_click(save_result)
    refresh()
    display(widgets.HBox([train, test, download]), report, log)
    return {"train": train, "test": test, "download": download, "log": log, "report": report}
