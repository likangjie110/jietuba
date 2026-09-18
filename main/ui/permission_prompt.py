# -*- coding: utf-8 -*-
"""权限缺失提示：说清「为什么没反应」，并把用户送到能修好的地方。

抓屏与热键各自在失败点叫这里一次。两个约束决定了它的形状：

- **每条权限每进程只提示一次**：抓屏是高频动作，每截一次弹一次没法用；热键那条同理——
  权限没给，用户多按几次热键也不会多知道什么。去重收在这里一处，调用点不用各自记状态。
- **只在主线程弹窗**：抓屏可能发生在后台线程（``main_app`` 的 CaptureThread），Qt 对话框
  不能在别的线程上 exec。非主线程只记日志。
"""

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from core.i18n import make_tr
from core.logger import log_debug, T
from core.platform import permissions
from ui.dialogs import show_custom_confirm_dialog

_tr = make_tr("PermissionPrompt")

# 每条权限的标题与正文；没登记过的 key 用一段兜底文案
_TEXTS = {
    permissions.SCREEN_RECORDING: (
        "Screen Recording Permission Missing",
        "Screenshots will contain the desktop only, because jietuba has no Screen "
        "Recording permission.",
    ),
    permissions.ACCESSIBILITY: (
        "Accessibility Permission Missing",
        "Global hotkeys do nothing while jietuba has no Accessibility permission.",
    ),
}
_FALLBACK_TEXT = (
    "System Permission Missing",
    "jietuba is missing a system permission it needs to work.",
)

_ACTION_OPEN = "open"

_prompted: set[str] = set()


def _on_main_thread() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    return QThread.currentThread() == app.thread()


def prompt_missing_permission(permission_key: str, *, parent=None) -> bool:
    """某条权限缺失时提示一次；返回是否真的弹了。

    弹窗上「打开权限设置」直接跳到设置里的权限页——系统对话框只在首次登记时弹一次，
    用户拒绝过之后只能靠这条路径把权限改回来。
    """
    if permission_key in _prompted:
        return False
    if not _on_main_thread():
        log_debug(T("不在主线程，跳过权限提示: {key}", key=permission_key), "Permission")
        return False

    # 先记账再弹：exec() 期间可能被重入（例如抓屏线程又在跑）
    _prompted.add(permission_key)

    title, message = _TEXTS.get(permission_key, _FALLBACK_TEXT)
    action = show_custom_confirm_dialog(
        parent,
        _tr(title),
        _tr(message),
        [
            {
                "id": _ACTION_OPEN,
                "text": _tr("Open Permission Settings"),
                "role": QDialogButtonBox.ButtonRole.AcceptRole,
                "default": True,
            },
            {
                "id": "later",
                "text": _tr("Later"),
                "role": QDialogButtonBox.ButtonRole.RejectRole,
            },
        ],
    )

    if action == _ACTION_OPEN:
        from ui.permission_actions import open_permission_settings

        open_permission_settings()
    return True


def reset_prompt_state() -> None:
    """清掉「已经提示过」的记录（测试用）。"""
    _prompted.clear()
