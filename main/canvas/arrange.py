# -*- coding: utf-8 -*-
"""标注元素的层级与对齐：纯几何决策，谁都不碰 Qt 的事件循环。

- **对齐**：以「选区」为参照——标注是对着截图内容放的，把元素对齐到选区左/中/右、上/中/下
  才是用户想要的；对齐到别的元素需要多选，而画布当前是单选。
- **层级**：``front``/``back`` 直接压到最上/最下（在所有标注元素之间），``forward``/``backward``
  在当前层级上走一格。z 值只比较**其它标注元素**，不碰背景与幕布（它们有自己的固定层级）。

判定都基于 ``sceneBoundingRect()`` 与 ``pos()``：不管元素把几何记在自己的 rect 里还是
场景坐标里，移动量都等价于 ``setPos(pos + delta)``。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF

#: 对齐方式
ALIGN_MODES = ("left", "hcenter", "right", "top", "vcenter", "bottom")
#: 层级调整方式
Z_MODES = ("front", "back", "forward", "backward")

#: 层级调整时不必参与的图层（背景、幕布、马赛克/像素补丁这类底纹）
Z_EXCLUDED_Z = (0, 5, 6, 8, 9)


def alignment_delta(bounds: QRectF, target: QRectF, mode: str) -> QPointF:
    """算出把 ``bounds`` 按 ``mode`` 对齐到 ``target`` 需要的位移。"""
    if mode not in ALIGN_MODES or bounds.isEmpty() or target.isEmpty():
        return QPointF(0, 0)
    if mode == "left":
        return QPointF(target.left() - bounds.left(), 0)
    if mode == "hcenter":
        return QPointF(target.center().x() - bounds.center().x(), 0)
    if mode == "right":
        return QPointF(target.right() - bounds.right(), 0)
    if mode == "top":
        return QPointF(0, target.top() - bounds.top())
    if mode == "vcenter":
        return QPointF(0, target.center().y() - bounds.center().y())
    if mode == "bottom":
        return QPointF(0, target.bottom() - bounds.bottom())
    return QPointF(0, 0)


def next_z_value(others, current: float, mode: str) -> float:
    """算出层级调整后的 z 值；``others`` 是场景里其它可调整元素的 z 值。"""
    values = [float(value) for value in others]
    if mode == "front":
        return (max(values) + 1.0) if values else current + 1.0
    if mode == "back":
        return (min(values) - 1.0) if values else current - 1.0
    if mode == "forward":
        return current + 1.0
    if mode == "backward":
        return current - 1.0
    return current


def is_adjustable_z(item) -> bool:
    """这个元素参不参与层级调整：底纹类（背景/幕布/像素补丁/水印）不参与。

    判据是 z 值而不是类型：底纹各自的固定层级写在 ``Z_EXCLUDED_Z`` 里，加新的底纹只要
    在那里登记一个值即可；按类型判断的话每加一种元素都要回来改这里。
    """
    try:
        return round(float(item.zValue())) not in Z_EXCLUDED_Z
    except Exception:
        return False
