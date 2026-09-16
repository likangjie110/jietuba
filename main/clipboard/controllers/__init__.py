"""clipboard controllers 导出。

前台焦点的记录/切回与粘贴键注入已迁到平台层（core/platform/focus 与 pointer），
需要时直接从那里导入。
"""

from .context_menu_controller import ContextMenuData, MenuAction
from .clipboard_controller import (
    ClipboardController,
    calc_sidebar_capacity,
    calc_topbar_capacity,
)
from .selection_manager import SelectionManager

__all__ = [
    "ClipboardController",
    "ContextMenuData",
    "MenuAction",
    "SelectionManager",
    "calc_sidebar_capacity",
    "calc_topbar_capacity",
]