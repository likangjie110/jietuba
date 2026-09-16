# -*- coding: utf-8 -*-
"""把图像写进系统剪贴板。

Windows 走 Win32：写 ``CF_DIBV5`` 并注册 ``"PNG"`` 格式，剪贴板被占用时重试。
这套做法是 Windows 专有的——DIBV5 让粘贴到画图/Word 这类原生程序时颜色与尺寸正确，
注册 ``"PNG"`` 则让支持它的程序拿到无损数据。

其它平台退回 Qt 的 ``setImage``。这不只是"能不能用"的差别：Qt 回退没有重试，
也没有 ``"PNG"`` 格式注册，粘贴到某些程序时的保真度与 Windows 不同，因此能力矩阵里
记的是降级实现（``Capability.CLIPBOARD_IMAGE``）。
"""

from __future__ import annotations

import io
import struct
import threading

from core.platform.detection import IS_WINDOWS

# 剪贴板同一时刻只能被一个进程打开，写入期间必须串行
_CLIPBOARD_WRITE_LOCK = threading.Lock()
# 0x800401D0 = CLRBRD_E_CANT_OPEN，被别的程序占用时的标准 HRESULT
_CLIPBOARD_BUSY_HRESULT = -2147221040
# 重试延迟逐级拉长：占用通常只持续几十毫秒（另一个进程正在取数据）
_WRITE_RETRY_DELAYS = (0.0, 0.02, 0.05, 0.1, 0.2, 0.35)


def backend_name() -> str:
    """当前平台的写法名，用于日志。"""
    return "win32" if IS_WINDOWS else "qt"


def copy_image(image) -> bool:
    """把 QImage 写进系统剪贴板，返回是否成功。

    Windows 上 Win32 写入失败会退回 Qt——宁可少一点保真度，也不要让用户看到
    "复制了但剪贴板里没有"。
    """
    from core.logger import log_warning, T

    if image is None or image.isNull():
        log_warning(T("剪切板: 图像为空"), "Clipboard")
        return False

    if IS_WINDOWS:
        try:
            _copy_win32_with_retry(image)
            return True
        except Exception as e:
            log_warning(T("剪切板: Win32 写入失败 ({e})", e=e), "Clipboard")

    try:
        _copy_qt(image)
        return True
    except Exception as e:
        from core.logger import log_exception

        log_exception(e, T("写入系统剪贴板"))
        return False


def _copy_qt(image) -> None:
    """Qt 回退：交给 QApplication 的剪贴板对象（三平台都可用）。"""
    from core.logger import log_info, T
    from PySide6.QtWidgets import QApplication

    QApplication.clipboard().setImage(image)
    log_info(T("已复制到剪切板 (Qt)"), "Clipboard")


# ──────────────────────────────────────────────
# Win32 实现
# ──────────────────────────────────────────────

def _copy_win32_with_retry(image) -> None:
    """用 Win32 写入 CF_DIBV5 + PNG，遇到剪贴板被占用时按延迟表重试。"""
    import time as _time

    from core.logger import log_debug, T

    last_exc = None
    total_attempts = len(_WRITE_RETRY_DELAYS)

    for attempt_index, delay in enumerate(_WRITE_RETRY_DELAYS, start=1):
        if delay > 0:
            _time.sleep(delay)
        try:
            _write_win32(image)
            return
        except Exception as exc:
            last_exc = exc
            if not _is_clipboard_busy_error(exc) or attempt_index == total_attempts:
                raise
            log_debug(
                T(
                    "剪贴板: {path_name} 写入时剪贴板被占用，准备重试 {attempt_next}/{total_attempts}",
                    path_name="Win32",
                    attempt_next=attempt_index + 1,
                    total_attempts=total_attempts,
                ),
                "Clipboard",
            )

    if last_exc is not None:
        raise last_exc


def _is_clipboard_busy_error(exc: Exception) -> bool:
    """判断异常是不是「剪贴板被别的进程占用」。

    pywin32 抛的异常形状不止一种（hresult 属性、args[0]、args[0][0]），
    三种都认，否则偶发的占用会被当成真失败。
    """
    if getattr(exc, "hresult", None) == _CLIPBOARD_BUSY_HRESULT:
        return True
    args = getattr(exc, "args", ())
    if args:
        if args[0] == _CLIPBOARD_BUSY_HRESULT:
            return True
        if isinstance(args[0], tuple) and args[0] and args[0][0] == _CLIPBOARD_BUSY_HRESULT:
            return True
    return False


def _write_win32(image) -> None:
    """真正写剪贴板：CF_DIBV5（17）与注册的 "PNG" 格式各写一份。"""
    import time as _time

    import win32clipboard

    from core.logger import log_debug, log_info, T

    t0 = _time.perf_counter()
    dibv5_data = _build_dibv5(image)
    t1 = _time.perf_counter()
    png_data = _build_png(image)
    t2 = _time.perf_counter()

    fmt_png = win32clipboard.RegisterClipboardFormat("PNG")
    with _CLIPBOARD_WRITE_LOCK:
        win32clipboard.OpenClipboard(0)
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(fmt_png, png_data)
            win32clipboard.SetClipboardData(17, dibv5_data)  # CF_DIBV5
        finally:
            win32clipboard.CloseClipboard()
    t3 = _time.perf_counter()

    log_debug(
        T(
            "已复制到剪切板 (Win32) "
            "dibv5={dibv5_ms:.1f}ms png={png_ms:.1f}ms win32={win32_ms:.1f}ms",
            dibv5_ms=(t1 - t0) * 1000,
            png_ms=(t2 - t1) * 1000,
            win32_ms=(t3 - t2) * 1000,
        ),
        "Clipboard",
    )
    log_info(T("已复制到剪切板 (Win32 CF_DIBV5 + PNG)"), "Clipboard")


def _build_dibv5(image) -> bytes:
    """把 QImage 转成 BITMAPV5HEADER + 32 位 BGRA 像素（bottom-up）。

    bottom-up 是 DIB 的约定（高度为正表示从下往上存），Qt 这边用 C++ 层做垂直
    翻转，避免 Python 逐行循环。
    """
    from PySide6.QtGui import QImage

    img = image.convertToFormat(QImage.Format.Format_ARGB32)

    w = img.width()
    h = img.height()
    stride = w * 4  # 32bpp，天然 4 字节对齐
    pixel_size = stride * h

    header = io.BytesIO()
    header.write(struct.pack('<I', 124))          # bV5Size
    header.write(struct.pack('<i', w))            # bV5Width
    header.write(struct.pack('<i', h))            # bV5Height（正 = bottom-up）
    header.write(struct.pack('<H', 1))            # bV5Planes
    header.write(struct.pack('<H', 32))           # bV5BitCount
    header.write(struct.pack('<I', 3))            # bV5Compression = BI_BITFIELDS
    header.write(struct.pack('<I', pixel_size))   # bV5SizeImage
    header.write(struct.pack('<i', 0))            # bV5XPelsPerMeter
    header.write(struct.pack('<i', 0))            # bV5YPelsPerMeter
    header.write(struct.pack('<I', 0))            # bV5ClrUsed
    header.write(struct.pack('<I', 0))            # bV5ClrImportant
    header.write(struct.pack('<I', 0x00FF0000))   # bV5RedMask
    header.write(struct.pack('<I', 0x0000FF00))   # bV5GreenMask
    header.write(struct.pack('<I', 0x000000FF))   # bV5BlueMask
    header.write(struct.pack('<I', 0xFF000000))   # bV5AlphaMask
    header.write(struct.pack('<I', 0x73524742))   # bV5CSType = LCS_sRGB
    header.write(b'\x00' * 36)                    # bV5Endpoints (CIEXYZTRIPLE)
    header.write(struct.pack('<I', 0))            # bV5GammaRed
    header.write(struct.pack('<I', 0))            # bV5GammaGreen
    header.write(struct.pack('<I', 0))            # bV5GammaBlue
    header.write(struct.pack('<I', 4))            # bV5Intent = LCS_GM_IMAGES
    header.write(struct.pack('<I', 0))            # bV5ProfileData
    header.write(struct.pack('<I', 0))            # bV5ProfileSize
    header.write(struct.pack('<I', 0))            # bV5Reserved

    header_bytes = header.getvalue()
    assert len(header_bytes) == 124

    # Qt 6.9 起 mirrored() 已废弃，flipped() 是等价替代（两者的字节结果实测一致）
    from PySide6.QtCore import Qt

    flipped = img.flipped(Qt.Orientation.Vertical)  # 垂直翻转 → bottom-up
    bits = flipped.bits()
    return header_bytes + bytes(bits)


def _build_png(image) -> bytes:
    """把 QImage 编码成 PNG 字节流。

    quality=50：比 100 多花约 14ms，体积缩小约 95%——写剪贴板时这点时间可感知，
    但体积差异会直接体现在粘贴进文档后的文件大小上。
    """
    from PySide6.QtCore import QBuffer, QIODeviceBase

    buf = QBuffer()
    buf.open(QIODeviceBase.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG", 50)
    buf.close()
    return bytes(buf.data())
