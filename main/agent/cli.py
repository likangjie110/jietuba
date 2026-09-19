# -*- coding: utf-8 -*-
"""``--json`` 命令行入口：给外部 Agent / 脚本用的结构化接口。

    python main_app.py --json status
    python main_app.py --json capture [--out 图片路径]
    python main_app.py --json ocr [--from 图片路径] [--text-out 文本路径]

约定：
- 标准输出**只有一行 JSON**（日志走 stderr/文件，不污染解析）；
- 退出码 0 = 命令成功，1 = 命令失败（JSON 里带 error），2 = 用法错误；
- 有常驻应用时优先经本机 bridge 走（复用已加载的 OCR/模型），没有就自己跑一份
  ——两条路的返回结构完全一样，调用方不需要知道走了哪条。
"""

import sys

from core.logger import T, log_debug

#: ``--json`` 后面跟的选项名 → 命令参数
OPTION_KEYS = {
    "--out": "path",
    "--from": "path",
    "--text-out": "save_text",
    "--no-save": "no_save",
}


def parse_argv(argv) -> tuple:
    """解析 ``--json <command> [--key value]``；返回 ``(command, options, error)``。"""
    args = list(argv or [])
    options = {}
    index = 0
    while index < len(args):
        token = args[index]
        if token in OPTION_KEYS:
            if index + 1 >= len(args):
                return "", {}, f"{token} 后面缺少值"
            options[OPTION_KEYS[token]] = args[index + 1]
            index += 2
            continue
        index += 1

    # 命令名是 ``--json`` 之后的第一个非选项参数
    command = ""
    for index, token in enumerate(args):
        if token == "--json":
            for follow in args[index + 1:]:
                if not follow.startswith("--"):
                    command = follow
                    break
            break
    if not command:
        return "", options, "缺少命令：status / capture / ocr"
    return command, options, ""


def is_agent_invocation(argv) -> bool:
    """命令行里有没有 ``--json``（bootstrap 靠它决定走 CLI 还是 GUI）。"""
    return "--json" in list(argv or [])


def _clean_options(command: str, options: dict) -> dict:
    """按命令裁剪参数：capture 不吃 --from，ocr 不吃 --out，免得传错了还静默忽略。"""
    if command == "capture":
        keep = {"path"} if not options.get("no_save") else set()
    elif command == "ocr":
        keep = {"path", "save_text"}
    else:
        keep = set()
    return {key: value for key, value in options.items() if key in keep}


def main(argv=None, *, call_bridge=None, out=None) -> int:
    """执行一次 CLI 调用；返回进程退出码。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout

    from agent.commands import dumps, run_command

    command, options, error = parse_argv(argv)
    if error:
        out.write(dumps({"ok": False, "command": "", "error": error}) + "\n")
        return 2

    options = _clean_options(command, options)
    result = None
    if call_bridge is not None:
        probed = call_bridge(command, options)
        if probed is not None:
            result = probed
            log_debug(T("Agent 命令经本机 bridge 执行: {command}", command=command), "Agent")
    if result is None:
        result = run_command(command, options)

    out.write(dumps(result) + "\n")
    try:
        out.flush()
    except Exception:
        pass
    return 0 if result.get("ok") else 1


def handle_cli(argv=None) -> bool:
    """bootstrap 的入口：是 ``--json`` 调用就执行并返回 True（不启动 GUI）。"""
    argv = list(sys.argv if argv is None else argv)
    if not is_agent_invocation(argv):
        return False

    from agent.bridge import call_bridge

    try:
        code = main(argv[1:], call_bridge=call_bridge)
    except Exception as e:
        from core.logger import log_exception

        log_exception(e, T("执行 Agent 命令行"))
        code = 1
    sys.exit(code)
