# -*- coding: utf-8 -*-
"""按识别结果自动遮挡敏感信息：手机号、邮箱、身份证号、银行卡号。

两件事分开：

- ``find_sensitive_spans``：在**文本**里找出敏感片段（纯函数，正则，无网络）；
- ``mask_boxes``：把命中的片段映射回它在图上的矩形并打码（马赛克）。

映射用的是「按字符在行内的位置线性插值」——OCR 只给整行的框，不给每个字的框，
所以按字符序号在行框宽度上按比例取一段。这比整行打码克制（同一行里的普通文字保留），
也是这个功能唯一能做到「只盖敏感部分」的办法；代价是等宽假设，中文/英文混排时框会
略有偏差，因此这里会把框**向两侧各放宽一点**，宁可多盖一两个字符也不漏。
"""

import re
from dataclasses import dataclass

from core.logger import T, log_debug

#: 各类敏感信息的识别式。顺序即优先级（先匹配到的类型胜出）。
PATTERNS = (
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    # 身份证：18 位（末位可能是 X）或 15 位
    ("id_card", re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
                           r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")),
    # 手机号：11 位，1 开头，第二位 3-9
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    # 银行卡：16~19 位连续数字
    ("bank_card", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
)

#: 命中的框向左右各放宽的像素（等宽假设的补偿）
BOX_PADDING = 3

#: 马赛克块大小（像素）
MOSAIC_BLOCK = 8


@dataclass(frozen=True)
class Span:
    """文本里命中的一段：类型、起止字符下标与命中的原文。"""

    kind: str
    start: int
    end: int
    text: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "start": self.start, "end": self.end, "text": self.text}


def find_sensitive_spans(text: str) -> list:
    """找出文本里所有敏感片段（按位置排序，互不重叠）。

    同一段文字只归一个类型：先匹配到的先占位，后面的重叠命中会被丢掉——
    一个 11 位手机号同时也是「16~19 位连续数字」的子串，重复上报只会让打码框叠两层。
    """
    source = str(text or "")
    if not source:
        return []
    found = []
    taken = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(source):
            start, end = match.start(), match.end()
            if any(start < other_end and end > other_start for other_start, other_end in taken):
                continue
            found.append(Span(kind, start, end, match.group()))
            taken.append((start, end))
    found.sort(key=lambda span: span.start)
    if found:
        log_debug(T("识别到 {count} 处敏感信息", count=len(found)), "Privacy")
    return found


def span_rect(box, start: int, end: int, total_chars: int, *, padding: int = BOX_PADDING):
    """把「行内第 start~end 个字符」映射成矩形（返回 ``[左, 上, 右, 下]``）。

    ``box`` 是 OCR 的整行框；等宽假设下按字符占比切分，再左右各放宽一点。
    """
    rect = _bounding_rect(box)
    if rect is None or total_chars <= 0 or end <= start:
        return None
    left, top, right, bottom = rect
    width = right - left
    span_left = left + width * (start / total_chars)
    span_right = left + width * (end / total_chars)
    return [int(max(left, span_left - padding)), int(top),
            int(min(right, span_right + padding)), int(bottom)]


def _bounding_rect(box):
    """OCR 框 → (左, 上, 右, 下)；不认识的形态返回 None。"""
    values = box
    if hasattr(values, "tolist"):
        values = values.tolist()
    if not isinstance(values, (list, tuple)) or not values:
        return None
    if len(values) == 4 and all(isinstance(item, (int, float)) for item in values):
        left, top, a, b = (float(item) for item in values)
        return (left, top, a, b)
    points = []
    for point in values:
        if hasattr(point, "tolist"):
            point = point.tolist()
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                points.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                continue
    if not points:
        return None
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    return (min(xs), min(ys), max(xs), max(ys))


def mask_regions(result, *, padding: int = BOX_PADDING) -> list:
    """从 OCR 结果里算出「要打码的矩形」列表（回到图像坐标系）。"""
    if not isinstance(result, dict):
        return []
    regions = []
    for row in result.get("data") or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text", "") or "")
        spans = find_sensitive_spans(text)
        if not spans:
            continue
        for span in spans:
            rect = span_rect(row.get("box"), span.start, span.end, len(text),
                             padding=padding)
            if rect and rect[2] > rect[0] and rect[3] > rect[1]:
                regions.append({"rect": rect, "kind": span.kind, "text": span.text})
    return regions


def mosaic_rectangles(image, regions, *, block: int = MOSAIC_BLOCK):
    """在 QImage 上把给定矩形打成马赛克，返回新图。"""
    from PySide6.QtGui import QImage

    if image is None or image.isNull():
        return QImage()
    if not regions:
        return image.copy()
    output = image.copy()
    size = max(2, int(block))
    for region in regions:
        rect = region.get("rect") if isinstance(region, dict) else region
        if not rect:
            continue
        left, top, right, bottom = (int(value) for value in rect)
        left, top = max(0, left), max(0, top)
        right, bottom = min(output.width(), right), min(output.height(), bottom)
        for y in range(top, bottom, size):
            for x in range(left, right, size):
                block_w = min(size, right - x)
                block_h = min(size, bottom - y)
                if block_w <= 0 or block_h <= 0:
                    continue
                patch = output.copy(x, y, block_w, block_h)
                average = _average_color(patch)
                for dy in range(block_h):
                    for dx in range(block_w):
                        output.setPixelColor(x + dx, y + dy, average)
    log_debug(T("已遮挡 {count} 处敏感信息", count=len(regions)), "Privacy")
    return output


def _average_color(patch):
    """一个小块的平均色（马赛克就是把块涂成均值）。"""
    from PySide6.QtGui import QColor

    total_r = total_g = total_b = 0
    count = 0
    for y in range(patch.height()):
        for x in range(patch.width()):
            color = patch.pixelColor(x, y)
            total_r += color.red()
            total_g += color.green()
            total_b += color.blue()
            count += 1
    if not count:
        return QColor(0, 0, 0)
    return QColor(total_r // count, total_g // count, total_b // count)
