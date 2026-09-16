# -*- coding: utf-8 -*-
"""平台层各能力共用的数据模型。

放在单独模块里而不是各自的实现文件里，是为了避免循环导入：后端实现只依赖契约，
门面同时依赖契约与后端。若把 ``WindowInfo`` 定义在 ``window.py`` 里，后端导入它
就变成 ``window`` → 后端 → ``window``。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WindowInfo:
    """一个可选的窗口。

    ``handle`` 是平台的窗口标识：Windows 上是 HWND，macOS 上是 CGWindowNumber。
    两者都只是**标识符**——不要假设它在美国那边也是指针或句柄，除了相等比较、
    去重与写日志之外没有别的用途。

    ``rect`` 是 ``(x1, y1, x2, y2)``，物理像素，虚拟桌面坐标系（主屏左上角为原点，
    多显示器时可能是负数）。macOS 的 Quartz 给的是"点"，与 Qt 的全局坐标同一套，
    命中逻辑因此与 Windows 共用。
    """

    handle: int
    rect: tuple[int, int, int, int]
    title: str
