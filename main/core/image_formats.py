# -*- coding: utf-8 -*-
"""保存格式清单：由**运行时可写能力**派生，而不是写死一张表。

写死清单的两种坏法都遇到过：列出一个这个构建里根本没有编码器的格式（用户选完点保存
直接失败），或者明明插件在、却选不到。所以这里问 Qt 的 `QImageWriter` 拿到能写的格式，
PDF 单独按 QtPdf 在不在补一条。

`verify_formats()` 是给测试用的自查：逐个真写一遍文件并读回，确保「列出来的都能写」。
"""

from __future__ import annotations

from core.logger import T, log_debug, log_warning

#: 支持的格式：id → 保存对话框过滤器
FORMAT_FILTERS = {
    "PNG": "PNG (*.png)",
    "JPG": "JPG (*.jpg)",
    "JPEG": "JPG (*.jpg)",
    "BMP": "BMP (*.bmp)",
    "WEBP": "WebP (*.webp)",
    "JXL": "JPEG XL (*.jxl)",
    "AVIF": "AVIF (*.avif)",
    "PDF": "PDF (*.pdf)",
}

#: 界面里的固定顺序（没列到的按字母序排后面）
FORMAT_ORDER = ("PNG", "JPG", "BMP", "WEBP", "JXL", "AVIF", "PDF")

#: Qt 的格式名与我们的 id 之间的差异
QT_ALIASES = {"JPEG": "JPG"}


def _writer_formats() -> set:
    """Qt 的图片写入器能写哪些格式（大写）。"""
    try:
        from PySide6.QtGui import QImageWriter

        return {bytes(name).decode("ascii").upper()
                for name in QImageWriter.supportedImageFormats()}
    except Exception as e:
        log_warning(T("读取可写图片格式失败: {e}", e=e), "ImageFormat")
        return set()


def _pdf_available() -> bool:
    """这个构建能不能写 PDF（QtPdfWriter 在 QtGui 里；缺了就没有 PDF）。"""
    try:
        from PySide6.QtGui import QPdfWriter  # noqa: F401

        return True
    except Exception:
        return False


def can_write(image_format: str) -> bool:
    """这个格式当前能不能真的写出来。"""
    fmt = normalize(image_format)
    if fmt == "PDF":
        return _pdf_available()
    return fmt in _writer_formats()


def normalize(image_format: str) -> str:
    """把外部传进来的格式名收敛成大写的规范名（``jpg``/``jpeg`` → ``JPG``）。"""
    name = str(image_format or "").strip().lstrip(".").upper()
    return QT_ALIASES.get(name, name)


def available_formats() -> list:
    """当前运行时可写的格式：``[(id, 过滤器)]``，只含真能写出来的那些。"""
    supported = _writer_formats()
    available = []
    for fmt in FORMAT_ORDER:
        if fmt in ("JPG", "JPEG"):
            if "JPEG" not in supported and "JPG" not in supported:
                continue
        elif fmt == "PDF":
            if not _pdf_available():
                continue
        elif fmt not in supported:
            continue
        available.append((fmt, FORMAT_FILTERS[fmt]))
    if not available:
        log_warning(T("没有任何可用的图片保存格式"), "ImageFormat")
    return available


def format_names() -> list:
    """只有名字的清单（设置页的下拉用它）。"""
    return [fmt for fmt, _filter in available_formats()]


def save_dialog_filter() -> str:
    """保存对话框的过滤器字符串（用 ``;;`` 连接）。"""
    return ";;".join(_filter for _fmt, _filter in available_formats())


def preferred_format(configured: str) -> str:
    """配置里的格式在当前运行时不可用时，退回第一个可用格式。"""
    fmt = normalize(configured)
    names = format_names()
    if fmt in names:
        return fmt
    fallback = names[0] if names else "PNG"
    if configured:
        log_debug(T("保存格式不可用，改用 {fallback}: {configured}",
                    fallback=fallback, configured=configured), "ImageFormat")
    return fallback


def verify_formats(directory: str) -> dict:
    """逐个格式真写一遍再读回，返回 ``{格式: 是否成功}``（自查 / 测试用）。

    写 PDF 与读图片分开判：PDF 用 QtPdf 读回页数，图片用 QImage 读回像素。
    """
    from PySide6.QtGui import QColor, QImage

    from core.save import SaveService

    results = {}
    service = SaveService()
    sample = QImage(64, 36, QImage.Format.Format_ARGB32)
    sample.fill(QColor("#3366CC"))
    for fmt in format_names():
        path = f"{directory}/format-check.{fmt.lower()}"
        try:
            saved = service.save_qimage_to_path(sample, path, image_format=fmt)
            results[fmt] = bool(saved) and _reads_back(path, fmt)
        except Exception:
            results[fmt] = False
    return results


def _reads_back(path: str, image_format: str) -> bool:
    import os

    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    if image_format == "PDF":
        try:
            from PySide6.QtPdf import QPdfDocument

            document = QPdfDocument()
            return document.load(path) is not None and document.pageCount() >= 1
        except Exception:
            return False
    from PySide6.QtGui import QImage

    image = QImage(path)
    return not image.isNull() and image.width() > 0
