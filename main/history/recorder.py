# -*- coding: utf-8 -*-
"""把「用户确认过的截图结果」写进历史，并按保留策略淘汰旧条目。

调用点在截图动作处理器里（``tools/action.py``）：确定/复制/另存三条路径拿到导出图之后
各调一次。写历史**不应该**让截图本身失败，所以整段都包在 try 里，失败只记日志。
"""

from __future__ import annotations

import weakref

from core.logger import T, log_debug, log_exception

from . import source as source_module
from .store import HistoryEntry, HistoryStore

#: 进程内共享的历史存储（浏览窗口、设置页的清理按钮、记录点都用它）
_store: HistoryStore | None = None

#: 新条目到达时的订阅者（弱引用：窗口关掉之后不该因为这里被留住）
_subscribers: list = []


def subscribe(callback) -> None:
    """订阅「有新条目」事件（浏览窗口开着的时候要跟着刷新）。

    存弱引用：订阅方通常是窗口的方法，强引用会让关掉的历史窗口永远回收不了。
    """
    try:
        reference = (weakref.WeakMethod(callback)
                     if getattr(callback, "__self__", None) is not None
                     else lambda: callback)
    except TypeError:
        return
    _subscribers.append(reference)


def unsubscribe(callback) -> None:
    """退订；窗口关闭时调用。"""
    remaining = []
    for reference in _subscribers:
        target = reference()
        if target is None or target == callback:
            continue
        remaining.append(reference)
    _subscribers[:] = remaining


def _notify(entry: HistoryEntry) -> None:
    alive = []
    for reference in _subscribers:
        callback = reference()
        if callback is None:
            continue
        alive.append(reference)
        try:
            callback(entry)
        except Exception as e:
            log_exception(e, T("通知截图历史刷新"))
    _subscribers[:] = alive


def get_store() -> HistoryStore:
    """取进程共享的历史存储；第一次调用时建目录并读索引。"""
    global _store
    if _store is None:
        _store = HistoryStore()
    return _store


def reset_store(store: HistoryStore | None = None) -> None:
    """替换共享存储（测试与「换历史目录」用）。"""
    global _store
    _store = store


def _screens() -> list:
    try:
        from PySide6.QtGui import QGuiApplication

        application = QGuiApplication.instance()
        if application is None:
            return []
        return [screen.geometry() for screen in application.screens()]
    except Exception:
        return []


def record_screenshot(image, selection=None, config_manager=None, *,
                      screens=None, probe=None) -> HistoryEntry | None:
    """记录一次截图；没开历史、图是空的、或者写盘失败都返回 None。

    ``probe`` 只在测试里注入（假造「这一点上是哪个窗口」），生产走平台层。
    """
    try:
        if config_manager is not None and not config_manager.get_history_enabled():
            log_debug(T("截图历史已关闭，跳过记录"), "History")
            return None
        if image is None or image.isNull():
            return None

        store = get_store()
        history_source, label = source_module.describe(
            selection, screens=_screens() if screens is None else screens, probe=probe)
        entry = store.add(image, source=history_source, label=label)
        if entry is None:
            return None
        _apply_retention(store, config_manager)
        _notify(entry)
        return entry
    except Exception as e:
        log_exception(e, T("记录截图历史"))
        return None


def _apply_retention(store: HistoryStore, config_manager) -> int:
    """按配置淘汰旧条目；配置读不到时用「不限制」（宁可留多，也不误删）。"""
    values = {"retention_days": 0, "max_entries": 0, "max_disk_mb": 0}
    if config_manager is not None:
        try:
            values = {
                "retention_days": config_manager.get_history_retention_days(),
                "max_entries": config_manager.get_history_max_entries(),
                "max_disk_mb": config_manager.get_history_max_disk_mb(),
            }
        except Exception as e:
            log_exception(e, T("读取截图历史保留策略"))
    return store.apply_retention(
        retention_days=values["retention_days"],
        max_entries=values["max_entries"],
        max_disk_bytes=int(values["max_disk_mb"]) * 1024 * 1024,
    )


def apply_configured_retention(config_manager) -> int:
    """按配置收拾一次历史（设置页的「立即清理」与启动时调用）。"""
    return _apply_retention(get_store(), config_manager)
