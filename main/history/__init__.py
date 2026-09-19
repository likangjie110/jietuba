# -*- coding: utf-8 -*-
"""截图历史模块入口。

对外接口：
    HistoryStore      — 索引与图片的读写（含保留策略的执行）
    plan_eviction     — 保留策略的纯决策函数
    record_screenshot — 截图结果入史（截图动作处理器调用）

浏览窗口单独导入（它拉着整条 Qt 界面依赖）：
    from history.window import HistoryWindow
"""

from .recorder import apply_configured_retention, get_store, record_screenshot, reset_store
from .store import HISTORY_DIR_NAME, SOURCES, HistoryEntry, HistoryStore, RetentionPolicy, plan_eviction

__all__ = [
    "HISTORY_DIR_NAME",
    "SOURCES",
    "HistoryEntry",
    "HistoryStore",
    "RetentionPolicy",
    "apply_configured_retention",
    "get_store",
    "plan_eviction",
    "record_screenshot",
    "reset_store",
]
