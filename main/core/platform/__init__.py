# -*- coding: utf-8 -*-
"""平台层门面：应用代码与「平台差异」之间的唯一接口。

应用模块不应该再直接 ``import win32gui`` / ``ctypes.windll`` / 判断 ``sys.platform``；
一律从这里问能力、拿实现。这样「新增一个平台」的成本是往 ``core/platform/`` 里补后端，
而不是在十几个业务模块里找平台分支。

现状：本模块导出平台探测与能力矩阵；具体能力的门面在各自的模块里——窗口
``window``、热键 ``hotkey``、系统权限清单 ``permissions``、剪贴板 ``clipboard``、指针与
输入注入 ``pointer``、窗口原生操作 ``window_ops``、抓帧 ``capture``、外壳 ``shell``、
自启 ``startup``、进程 ``process``、路径 ``paths``、字体 ``fonts``、凭据 ``secrets``。
业务模块从这些门面拿实现或问能力，不判断平台。

结构由 ``main/tests/test_platform_structure.py`` 强制，两条都卡：业务模块不许直接调
平台 API（``ctypes.windll`` / 平台专有导入 / ``os.startfile``），也不许拿
``IS_WINDOWS`` / ``PLATFORM_NAME`` 这类常量做分派。

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
