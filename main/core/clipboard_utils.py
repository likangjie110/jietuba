# -*- coding: utf-8 -*-
"""
图像投递：把截图送进系统剪贴板，并按需在后台线程保存到磁盘。

写剪贴板本身（Windows 的 CF_DIBV5 + PNG、其它平台的 Qt 回退）在
``core/platform/clipboard.py``；这里只负责编排——剪贴板同步写（主线程），
落盘放后台线程，避免保存大图时卡住界面。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtGui import QImage

from core import log_warning, log_debug, T
from core.platform.clipboard import copy_image as clipboard_clipboard_copy

if TYPE_CHECKING:
    from core.save import SaveService




def deliver_image_async(
    image: QImage,
    *,
    copy_to_clipboard: bool = True,
    save_service: "SaveService | None" = None,
    save_kwargs: dict | None = None,
) -> threading.Thread | None:
    """复制到剪贴板并可选在后台线程中保存图像。"""
    if image is None or image.isNull():
        log_warning(T("图像投递: 图像为空，跳过"), "Clipboard")
        return None

    if not copy_to_clipboard and save_service is None:
        log_warning(T("图像投递: 未请求复制或保存，跳过"), "Clipboard")
        return None

    save_kwargs = dict(save_kwargs or {})

    if copy_to_clipboard:
        # 写剪贴板必须在主线程（Qt 的剪贴板对象不是线程安全的），因此这里同步做；
        # 平台差异（Win32 的 DIBV5+PNG 与 Qt 回退）由平台层负责。
        clipboard_clipboard_copy(image)
        copy_to_clipboard = False

    if save_service is None:
        return None

    def worker() -> None:
        nonlocal image
        import time as _time

        t0 = _time.perf_counter()
        clipboard_ok = not copy_to_clipboard
        save_ok = save_service is None

        try:
            t1 = _time.perf_counter()

            if save_service is not None:
                save_ok, _ = save_service.save_qimage(image, **save_kwargs)
            t2 = _time.perf_counter()

            log_debug(
                T(
                    "异步图像投递完成 clipboard={clipboard_ok} save={save_ok} "
                    "clipboard={clipboard_ms:.1f}ms save={save_ms:.1f}ms total={total_ms:.1f}ms",
                    clipboard_ok=clipboard_ok,
                    save_ok=save_ok,
                    clipboard_ms=(t1 - t0) * 1000,
                    save_ms=(t2 - t1) * 1000,
                    total_ms=(t2 - t0) * 1000,
                ),
                "Clipboard"
            )
        except Exception as exc:
            log_warning(T("图像投递: 后台任务失败 ({exc})", exc=exc), "Clipboard")
        finally:
            image = None

    thread = threading.Thread(target=worker, daemon=True, name="ClipboardDeliver")
    thread.start()
    return thread
