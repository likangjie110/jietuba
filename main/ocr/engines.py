# -*- coding: utf-8 -*-
"""
engines.py - 内建 OCR 引擎

OCR_VARIANT 是构建期开关，决定注册哪些内建引擎：

    "pp"  默认发行版：只带 ppocr_rust（自研 Rust + ONNX Runtime，开源合规、无外部依赖）
    "win" 借用 Windows 系统组件的变体：oneocr 高精度引擎 + Windows Media OCR 托底

每个引擎自己负责探测可用性（扩展能不能导入、模型在不在、系统组件找不找得到），
探测结果只算一次；OCRManager 只按 engine.OcrEngine 这个接口调用，不关心差异。

外部引擎不必写在这里——那是 OCRManager.register_engine() 的活。
"""
import ctypes
import importlib.util
import os
import sys
import threading
import time
import traceback as _tb
from typing import Any, List, Optional, Tuple

from PySide6.QtGui import QImage

from core.logger import T

from .engine import OcrEngine, empty_result, error_result, format_result, ocr_log

OCR_VARIANT: str = "pp"


# ══════════════════════════════════════════════════════════
# ppocr_rust（默认发行版引擎）
# ══════════════════════════════════════════════════════════

def _ppocr_model_paths() -> Tuple[Optional[str], Optional[str]]:
    """返回 (det_path, rec_path)；找不到则 (None, None)。

    查找顺序：
      1) 打包后：exe 同级目录的 models/（外置，启动快、可替换）
      2) 打包后回退：_MEIPASS/models/（若模型被打进包内）
      3) 开发环境：仓库根 models/
    """
    candidates = []
    try:
        if getattr(sys, "frozen", False):
            candidates.append(os.path.join(os.path.dirname(sys.executable), "models"))
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(os.path.join(meipass, "models"))
        else:
            from core.resource_manager import ResourceManager
            candidates.append(ResourceManager.get_resource_path("models"))
    except Exception as e:
        ocr_log(T("解析 ppocr_rust 模型路径失败: {e}", e=e), "DEBUG")

    for base in candidates:
        det = os.path.join(base, "PP-OCRv6_det_small.onnx")
        rec = os.path.join(base, "PP-OCRv6_rec_small.onnx")
        if os.path.exists(det) and os.path.exists(rec):
            return det, rec
    return None, None


def probe_ppocr_rust() -> Tuple[bool, Optional[str], Optional[str]]:
    """探测扩展与模型，返回 (是否可用, det 路径, rec 路径)。

    单独抽成模块级函数是为了留一个替换点：测试注入探测结果即可，不必真的装扩展、
    也不需要仓库里有模型文件。
    """
    try:
        has_extension = importlib.util.find_spec("ppocr_rust") is not None
        det, rec = _ppocr_model_paths() if has_extension else (None, None)
        available = bool(has_extension and det and rec)
        if available:
            ocr_log(T("ppocr_rust 引擎可用 (Rust + ort PP-OCR)"), "DEBUG")
        elif has_extension:
            ocr_log(T("ppocr_rust 已安装但缺少模型文件"), "DEBUG")
        else:
            ocr_log(T("ppocr_rust 未安装"), "DEBUG")
        return available, det, rec
    except Exception as e:
        ocr_log(T("ppocr_rust 引擎检测失败: {e}", e=e), "DEBUG")
        return False, None, None


class PpOcrRustEngine(OcrEngine):
    """ppocr_rust —— Rust + ONNX Runtime 的 PP-OCR（det + rec 两个模型）。

    推理在 Rust 侧用 allow_threads 放开 GIL，跑在原生线程，不拖慢 UI。
    """

    name = "ppocr_rust"
    label = "Rust + ort PP-OCR"

    def __init__(self):
        super().__init__()
        self._engine = None          # ppocr_rust.Engine 实例，None 即未初始化
        self._init_lock = threading.Lock()
        self._probe: Optional[Tuple[bool, Optional[str], Optional[str]]] = None

    def _probe_once(self) -> Tuple[bool, Optional[str], Optional[str]]:
        if self._probe is None:
            self._probe = probe_ppocr_rust()
        return self._probe

    def is_available(self) -> bool:
        return self._probe_once()[0]

    def initialize(self, language: Optional[str] = None) -> bool:
        if not self.is_available():
            self.last_error = "ppocr_rust 引擎不可用"
            ocr_log(T("ppocr_rust 引擎不可用"), "WARN")
            return False
        if self._engine is not None:
            return True
        with self._init_lock:
            if self._engine is not None:
                return True
            det, rec = self._probe_once()[1:]
            try:
                ocr_log(T("正在初始化 ppocr_rust 引擎 (Rust + ort)..."), "DEBUG")
                import ppocr_rust
                self._engine = ppocr_rust.Engine(det, rec)
                ocr_log(T("ppocr_rust 引擎初始化成功"), "DEBUG")
                return True
            except Exception as e:
                self.last_error = f"ppocr_rust 初始化失败: {str(e)}"
                ocr_log(T("ppocr_rust 初始化失败: {e}\n{tb}", e=str(e), tb=_tb.format_exc()), "ERROR")
                self._engine = None
                return False

    def recognize(self, pixmap, return_format: str = "dict") -> Any:
        if not self.is_available():
            return error_result(return_format, "ppocr_rust 不可用")
        if self._engine is None and not self.initialize():
            return error_result(return_format, self.last_error)
        try:
            start_time = time.time()
            conv = self.rgb_bytes(pixmap)
            if conv is None:
                return empty_result(return_format)
            raw, w, h, stride = conv

            # 取到局部变量：release() 可能在别的线程把 self._engine 置空
            engine = self._engine
            if engine is None:
                return error_result(return_format, "ppocr_rust 引擎已释放")
            lines = engine.recognize(raw, w, h, stride)
            elapse = time.time() - start_time

            # ppocr_rust 返回 list[TextLine]，.points 是四个角点
            rows = []
            for line in lines or []:
                if not line.text:
                    continue
                rows.append([
                    [[float(x), float(y)] for x, y in line.points],
                    line.text,
                    float(line.score),
                ])
            if not rows:
                return empty_result(return_format)
            return format_result(rows, return_format, elapse)
        except Exception as e:
            self.last_error = f"ppocr_rust 识别失败: {str(e)}"
            ocr_log(T("ppocr_rust 识别失败: {e}\n{tb}", e=str(e), tb=_tb.format_exc()), "ERROR")
            return error_result(return_format, self.last_error)

    def release(self) -> None:
        # 引擎实例持有 det/rec 两个 ONNX 模型（约 30 MB），close() 后立即释放，
        # 不必再等进程退出
        if self._engine is not None:
            self._engine.close()
            self._engine = None

    def is_loaded(self) -> bool:
        return self._engine is not None

    def status_text(self) -> str:
        return "已初始化 (ppocr_rust Rust+ort 引擎)" if self._engine is not None else "未初始化"


# ══════════════════════════════════════════════════════════
# windows_media_ocr（"win" 变体的两个引擎，共用同一个扩展）
# ══════════════════════════════════════════════════════════

_wmo_module = None
_wmo_probed = False


def _windows_media_ocr():
    """加载 windows_media_ocr 扩展（只尝试一次），不可用返回 None。"""
    global _wmo_module, _wmo_probed
    if not _wmo_probed:
        _wmo_probed = True
        try:
            import windows_media_ocr
            _wmo_module = windows_media_ocr
        except ImportError as e:
            _wmo_module = None
            ocr_log(T("windows_media_ocr 库不可用: {e}", e=e), "DEBUG")
    return _wmo_module


def _windows_languages(module) -> list:
    """扩展报告的可用语言列表；取不到就返回空表（只为日志用，不能影响识别）。"""
    try:
        return module.get_available_languages()
    except Exception:
        return []


class OneOcrEngine(OcrEngine):
    """高精度引擎：通过 Rust FFI 调用系统组件（Windows ScreenSketch 那套）。

    引擎本体是 Rust 侧的 OnceLock 全局单例，生命周期与进程相同，release() 只能
    重置 Python 侧状态。
    """

    name = "windos_ocr"
    label = "Windows ScreenSketch OCR"

    def __init__(self):
        super().__init__()
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        module = _windows_media_ocr()
        if module is None:
            return False
        if self._available is None:
            try:
                self._available = bool(module.oneocr_available())
                if self._available:
                    ocr_log(T("高精度引擎可用 (Rust FFI)"), "INFO")
                else:
                    ocr_log(T("高精度引擎不可用 (系统组件未找到)"), "DEBUG")
            except Exception as e:
                self._available = False
                ocr_log(T("高精度引擎检测失败: {e}", e=e), "DEBUG")
        return self._available

    def initialize(self, language: Optional[str] = None) -> bool:
        module = _windows_media_ocr()
        if module is None or not self.is_available():
            self.last_error = "高精度引擎不可用"
            return False
        try:
            ocr_log(T("正在初始化高精度引擎 (Rust FFI)..."), "DEBUG")
            module.oneocr_initialize()
            ocr_log(T("高精度引擎初始化成功"), "DEBUG")
            return True
        except Exception as e:
            self.last_error = f"高精度引擎初始化失败: {str(e)}"
            ocr_log(T("高精度引擎初始化失败: {e}\n{tb}", e=str(e), tb=_tb.format_exc()), "ERROR")
            return False

    def recognize(self, pixmap, return_format: str = "dict") -> Any:
        try:
            start_time = time.time()

            image = self._as_image(pixmap)
            if image.isNull():
                return empty_result(return_format)
            # QImage 的 ARGB32 在小端机器上的内存布局就是 BGRA，Rust 侧按这个读
            if image.format() != QImage.Format.Format_ARGB32:
                image = image.convertToFormat(QImage.Format.Format_ARGB32)

            w, h, stride = image.width(), image.height(), image.bytesPerLine()
            total_bytes = stride * h
            # 像素拷进 ctypes buffer 再传地址：阻塞调用期间得保证这块内存活着
            raw_data = (ctypes.c_char * total_bytes).from_buffer_copy(
                bytes(image.bits()[:total_bytes])
            )
            result = _windows_media_ocr().oneocr_recognize_raw(
                ctypes.addressof(raw_data), w, h, stride
            )
            elapse = time.time() - start_time

            if not result or not result.get("lines"):
                return empty_result(return_format)

            rows = []
            for line in result["lines"]:
                if not line["text"]:
                    continue
                bbox = line["bounding_rect"]
                if bbox:
                    box = [
                        [bbox["x1"], bbox["y1"]],
                        [bbox["x2"], bbox["y2"]],
                        [bbox["x3"], bbox["y3"]],
                        [bbox["x4"], bbox["y4"]],
                    ]
                else:
                    box = [[0, 0], [100, 0], [100, 20], [0, 20]]
                # 扩展只给词级置信度，取平均；一个都没有时按 1.0 处理
                confidences = [
                    word["confidence"] for word in line.get("words", [])
                    if word.get("confidence") is not None
                ]
                score = sum(confidences) / len(confidences) if confidences else 1.0
                rows.append([box, line["text"], score])

            if not rows:
                return empty_result(return_format)
            return format_result(rows, return_format, elapse)
        except Exception as e:
            self.last_error = f"高精度引擎识别失败: {str(e)}"
            ocr_log(T("高精度引擎识别失败: {e}\n{tb}", e=str(e), tb=_tb.format_exc()), "ERROR")
            return error_result(return_format, self.last_error)

    def status_text(self) -> str:
        return "已初始化 (高精度引擎 Rust FFI)" if self.is_available() else "未初始化"


class WindowsMediaOcrEngine(OcrEngine):
    """Windows Media OCR：系统自带、无需模型文件，识别质量弱于高精度引擎。"""

    name = "windows_media_ocr"
    label = "Windows Media OCR"

    # 应用语言 -> windows_media_ocr 语言代码
    LANGUAGE_MAP = {
        "日本語": "ja",
        "Japanese": "ja",
        "ja": "ja",
        "中文": "zh-Hans-CN",
        "Chinese": "zh-Hans-CN",
        "zh": "zh-Hans-CN",
        "English": "en-US",
        "英语": "en-US",
        "en": "en-US",
    }

    def __init__(self):
        super().__init__()
        self._language: Optional[str] = None

    def is_available(self) -> bool:
        return _windows_media_ocr() is not None

    def initialize(self, language: Optional[str] = None) -> bool:
        module = _windows_media_ocr()
        if module is None:
            self.last_error = "windows_media_ocr 模块不可用"
            return False
        try:
            language = language or "日本語"
            self._language = self.LANGUAGE_MAP.get(language, "zh-Hans-CN")
            ocr_log(T(
                "初始化 windows_media_ocr 引擎(语言配置: {language} -> {ocr_lang})",
                language=language, ocr_lang=self._language,
            ))
            ocr_log(T("windows_media_ocr 引擎初始化成功"))
            langs = _windows_languages(module)
            if langs:
                ocr_log(T("Windows OCR 支持的语言: {langs}", langs=langs))
            return True
        except Exception as e:
            self.last_error = f"windows_media_ocr 初始化失败: {str(e)}"
            ocr_log(T("windows_media_ocr 初始化失败: {e}\n{tb}", e=str(e), tb=_tb.format_exc()), "ERROR")
            return False

    def recognize(self, pixmap, return_format: str = "dict") -> Any:
        if not self.is_available():
            return error_result(return_format, "windows_media_ocr 不可用")
        if not self._language and not self.initialize():
            return error_result(return_format, self.last_error)
        try:
            start_time = time.time()
            result = _windows_media_ocr().recognize_from_bytes(
                self.png_bytes(pixmap), language=self._language
            )
            elapse = time.time() - start_time

            if result is None or not result.text or not result.lines:
                return empty_result(return_format)

            rows = []
            for line in result.lines:
                bounds = line.bounds
                box = [
                    [bounds.x, bounds.y],
                    [bounds.x + bounds.width, bounds.y],
                    [bounds.x + bounds.width, bounds.y + bounds.height],
                    [bounds.x, bounds.y + bounds.height],
                ]
                # 这个扩展不返回置信度
                rows.append([box, line.text, 1.0])
            if not rows:
                return empty_result(return_format)
            return format_result(rows, return_format, elapse)
        except Exception as e:
            self.last_error = f"windows_media_ocr 识别失败: {str(e)}"
            ocr_log(T("windows_media_ocr 识别失败: {e}\n{tb}", e=str(e), tb=_tb.format_exc()), "ERROR")
            return error_result(return_format, self.last_error)

    def release(self) -> None:
        self._language = None

    def is_loaded(self) -> bool:
        return self._language is not None

    def status_text(self) -> str:
        if self._language:
            return f"已初始化 (windows_media_ocr 引擎, 语言: {self._language})"
        return "未初始化"


def builtin_engines() -> List[OcrEngine]:
    """本次构建注册的内建引擎。列表顺序 = 自动选择时的优先级。"""
    if OCR_VARIANT == "win":
        return [OneOcrEngine(), WindowsMediaOcrEngine()]
    return [PpOcrRustEngine()]
