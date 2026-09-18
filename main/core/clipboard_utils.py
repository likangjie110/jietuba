# -*- coding: utf-8 -*-
"""
图像投递：把截图送进系统剪贴板，并按需在后台线程保存到磁盘。

写剪贴板本身（Windows 的 CF_DIBV5 + PNG、其它平台的 Qt 回退）在
``core/platform/clipboard.py``；这里只负责编排——剪贴板同步写（主线程），
落盘放后台线程，避免保存大图时卡住界面。
"""

from __future__ import annotations

import hashlib
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QImage

from core import log_exception, log_warning, log_debug, T
from core.platform.clipboard import copy_image as clipboard_clipboard_copy

if TYPE_CHECKING:
    from core.save import SaveService




def attach_image_file_to_clipboard(image: QImage, *, keep_image: bool = True) -> bool:
    """把图片再以 PNG 文件的形式放进剪贴板（设置里的「复制图像为文件」）。

    为什么需要：有些程序（不少终端、聊天工具、IDE）粘贴时只认 ``text/uri-list``，
    给它们图片数据是没反应的；反过来也有程序只认图片。``keep_image=True``（自动档）
    时两种都给，由目标程序自己挑；``False``（只给文件档）时去掉图片数据。

    文件落在临时目录下、按内容命名，同一张图重复粘贴不会堆一堆文件。返回是否写入成功。
    """
    if image is None or image.isNull():
        return False

    try:
        from PySide6.QtCore import QMimeData, QUrl
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False

        directory = Path(tempfile.gettempdir()) / "jietuba_clipboard_files"
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(
            bytes(image.constBits()[: image.sizeInBytes()])
        ).hexdigest()[:16]
        path = directory / f"{digest}.png"
        if not path.exists() and not image.save(str(path), "PNG"):
            log_warning(T("图片写入临时文件失败: {path}", path=str(path)), "Clipboard")
            return False

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        if keep_image:
            # 图片数据也要留着：自动档下两种格式并存，谁认哪个用哪个
            mime.setImageData(image)
        clipboard.setMimeData(mime)
        log_debug(T("图片已按文件形式放入剪贴板: {path}", path=str(path)), "Clipboard")
        return True
    except Exception as e:
        log_exception(e, T("把图片以文件形式放进剪贴板"))
        return False


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
