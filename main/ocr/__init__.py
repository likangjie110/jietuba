"""
OCR 模块 - 文字识别功能

内建引擎（engines.py，由构建期开关 OCR_VARIANT 决定注册哪些）：
- ppocr_rust: Rust + ONNX Runtime 的 PP-OCR，默认发行版唯一引擎，需 models/ 下的 det/rec 模型
- windos_ocr: 高精度引擎，经 Rust FFI 调用系统组件（"win" 变体）
- windows_media_ocr: Windows 系统自带 OCR API，轻量级（"win" 变体，托底用）

引擎是可插拔的：实现 engine.OcrEngine 后 register_engine() 注册即可，见 engine.py
与 OCRManager.register_engine。

主要功能：
- OCRManager: OCR 管理器（单例模式）
- is_ocr_available: 检查 OCR 是否可用
- get_available_engines: 获取可用的 OCR 引擎列表
- set_ocr_engine: 设置当前使用的 OCR 引擎
- get_current_engine: 获取当前使用的 OCR 引擎
- initialize_ocr: 初始化 OCR 引擎
- recognize_text: 识别图像中的文字

注意：OCRTextLayer（钉图文字选择层）已移至 pin 模块

使用示例：
    from ocr import is_ocr_available, get_available_engines, set_ocr_engine, initialize_ocr, recognize_text

    if is_ocr_available():
        engines = get_available_engines()
        print(f"可用引擎: {engines}")

        set_ocr_engine("ppocr_rust")  # 切换引擎
        initialize_ocr()
        result = recognize_text(pixmap, return_format="text")
        print(result)
"""

from .engine import OcrEngine
from .ocr_manager import (
    OCRManager,
    is_ocr_available,
    get_available_engines,
    set_ocr_engine,
    get_current_engine,
    initialize_ocr,
    recognize_text,
    release_ocr_engine,
    get_ocr_memory_status,
    format_ocr_result_text,
)

__all__ = [
    'OcrEngine',
    'OCRManager',
    'is_ocr_available',
    'get_available_engines',
    'set_ocr_engine',
    'get_current_engine',
    'initialize_ocr',
    'recognize_text',
    'release_ocr_engine',
    'get_ocr_memory_status',
    'format_ocr_result_text',
]
 