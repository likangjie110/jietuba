# -*- coding: utf-8 -*-
"""
engine.py - OCR 引擎接口

引擎 =「探测可用性 → 初始化 → 识别 → 释放」四件事的封装。外部引擎包只要实现这里的
OcrEngine，用 OCRManager.register_engine() 注册进来就能用，不必改动本仓库其它文件
——OCRManager 只认识这个接口，不认识任何具体扩展。

识别结果的统一约定：recognize() 返回 ``[[box, text, score], ...]``

    box   四个角点 ``[[x1,y1],[x2,y2],[x3,y3],[x4,y4]]``
    text  该行文字，空行由引擎自己丢弃
    score 0~1 的置信度，给不出置信度的引擎填 1.0

组装成 dict / list / text 三种 return_format 由本模块的 format_result()、
empty_result()、error_result() 统一负责。各引擎自己拼格式的话，下游（钉图文字层、
翻译）拿到的结构就会各不相同——这部分不要在子类里重写。
"""
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Tuple

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage


def ocr_log(msg, level: str = "INFO"):
    """写 OCR 日志。msg 可以是 str，也可以是 T() 构造的可翻译消息。

    core.logger 不可用时静默退回 print：OCR 会在启动预加载阶段被调用，那时日志
    系统未必已经就绪，但不该因此丢掉整条识别路径。
    """
    try:
        from core.logger import log_info, log_warning, log_error, log_debug
        if level == "ERROR":
            log_error(msg, "OCR")
        elif level == "WARN":
            log_warning(msg, "OCR")
        elif level == "DEBUG":
            log_debug(msg, "OCR")
        else:
            log_info(msg, "OCR")
    except Exception:
        pass


class OcrEngine(ABC):
    """OCR 引擎基类。

    子类必须实现 is_available() 与 recognize()，其余按需覆盖：
    initialize() 预热、release() 释放、is_loaded()/status_text() 报告状态。
    """

    #: 引擎 id。OCRManager 用它选择引擎，也是对外 API 的一部分（set_ocr_engine 的入参），
    #: 改名等于破坏兼容。
    name: str = ""
    #: 显示名，只用于日志与状态文本。
    label: str = ""

    def __init__(self):
        self.last_error: Optional[str] = None

    # ── 生命周期 ──────────────────────────────────────

    @abstractmethod
    def is_available(self) -> bool:
        """扩展、模型或系统组件是否齐备。

        这个方法会被反复调用（选引擎、初始化、设置页检测），实现里必须自己缓存
        探测结果，不要每次重新扫磁盘或加载 DLL。
        """

    @abstractmethod
    def recognize(self, pixmap, return_format: str = "dict") -> Any:
        """识别一张图（QPixmap 或 QImage）。返回格式见模块文档。"""

    def initialize(self, language: Optional[str] = None) -> bool:
        """预热引擎（加载模型、建立连接、准备子进程）。默认无操作。"""
        return True

    def release(self) -> None:  # noqa: B027 —— 默认无操作是接口的一部分：多数引擎没有可释放的东西
        """释放引擎占用的资源。模型动辄几十 MB，能当场释放的不要等进程退出。"""

    def is_loaded(self) -> bool:
        """引擎是否已就绪、可以直接识别。"""
        return self.is_available()

    def status_text(self) -> str:
        """get_memory_status() 用的一行状态描述。"""
        return "已初始化" if self.is_loaded() else "未初始化"

    # ── 图像转换助手（各引擎通用）──────────────────────

    @staticmethod
    def _as_image(pixmap) -> QImage:
        """QPixmap 搬到 CPU；本来就是 QImage 的原样返回。"""
        return pixmap if isinstance(pixmap, QImage) else pixmap.toImage()

    @classmethod
    def rgb_bytes(cls, pixmap) -> Optional[Tuple[bytes, int, int, int]]:
        """转成 RGB888 裸字节 ``(data, w, h, stride)``，给直接吃像素的引擎用。

        图像为空时返回 None，调用方按「没识别到内容」处理。
        """
        image = cls._as_image(pixmap)
        if image.isNull():
            return None
        if image.format() != QImage.Format.Format_RGB888:
            image = image.convertToFormat(QImage.Format.Format_RGB888)
        w, h, stride = image.width(), image.height(), image.bytesPerLine()
        return bytes(image.constBits()[: stride * h]), w, h, stride

    @classmethod
    def png_bytes(cls, pixmap) -> bytes:
        """转成 PNG 字节，给需要自包含图片的引擎用（跨进程、走文件接口）。

        编解码不便宜，只在引擎确实需要时用；同进程吃裸像素的走 rgb_bytes()。
        """
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        cls._as_image(pixmap).save(buffer, "PNG")
        data = buffer.data().data()
        buffer.close()
        return data


# ── 结果组装 ────────────────────────────────────────────
# 三个 return_format 的语义由这里单点定义，引擎只管产出 [[box, text, score], ...]。

def format_result(rows: List, return_format: str, elapse: float) -> Any:
    """把 ``[[box, text, score], ...]`` 组装成调用方要的格式。"""
    if return_format == "text":
        texts = [item[1] for item in rows if len(item) > 1]
        return "\n".join(texts) if texts else "[未识别到文字]"

    if return_format == "list":
        return [item[1] for item in rows if len(item) > 1]

    if return_format == "dict":
        data = []
        for item in rows:
            if len(item) < 2:
                continue
            box = item[0]
            # 有些引擎（numpy 后端）会给出 ndarray，转成普通列表再交给下游
            if hasattr(box, "tolist"):
                box = box.tolist()
            data.append({
                "box": box,
                "text": item[1],
                "score": item[2] if len(item) > 2 else 0.0,
            })
        return {"code": 100, "msg": "成功", "data": data, "elapse": elapse}

    return rows


def empty_result(return_format: str) -> Any:
    """没识别到文字时的返回值。"""
    if return_format == "text":
        return "[未识别到文字]"
    if return_format == "list":
        return []
    if return_format == "dict":
        return {"code": 100, "msg": "未识别到文字", "data": [], "elapse": 0.0}
    return None


def error_result(return_format: str, message: Optional[str] = None) -> Any:
    """识别失败时的返回值。message 为空时给一句通用说明。"""
    msg = message or "OCR 不可用"
    if return_format == "text":
        return f"[错误] {msg}"
    if return_format == "list":
        return []
    if return_format == "dict":
        return {"code": -1, "msg": msg, "data": [], "elapse": 0.0}
    return None
