# -*- coding: utf-8 -*-
"""把译文画回图上：原图文字替换 / 原图下方双语对照。

截图翻译此前只给一个文本窗口；这里补上「图」的那一半。两种呈现各自解决一个问题：

- ``replace``：按识别框把原文盖掉、写上译文——适合分享一张「已经是中文」的图；
- ``bilingual``：原图不动，在下方追加一段与行数相称的译文区——适合对照阅读，
  也不会因为遮挡而丢掉原图信息。

渲染是纯函数（输入 QImage + 行数据，输出新 QImage），所以像素级断言可以直接写。
需要网络的是**取译文**那一步，不在本模块里——本模块只负责画。
"""

from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen

from core.logger import T, log_debug

#: 三种呈现：只给文本（老行为）/ 原图替换 / 双语对照
MODES = ("text", "replace", "bilingual")

#: 字体最小/最大像素高度，以及框内边距
MIN_FONT_PX = 8
MAX_FONT_PX = 64
PADDING = 2

#: 双语区：每行译文的行高倍数与整体上下留白
LINE_HEIGHT_RATIO = 1.35
PANEL_PADDING = 10

#: 双语区太窄时按这个最小宽度排字（避免 1px 宽的图把文字挤成竖条）
MIN_PANEL_WIDTH = 120


@dataclass
class TranslatedLine:
    """一行：识别框（四点或矩形）+ 原文 + 译文。"""

    box: list
    text: str = ""
    translation: str = ""
    meta: dict = field(default_factory=dict)

    def rect(self) -> QRectF:
        """识别框的包围矩形；框不合法时返回空矩形（调用方会跳过）。"""
        return bounding_rect(self.box)


def lines_from_ocr_result(result) -> list:
    """把 OCR 结果（``{"data": [{"box", "text", "score"}, ...]}``）转成行列表。"""
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    if not isinstance(data, list):
        return []
    lines = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue
        lines.append(TranslatedLine(box=item.get("box"), text=text))
    return lines


#: 逐行翻译的请求上限：超过就退回双语模式（一次请求），避免把用户额度当无限用
MAX_LINE_REQUESTS = 40


def translate_lines(service, lines, target_lang: str, *, source_lang: str | None = None,
                    max_lines: int = MAX_LINE_REQUESTS) -> tuple:
    """逐行取译文，返回 ``(填好译文的行, 失败行数)``。

    为什么要逐行：替换模式必须知道「哪句译文属于哪个框」。代价是每行一次请求，所以行数
    超过 ``max_lines`` 时**不做**这件事（返回只填了部分的行，调用方据此退回双语模式）。
    """
    from translation.models import TranslationRequest

    if service is None or not lines:
        return list(lines or []), 0
    if len(lines) > max_lines:
        log_debug(T("行数超过 {max}，不逐行翻译", max=max_lines), "ImageTranslation")
        return list(lines), 0

    failures = 0
    for line in lines:
        try:
            result = service.translate(TranslationRequest(
                text=line.text, target_lang=target_lang, source_lang=source_lang))
        except Exception as e:
            from core.logger import log_exception

            log_exception(e, T("翻译第 {index} 行", index=len(line.text)))
            failures += 1
            continue
        text = str(getattr(result, "translated_text", "") or "").strip()
        if getattr(result, "success", False) and text:
            line.translation = text
        else:
            failures += 1
    return lines, failures


def bounding_rect(box) -> QRectF:
    """把 OCR 框（``[[x,y], ...]`` / ``[x, y, w, h]`` / ``QRectF``）统一成矩形。

    OCR 引擎给的框有四边形（带旋转）也有 [左,上,右,下]；这里只取包围盒——画译文不需要
    知道原文的旋转角度，而算包围盒对两种形态都成立。
    """
    if box is None:
        return QRectF()
    if isinstance(box, QRectF):
        return QRectF(box)
    values = box
    if hasattr(values, "tolist"):
        values = values.tolist()
    if not isinstance(values, (list, tuple)) or not values:
        return QRectF()
    if len(values) == 4 and all(isinstance(item, (int, float)) for item in values):
        left, top, a, b = (float(item) for item in values)
        # 兼容 [左, 上, 右, 下] 与 [左, 上, 宽, 高]：宽高一定小于等于右下坐标
        if a >= left and b >= top and (a - left) >= 0 and (b - top) >= 0:
            return QRectF(left, top, a - left, b - top).normalized()
        return QRectF(left, top, a, b).normalized()
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
        return QRectF()
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).normalized()


def render_translated_image(image: QImage, lines, mode: str = "replace", *,
                            font_family: str = "") -> QImage:
    """按 ``mode`` 把译文画到图上，返回**新的** QImage（原图不动）。

    ``text`` 模式原样返回一份拷贝：调用方（截图翻译）在这种模式下走文本窗口，不落图。
    """
    if image is None or image.isNull():
        return QImage()
    result = image.copy()
    mode = mode if mode in MODES else "replace"
    usable = [line for line in lines if isinstance(line, TranslatedLine)
              and line.translation.strip() and not line.rect().isEmpty()]

    if mode == "text" or not usable:
        return result

    if mode == "bilingual":
        return _render_bilingual(result, usable, font_family)
    return _render_replace(result, usable, font_family)


# ── 原图替换 ──────────────────────────────────────────────

def _render_replace(image: QImage, lines, font_family: str) -> QImage:
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    try:
        for line in lines:
            rect = line.rect().adjusted(-PADDING, -PADDING, PADDING, PADDING)
            if not image.rect().intersects(rect.toRect()):
                continue
            background = _sample_background(image, line.rect())
            painter.fillRect(rect, background)
            font = _fit_font(line.translation, rect, font_family)
            painter.setFont(font)
            painter.setPen(QPen(_contrast_color(background)))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter) |
                             int(Qt.TextFlag.TextWordWrap), line.translation)
    finally:
        painter.end()
    log_debug(T("译文已画回原图（替换模式，{count} 行）", count=len(lines)),
              "ImageTranslation")
    return image


def _sample_background(image: QImage, rect: QRectF) -> QColor:
    """取框周围的底色：沿框的四边各采一圈像素，取出现最多的那个颜色。

    直接取框内平均色会把文字本身的颜色也算进去（黑字白底会得到灰底），所以采**框外**一圈。
    """
    ring = rect.adjusted(-PADDING, -PADDING, PADDING, PADDING)
    samples = {}
    step = max(1, int(min(ring.width(), ring.height()) / 8) or 1)
    points = []
    for x in range(int(ring.left()), int(ring.right()) + 1, step):
        points.append((x, int(ring.top())))
        points.append((x, int(ring.bottom())))
    for y in range(int(ring.top()), int(ring.bottom()) + 1, step):
        points.append((int(ring.left()), y))
        points.append((int(ring.right()), y))

    for x, y in points:
        if 0 <= x < image.width() and 0 <= y < image.height():
            color = image.pixelColor(x, y)
            key = (color.red(), color.green(), color.blue())
            samples[key] = samples.get(key, 0) + 1
    if not samples:
        return QColor(255, 255, 255)
    best = max(samples.items(), key=lambda item: item[1])[0]
    return QColor(*best)


def _contrast_color(background: QColor) -> QColor:
    """在给定底色上挑一个读得清的文字色（亮底用黑、暗底用白）。"""
    luminance = (0.299 * background.red() + 0.587 * background.green()
                 + 0.114 * background.blue())
    return QColor(0, 0, 0) if luminance > 140 else QColor(255, 255, 255)


def _fit_font(text: str, rect: QRectF, font_family: str) -> QFont:
    """挑一个能放进框里的字号：从框高往下试，装不下就缩小，最小到 MIN_FONT_PX。"""
    font = QFont()
    if font_family:
        font.setFamily(font_family)
    limit = max(MIN_FONT_PX, min(MAX_FONT_PX, int(rect.height())))
    size = limit
    while size > MIN_FONT_PX:
        font.setPixelSize(size)
        metrics = QFontMetricsF(font)
        bounds = metrics.boundingRect(
            rect, int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignLeft),
            text)
        if bounds.height() <= rect.height() and bounds.width() <= rect.width() + 1:
            break
        size -= 1
    font.setPixelSize(max(MIN_FONT_PX, size))
    return font


# ── 双语对照 ──────────────────────────────────────────────

def bilingual_panel_height(lines, width: int, font_family: str = "") -> int:
    """双语区需要多高：按每行译文换行后的实际行数算（导出尺寸要提前知道）。"""
    if not lines:
        return 0
    font = QFont()
    if font_family:
        font.setFamily(font_family)
    font.setPixelSize(int(font.pixelSize() if font.pixelSize() > 0 else 14))
    metrics = QFontMetricsF(font)
    line_height = metrics.height() * LINE_HEIGHT_RATIO
    total = PANEL_PADDING
    inner = max(MIN_PANEL_WIDTH, width) - 2 * PANEL_PADDING
    for line in lines:
        _, rows = _wrap_translation(line.translation, inner, metrics)
        total += rows * line_height
    return int(total + PANEL_PADDING)


def _wrap_translation(text: str, width: float, metrics: QFontMetricsF):
    """把译文折成能放下的行；返回 (行列表, 行数)。空文本算 0 行。"""
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return [], 0
    rows = []
    current = ""
    for chunk in cleaned:
        candidate = current + chunk
        if metrics.horizontalAdvance(candidate) > width and current:
            rows.append(current)
            current = chunk
        else:
            current = candidate
    if current:
        rows.append(current)
    return rows, len(rows)


def _render_bilingual(image: QImage, lines, font_family: str) -> QImage:
    """在原图下方接一块译文区；原图像素一行不动。"""
    height = bilingual_panel_height(lines, image.width(), font_family)
    if height <= 0:
        return image
    output = QImage(image.width(), image.height() + height, image.format())
    output.fill(QColor(255, 255, 255))
    painter = QPainter(output)
    try:
        painter.drawImage(0, 0, image)
        painter.setPen(QPen(QColor(0, 0, 0)))
        painter.drawLine(0, image.height(), output.width(), image.height())
        font = QFont()
        if font_family:
            font.setFamily(font_family)
        size = int(font.pixelSize() if font.pixelSize() > 0 else 14)
        font.setPixelSize(max(MIN_FONT_PX, size))
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        line_height = metrics.height() * LINE_HEIGHT_RATIO
        y = float(image.height() + PANEL_PADDING)
        inner = max(MIN_PANEL_WIDTH, image.width()) - 2 * PANEL_PADDING
        for line in lines:
            rows, _count = _wrap_translation(line.translation, inner, metrics)
            for row in rows:
                painter.drawText(float(PANEL_PADDING), y + metrics.ascent(), row)
                y += line_height
    finally:
        painter.end()
    log_debug(T("译文已画回原图（双语对照，{count} 行）", count=len(lines)),
              "ImageTranslation")
    return output
