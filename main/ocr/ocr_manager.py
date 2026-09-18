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


def format_ocr_result_text(result: dict, separator: str = "\n", layout: str = "auto",
                           punctuation: str = "none") -> str:
    """
    格式化 OCR 结果为阅读顺序文本
    
    智能处理：
    - 按 Y 坐标分行（从上到下）
    - 同一行内按 X 坐标排序（从左到右）
    - 同行文字用空格连接，不同行用 separator 分隔
    
    Args:
        result: OCR 识别结果（dict 格式，包含 code 和 data 字段）
        separator: 行之间的分隔符，默认换行
        layout: 文本布局（``core.settings`` 的 OCR_TEXT_LAYOUTS）：
                auto 智能分行（默认）/ lines 每个文字块一行 / single 全部并成一行
        punctuation: 标点处理（OCR_PUNCTUATIONS）：
                     none 原样 / strip_trailing 去掉行尾标点 / to_halfwidth 全角转半角
        
    Returns:
        格式化后的文本字符串
    
    使用示例:
        result = recognize_text(pixmap, return_format="dict")
        text = format_ocr_result_text(result)
    """
    rows = _ocr_result_rows(result)
    if not rows:
        return ""

    if layout == "lines":
        # 每个文字块单独一行：表格/表单一类「每格独立」的图，合并同行反而读不清
        blocks = [block['text'] for row in rows for block in row]
        text = separator.join(blocks)
    elif layout == "single":
        # 全部并成一行：喂给需要单行输入的场合（搜索框、命令行）
        blocks = [block['text'] for row in rows for block in row]
        text = " ".join(blocks)
    else:
        text = separator.join(" ".join(block['text'] for block in row) for row in rows)

    return apply_ocr_punctuation(text, punctuation)


def _ocr_result_rows(result: dict) -> List[List[dict]]:
    """把 OCR 结果按「阅读顺序」聚成行：行内按 x 排序，行间按 y 排序。

    返回 ``[[{'text', 'left_x', 'center_y'}, ...], ...]``；结果不可用时返回空列表。
    """
    if not result or not isinstance(result, dict):
        return []
    if result.get('code') != 100:
        return []

    items_with_pos = []
    for item in result.get('data', []) or []:
        if not isinstance(item, dict):
            continue
        box = item.get('box', [])
        text = item.get('text', '')
        if not box or not text:
            continue

        y_coords = [pt[1] for pt in box if len(pt) >= 2]
        x_coords = [pt[0] for pt in box if len(pt) >= 2]
        if not y_coords or not x_coords:
            continue

        min_y, max_y = min(y_coords), max(y_coords)
        items_with_pos.append({
            'text': text,
            'center_y': (min_y + max_y) / 2,
            'height': max_y - min_y,
            'left_x': min(x_coords),
        })

    if not items_with_pos:
        return []

    # 计算行高容差
    avg_height = sum(b['height'] for b in items_with_pos) / len(items_with_pos)
    line_tolerance = avg_height * 0.8

    rows: List[List[dict]] = []
    current_line: List[dict] = []
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
            rows.append(current_line)
            current_line = [block]
            current_line_y = block['center_y']

    # 别忘了最后一行
    if current_line:
        current_line.sort(key=lambda x: x['left_x'])
        rows.append(current_line)

    return rows


#: 全角 → 半角：全角区间（FF01-FF5E）与 ASCII 相差 0xFEE0
_HALFWIDTH_TABLE = str.maketrans(
    {chr(code): chr(code - 0xFEE0) for code in range(0xFF01, 0xFF5F)}
)

#: 「行尾标点」——找的是一行末尾的标点，中英文都要认
_TRAILING_PUNCTUATION = "，。、；：？！,.;:?!"


def apply_ocr_punctuation(text: str, punctuation: str) -> str:
    """按设置处理识别结果里的标点。

    - ``none``：原样
    - ``strip_trailing``：去掉每行末尾的标点（OCR 常把图片边缘的噪点认成句号逗号）
    - ``to_halfwidth``：全角转半角（中英混排时喂给其它程序更省事）
    """
    if not text or punctuation == "none":
        return text
    if punctuation == "to_halfwidth":
        return text.translate(_HALFWIDTH_TABLE)
    if punctuation == "strip_trailing":
        return "\n".join(
            line.rstrip().rstrip(_TRAILING_PUNCTUATION).rstrip()
            for line in text.split("\n")
        )
    return text


def resolve_ocr_language(configured: str, app_language: str = "") -> str:
    """把「识别语言」设置换成引擎认的语言名。

    ``follow_app``（缺省）跟随界面语言；界面语言也不认识时用日语——发行版的识别模型
    是按中日英混排调的，日语档最接近。
    """
    mapping = {"zh": "简体中文", "en": "English", "ja": "日本語", "ko": "한국어"}
    key = (configured or "").strip().lower()
    if key in mapping:
        return mapping[key]
    return mapping.get((app_language or "").strip().lower(), "日本語")


# ── 表格 → Markdown ──────────────────────────────────────────────
#: 表格判定的阈值，全部按「字高的比例」给，避免写死像素值：
#: 同一行的 y 容差沿用行聚类那一档；行内间隔超过这个倍数就当作换了一列。
TABLE_LINE_TOLERANCE = 0.8
TABLE_COLUMN_GAP = 0.8
TABLE_MIN_ROWS = 2          # 表头 + 至少一行数据
TABLE_MIN_COLS = 2


def _ocr_boxes(result: dict) -> List[dict]:
    """把 OCR 结果里的文字块连同几何信息取出来（保持引擎给的顺序）。

    box 是四点坐标 ``[[x1,y1], …]``；没有坐标或没有文字的块直接跳过——没有几何就
    没法判列，硬凑只会把普通段落拆错。
    """
    data = result.get('data', []) if isinstance(result, dict) else []
    boxes = []
    for item in data:
        if not isinstance(item, dict):
            continue
        points = [pt for pt in (item.get('box') or [])
                  if isinstance(pt, (list, tuple)) and len(pt) >= 2]
        text = str(item.get('text') or '').strip()
        if not points or not text:
            continue
        xs = [float(pt[0]) for pt in points]
        ys = [float(pt[1]) for pt in points]
        boxes.append({
            'text': text,
            'left': min(xs),
            'right': max(xs),
            'center_y': (min(ys) + max(ys)) / 2.0,
            'height': max(ys) - min(ys),
        })
    return boxes


def _group_ocr_rows(boxes: List[dict]) -> List[List[dict]]:
    """按 y 聚类成行；行内按 x 从左到右。"""
    if not boxes:
        return []

    avg_height = sum(b['height'] for b in boxes) / len(boxes)
    tolerance = avg_height * TABLE_LINE_TOLERANCE

    rows: List[List[dict]] = []
    current: List[dict] = []
    current_y = None
    for box in sorted(boxes, key=lambda b: b['center_y']):
        if current_y is None:
            current, current_y = [box], box['center_y']
        elif abs(box['center_y'] - current_y) <= tolerance:
            current.append(box)
        else:
            rows.append(sorted(current, key=lambda b: b['left']))
            current, current_y = [box], box['center_y']
    if current:
        rows.append(sorted(current, key=lambda b: b['left']))
    return rows


def _split_ocr_columns(row: List[dict], avg_height: float) -> List[str]:
    """把一行里的文字块按 x 间隙切成单元格（间隙小的当同一格，用空格连接）。"""
    gap_limit = max(avg_height * TABLE_COLUMN_GAP, 1.0)
    cells = [row[0]['text']]
    for previous, box in zip(row, row[1:]):
        if box['left'] - previous['right'] > gap_limit:
            cells.append(box['text'])
        else:
            cells[-1] = f"{cells[-1]} {box['text']}"
    return cells


def _markdown_cell(text: str) -> str:
    """单元格文本：竖线要转义、换行折成空格，否则表格结构就断了。"""
    return " ".join(str(text).split()).replace("|", "\\|")


def _pad_cells(cells: List[str], width: int) -> List[str]:
    return list(cells[:width]) + [""] * (width - len(cells))


def format_ocr_result_markdown(result: dict, layout: str = "auto",
                               punctuation: str = "none") -> str:
    """把带坐标的 OCR 结果渲染成 Markdown 表格；没有表格特征时退回纯文本。

    判定完全靠几何：先按 y 聚类成行、行内按 x 间隙切列，然后要求「至少 2 行、其中
    至少 2 行是多列」才认成表格。只满足一半（例如普通段落里的缩进、两行各一个词）
    时宁可退回 ``format_ocr_result_text`` 的纯文本，也不硬造一张表——这时
    ``layout`` / ``punctuation`` 会原样传给那条纯文本路径，设置里的选项照样生效。
    """
    plain = format_ocr_result_text(result, layout=layout, punctuation=punctuation)
    boxes = _ocr_boxes(result)
    if len(boxes) < TABLE_MIN_ROWS:
        return plain

    rows = _group_ocr_rows(boxes)
    if len(rows) < TABLE_MIN_ROWS:
        return plain

    avg_height = sum(b['height'] for b in boxes) / len(boxes)
    cells_per_row = [_split_ocr_columns(row, avg_height) for row in rows]
    widths = [len(cells) for cells in cells_per_row]

    if sum(1 for width in widths if width >= TABLE_MIN_COLS) < TABLE_MIN_ROWS:
        return plain

    # 表宽取众数，并列时取更宽的：OCR 少认一格时补空，多认一格时截掉，
    # 都比整张表退回纯文本更接近用户想要的
    table_width = max(set(widths), key=lambda width: (widths.count(width), width))
    if table_width < TABLE_MIN_COLS:
        return plain

    header, body = cells_per_row[0], cells_per_row[1:]
    lines = [
        "| " + " | ".join(_markdown_cell(c) for c in _pad_cells(header, table_width)) + " |",
        "| " + " | ".join("---" for _ in range(table_width)) + " |",
    ]
    lines += [
        "| " + " | ".join(_markdown_cell(c) for c in _pad_cells(row, table_width)) + " |"
        for row in body
    ]
    return "\n".join(lines)
