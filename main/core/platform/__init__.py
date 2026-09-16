# -*- coding: utf-8 -*-
"""平台层门面：应用代码与「平台差异」之间的唯一接口。

应用模块不应该再直接 ``import win32gui`` / ``ctypes.windll`` / 判断 ``sys.platform``；
一律从这里问能力、拿实现。这样「新增一个平台」的成本是往 ``core/platform/`` 里补后端，
而不是在十几个业务模块里找平台分支。

现状（能力逐个迁移中）：本模块已导出平台探测与能力矩阵，窗口、热键、指针、剪贴板等
能力的门面在后续提交里陆续并入；未迁移完成前，对应模块仍有各自的分支，迁移时删除。

包名刻意放在 ``core/`` 下而不是顶层 ``platform/``：``main/`` 会被插到 ``sys.path`` 最前
（``core/bootstrap.py:ensure_module_path``），顶层建一个 ``platform`` 包会把标准库的
``platform`` 模块整个遮掉。
"""

from core.platform.capabilities import (
    Capability,
    Support,
    available,
    backend_name,
    support,
    unsupported,
)
from core.platform.detection import (
    IS_LINUX,
    IS_MACOS,
    IS_WINDOWS,
    PLATFORM_NAME,
    SUPPORTED_PLATFORMS,
    is_native_qt_platform,
    qt_platform_name,
)

__all__ = [
    # 平台探测
    "IS_WINDOWS",
    "IS_MACOS",
    "IS_LINUX",
    "PLATFORM_NAME",
    "SUPPORTED_PLATFORMS",
    "qt_platform_name",
    "is_native_qt_platform",
    # 能力矩阵
    "Capability",
    "Support",
    "support",
    "available",
    "backend_name",
    "unsupported",
]
