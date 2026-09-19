# -*- coding: utf-8 -*-
"""Agent 接口：命令行（``--json``）与本机 bridge，两条入口共用 agent/commands.py 的实现。

设计要点见三个模块各自的 docstring；对外只有一件事要知道：**外部调用不会弹窗、不抢焦点、
不动鼠标键盘**，返回统一是 ``{"ok": bool, "command": str, ...}`` 的 JSON。
"""

from agent.commands import COMMANDS, dumps, run_command

__all__ = ["COMMANDS", "dumps", "run_command"]
