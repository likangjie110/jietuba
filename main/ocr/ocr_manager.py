# -*- coding: utf-8 -*-
"""
ocr_manager.py - OCR 管理器

职责只有三件事：**持有引擎、选引擎、把调用转发给引擎**。

引擎实现见 engines.py（内建，构建期变体开关 OCR_VARIANT 也在那里）或任何实现了
engine.OcrEngine 的外部包，接口见 engine.py。本模块不认识任何具体扩展——新增引擎
不用改这里，注册进来即可：内建的在 builtin_engines() 里，外部插件由 plugins.py 从
plugins/ 目录发现，也可以直接调 register_engine()。

对外 API（recognize_text / is_ocr_available / initialize_ocr / set_ocr_engine ...）
保持不变，调用方（钉图文字层、翻译、设置页）不受引擎增减影响。

主要功能:
- 识别截图区域的文字
- 单例模式管理引擎，支持多引擎切换
"""
import traceback as _tb
from typing import Any, Dict, List, Optional

from PySide6.QtGui import QPixmap

from core.logger import T

from .engine import OcrEngine, error_result, ocr_log
from .engines import builtin_engines
from .plugins import discover_plugins


class OCRManager:
    """OCR 管理器 - 单例，持有一组引擎并转发调用"""

    # 引擎类型常量。取值就是各引擎的 name，也是 set_ocr_engine() 的入参。
    ENGINE_WINDOS_OCR = "windos_ocr"          # Windows 高精度引擎 (Rust FFI)
    ENGINE_WINDOWS_OCR = "windows_media_ocr"  # Windows Media OCR (轻量级)
    ENGINE_PP_RUST = "ppocr_rust"             # Rust + ort 的 PP-OCR (默认)

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._initialized = True
            self._last_error = None
            self._current_engine: Optional[str] = None
            self._engines: Dict[str, OcrEngine] = {}
            for engine in builtin_engines():
                self.register_engine(engine)
            # 插件后注册：同名时插件覆盖内建，用户放进来的东西说了算
            discover_plugins(self)

    # ── 引擎注册表 ────────────────────────────────────

    def register_engine(self, engine: OcrEngine) -> None:
        """登记一个引擎，同名覆盖。

        外部引擎包在这里接入——注册之后它和内建引擎没有任何区别。
        """
        self._engines[engine.name] = engine

    def get_engine(self, name: str) -> Optional[OcrEngine]:
        """按 id 取引擎实例（未注册返回 None）。"""
        return self._engines.get(name)

    @property
    def is_available(self) -> bool:
        """任一引擎可用即视为 OCR 可用。"""
        return any(engine.is_available() for engine in self._engines.values())

    def get_available_engines(self) -> List[str]:
        """当前可用的引擎 id，顺序与注册顺序一致。"""
        return [name for name, engine in self._engines.items() if engine.is_available()]

    def set_engine(self, engine_type: str) -> bool:
        """切换当前使用的引擎。引擎未注册或不可用时返回 False，且不改动现状。"""
        engine = self._engines.get(engine_type)
        if engine is None:
            ocr_log(T("不支持的引擎类型: {engine_type}", engine_type=engine_type), "ERROR")
            return False
        if not engine.is_available():
            ocr_log(T("引擎不可用: {engine_type}", engine_type=engine_type), "WARN")
            return False

        if self._current_engine != engine_type:
            ocr_log(T("切换引擎: {old} -> {new}", old=self._current_engine, new=engine_type))
            self._current_engine = engine_type
            ocr_log(T("使用 {engine} 引擎 ({label})", engine=engine.name, label=engine.label))
        return True

    def get_current_engine(self) -> Optional[str]:
        """获取当前使用的引擎类型"""
        return self._current_engine

    # ── 初始化与识别 ──────────────────────────────────

    def initialize(self, language: str = "日本語", engine_type: Optional[str] = None) -> bool:
        """初始化当前引擎（未指定则以注册顺序取第一个可用的）。"""
        if not self.is_available:
            self._last_error = "没有可用的 OCR 引擎"
            return False

        if engine_type:
            self.set_engine(engine_type)

        if not self._current_engine:
            available = self.get_available_engines()
            if not available:
                self._last_error = "没有可用的 OCR 引擎"
                return False
            # 注册顺序即优先级：内建引擎的先后由 engines.builtin_engines() 决定
            self._current_engine = available[0]
            ocr_log(T("自动选择引擎: {engine}", engine=self._current_engine))

        engine = self._engines[self._current_engine]
        ok = engine.initialize(language)
        if not ok:
            self._last_error = engine.last_error or f"{self._current_engine} 初始化失败"
        return ok

    def recognize_pixmap(self, pixmap: QPixmap, return_format: str = "dict") -> Any:
        """识别 QPixmap 图像中的文字。

        Args:
            pixmap: QPixmap / QImage
            return_format: 返回格式 ("text", "list", "dict")

        Returns:
            识别结果（格式取决于 return_format，见 engine.py 的结果约定）
        """
        if not self._current_engine and not self.initialize():
            return error_result(return_format, self._last_error)

        engine = self._engines.get(self._current_engine)
        if engine is None:
            return error_result(return_format, f"引擎未注册: {self._current_engine}")

        try:
            return engine.recognize(pixmap, return_format)
        except Exception as e:
            # 引擎可能是第三方包：异常不该从 OCR 一路冒到 UI 线程
            self._last_error = str(e)
            ocr_log(T("OCR 引擎识别异常: {e}\n{tb}", e=str(e), tb=_tb.format_exc()), "ERROR")
            return error_result(return_format, self._last_error)

    # ── 状态与释放 ────────────────────────────────────

    def is_engine_loaded(self) -> bool:
        """当前引擎是否已初始化"""
        engine = self._engines.get(self._current_engine) if self._current_engine else None
        return bool(engine and engine.is_loaded())

    def get_memory_status(self) -> str:
        """当前引擎状态（供设置页的开发者信息显示）"""
        engine = self._engines.get(self._current_engine) if self._current_engine else None
        return engine.status_text() if engine else "未初始化"

    def get_last_error(self) -> str:
        """获取最后一次错误信息"""
        return self._last_error or "无错误"

    def close(self):
        """关闭 OCR 引擎"""
        self.release_engine()

    def release_engine(self):
        """释放所有引擎的资源，并清空当前选择。

        释放的是全部引擎而不是只有当前那个：引擎实例可能是在切换前构建的，
        留着就白白占住模型内存。高精度引擎是 Rust 侧的进程级单例，其 release()
        只能重置 Python 侧状态。
        """
        try:
            for engine in self._engines.values():
                engine.release()
            self._current_engine = None
            ocr_log(T("OCR 管理器状态已重置"))
        except Exception as e:
            ocr_log(T("释放 OCR 资源时出错: {e}", e=e), "WARN")


# 全局单例实例
_ocr_manager = OCRManager()


def is_ocr_available() -> bool:
    """检查 OCR 功能是否可用（任一引擎可用即为可用）"""
    return _ocr_manager.is_available


def get_available_engines() -> list:
    """获取可用的 OCR 引擎列表"""
    return _ocr_manager.get_available_engines()


def set_ocr_engine(engine_type: str) -> bool:
    """设置当前使用的 OCR 引擎"""
    return _ocr_manager.set_engine(engine_type)


def get_current_engine() -> Optional[str]:
    """获取当前使用的 OCR 引擎"""
    return _ocr_manager.get_current_engine()


def initialize_ocr(language: str = "日本語", engine_type: Optional[str] = None) -> bool:
    """初始化 OCR 引擎（未指定引擎时按注册顺序自动选择）"""
    return _ocr_manager.initialize(language, engine_type)


def recognize_text(pixmap: QPixmap, **kwargs) -> Any:
    """识别图像中的文字

    Args:
        pixmap: QPixmap / QImage
        **kwargs: 其他参数（return_format）
    """
    return _ocr_manager.recognize_pixmap(pixmap, **kwargs)


def release_ocr_engine():
    """释放 OCR 引擎，回收内存

    建议在以下场景调用：
    - 钉图窗口关闭后
    - 长时间不使用 OCR 时
    - 应用切换到后台时
    """
    _ocr_manager.release_engine()


def get_ocr_memory_status() -> str:
    """获取 OCR 引擎内存状态"""
    return _ocr_manager.get_memory_status()


def format_ocr_result_text(result: dict, separator: str = "\n") -> str:
    """
    格式化 OCR 结果为阅读顺序文本
    
    智能处理：
    - 按 Y 坐标分行（从上到下）
    - 同一行内按 X 坐标排序（从左到右）
    - 同行文字用空格连接，不同行用 separator 分隔
    
    Args:
        result: OCR 识别结果（dict 格式，包含 code 和 data 字段）
        separator: 行之间的分隔符，默认换行
        
    Returns:
        格式化后的文本字符串
    
    使用示例:
        result = recognize_text(pixmap, return_format="dict")
        text = format_ocr_result_text(result)
    """
    if not result or not isinstance(result, dict):
        return ""
    
    if result.get('code') != 100:
        return ""
    
    data = result.get('data', [])
    if not data:
        return ""
    
    if len(data) == 1:
        return data[0].get('text', '')
    
    # 收集每个文字块的位置信息
    items_with_pos = []
    for item in data:
        box = item.get('box', [])
        text = item.get('text', '')
        if not box or not text:
            continue
        
        # box 格式: [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
        # 计算中心Y和高度
        y_coords = [pt[1] for pt in box if len(pt) >= 2]
        if not y_coords:
            continue
        
        min_y = min(y_coords)
        max_y = max(y_coords)
        center_y = (min_y + max_y) / 2
        height = max_y - min_y
        
        # 计算左边X（用于同行内排序）
        x_coords = [pt[0] for pt in box if len(pt) >= 2]
        left_x = min(x_coords) if x_coords else 0
        
        items_with_pos.append({
            'text': text,
            'center_y': center_y,
            'height': height,
            'left_x': left_x
        })
    
    if not items_with_pos:
        return ""
    
    # 计算行高容差
    avg_height = sum(b['height'] for b in items_with_pos) / len(items_with_pos)
    line_tolerance = avg_height * 0.8
    
    # 按Y坐标分行
    lines = []
    current_line = []
    current_line_y = None
    
    # 先按Y排序（从上到下）
    items_with_pos.sort(key=lambda x: x['center_y'])
    
    for block in items_with_pos:
        if current_line_y is None:
            current_line = [block]
            current_line_y = block['center_y']
        elif abs(block['center_y'] - current_line_y) <= line_tolerance:
            # 同一行
            current_line.append(block)
        else:
            # 新的一行：先将当前行按X排序后输出
            current_line.sort(key=lambda x: x['left_x'])
            lines.append(" ".join(b['text'] for b in current_line))
            current_line = [block]
            current_line_y = block['center_y']
    
    # 别忘了最后一行
    if current_line:
        current_line.sort(key=lambda x: x['left_x'])
        lines.append(" ".join(b['text'] for b in current_line))
    
    return separator.join(lines)
