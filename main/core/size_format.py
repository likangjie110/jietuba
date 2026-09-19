# -*- coding: utf-8 -*-
"""字节数转可读文本。

界面里有两处各写了一份近似实现（剪贴板列表、历史列表），保存预览又要一份——放到这里
一份，改口径（比如小数位）时不会只改到一处。
"""

from __future__ import annotations

UNITS = ("B", "KB", "MB", "GB", "TB")


def human_size(size_bytes) -> str:
    """把字节数转成 ``1.2 MB`` 这样的文本；坏输入返回空串（调用方按「未知」处理）。"""
    try:
        value = float(size_bytes)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    index = 0
    while value >= 1024 and index < len(UNITS) - 1:
        value /= 1024.0
        index += 1
    if index == 0:
        return f"{int(value)} {UNITS[index]}"
    return f"{value:.1f} {UNITS[index]}"
