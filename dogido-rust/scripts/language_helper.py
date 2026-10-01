"""国語対話の入口だけを接続。解釈・検索・回答・検査・状態はRustが所有する。"""


def handle(command):
    raise ValueError("unsupported language helper command")


def run(exchange):
    command = exchange({"op": "language", "stage": "start"})
    if command.get("command") != "done":
        # 検索・生成をPythonへ戻すプロトコルは受け付けない。
        handle(command)
    return command
