# -*- coding: utf-8 -*-
"""动作注册表：应用里「能做哪些事」的唯一出处，以及全局鼠标手势的执行入口。

全局鼠标动作（修饰键 + 鼠标手势）从这里取动作表，后面的「快捷键/动作页」与托盘菜单
也会读同一张表。动作的具体入口仍留在原处（``MainApp`` / 截图窗口），这里只做
「id → 名称 → 怎么跑」的映射，免得每个入口各自维护一份动作清单。

三类动作：

- ``silent_capture``：**静默截图**——按「UI 检测」档位在鼠标位置抓一块，直接做后续
  处理（复制/贴图/快速保存/识别文字），不进截图编辑器；
- ``editor_mode``：打开截图编辑器，并且把检测到的区域设成选区后直接进入某个模式
  （翻译/长截图/GIF）。走既有入口而不是另写一套，行为才和用户手动点工具栏一致；
- 其余动作交给应用既有入口（截图编辑器、剪贴板窗口、翻译窗口）。
"""

from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

from core.logger import T, log_debug, log_exception, log_warning
from core.platform.window import UI_DETECTION_NONE

#: 打开编辑器后直接进入的模式（``Action.editor_mode``）
EDITOR_MODE_TRANSLATE = "translate"
EDITOR_MODE_LONG = "long"
EDITOR_MODE_GIF = "gif"
EDITOR_MODE_VIDEO = "video"

#: 不走截图链路、直接调应用入口的动作 id
APP_ENTRY_ACTIONS = ("clipboard", "open_translation", "pin_clipboard_text",
                     "open_save_folder", "translate_clipboard_image", "check_updates",
                     "open_history", "open_image_viewer", "open_main_window")


@dataclass(frozen=True)
class Action:
    """一个可绑定、可触发的动作。"""

    id: str
    label: str                      # 界面文案（英文源，界面用 tr() 翻译）
    silent_capture: bool = False    # 静默截图：不进编辑器
    editor_mode: str = ""           # 进编辑器后直接进入的模式（空 = 普通截图）
    tray: bool = False              # 默认是否出现在托盘菜单里


ACTIONS = (
    Action("screenshot", "Screenshot", tray=True),
    Action("screenshot_copy", "Screenshot and Copy", silent_capture=True, tray=True),
    Action("screenshot_pin", "Screenshot and Pin", silent_capture=True),
    Action("screenshot_quick_save", "Screenshot and Save", silent_capture=True, tray=True),
    Action("screenshot_copy_text", "Screenshot and Copy Text", silent_capture=True),
    Action("copy_table_markdown", "Copy Table as Markdown", silent_capture=True),
    Action("recognize_table", "Table Recognition", silent_capture=True, tray=True),
    Action("auto_recognize", "Auto Recognize", silent_capture=True, tray=True),
    Action("convert_image_markdown", "Convert Image to Markdown", silent_capture=True,
           tray=True),
    Action("read_image_ai", "Read Image with AI", silent_capture=True, tray=True),
    Action("solve_problem", "Solve Problem", silent_capture=True),
    Action("translate_image_in_place", "Translate Image in Place", silent_capture=True,
           tray=True),
    Action("mask_sensitive_info", "Mask Sensitive Info", silent_capture=True, tray=True),
    Action("extract_colors", "Extract Palette", silent_capture=True, tray=True),
    Action("beautify_export", "Beautify and Export", silent_capture=True, tray=True),
    Action("recognize_formula", "Recognize Formula as LaTeX", silent_capture=True),
    Action("screenshot_translate", "Screenshot and Translate",
           editor_mode=EDITOR_MODE_TRANSLATE),
    Action("long_screenshot", "Long Screenshot", editor_mode=EDITOR_MODE_LONG, tray=True),
    Action("gif_capture", "GIF Capture", editor_mode=EDITOR_MODE_GIF, tray=True),
    Action("video_capture", "Video Recording", editor_mode=EDITOR_MODE_VIDEO, tray=True),
    Action("clipboard", "Clipboard", tray=True),
    Action("open_history", "Screenshot History", tray=True),
    Action("open_image_viewer", "Image Viewer", tray=True),
    Action("open_main_window", "Main Window", tray=True),
    Action("open_translation", "Translation", tray=True),
    Action("pin_clipboard_text", "Pin Clipboard Text", tray=True),
    Action("translate_clipboard_image", "Translate Clipboard Image", tray=True),
    # 托盘常用入口：直接打开截图保存目录（找不到目录时用平台层兜底打开）
    Action("open_save_folder", "Open Save Folder", tray=True),
    # 检查更新：更新源是 GitHub 发布页（见 core/updates.py），当前只做「展示 + 打开」
    Action("check_updates", "Check for Updates"),
)

ACTIONS_BY_ID = {action.id: action for action in ACTIONS}

#: 全局鼠标手势能绑的动作（顺序即界面顺序）
GESTURE_ACTION_IDS = tuple(action.id for action in ACTIONS)


def _target_rect(app) -> list[int] | None:
    """鼠标位置的抓取范围（屏幕坐标）。

    按「UI 检测」档位取：element/window 档给出元素或窗口矩形，none 档或没有窗口枚举
    后端时返回 None（调用方按整屏处理）。
    """
    from PySide6.QtGui import QCursor

    from core.platform import window as platform_window

    try:
        mode = app.config_manager.get_ui_detection()
        margin = app.config_manager.get_ui_detection_margin()
    except Exception:
        return None

    if mode == UI_DETECTION_NONE or not platform_window.is_window_enumeration_available():
        return None

    try:
        finder = platform_window.WindowFinder()
        finder.find_windows()
        cursor = QCursor.pos()
        return finder.find_rect_at_point(cursor.x(), cursor.y(), mode=mode, margin=margin)
    except Exception as e:
        log_exception(e, T("确定鼠标位置的抓取范围"))
        return None


def capture_at_cursor(app):
    """按 UI 检测档位静默抓一块屏幕；返回 ``(QImage, QRectF)`` 或 None。

    ``QRectF`` 是这块区域在屏幕上的位置（钉图要按它定位）。
    """
    from PySide6.QtCore import QRectF

    try:
        from capture.capture_service import CaptureService

        image, virtual_rect = CaptureService().capture_all_screens()
    except Exception as e:
        log_exception(e, T("静默截屏"))
        return None

    if image is None or image.isNull():
        return None

    rect = _target_rect(app)
    if not rect:
        return image, virtual_rect

    # 整屏图的原点不一定是 (0, 0)（多显示器可以有负坐标），要减掉虚拟桌面原点再裁
    x1 = int(rect[0] - virtual_rect.x())
    y1 = int(rect[1] - virtual_rect.y())
    width = int(rect[2] - rect[0])
    height = int(rect[3] - rect[1])
    if width <= 0 or height <= 0:
        return image, virtual_rect

    cropped = image.copy(x1, y1, width, height)
    if cropped.isNull():
        return image, virtual_rect
    log_debug(T("静默截屏: {width}x{height} @({x}, {y})",
                width=width, height=height, x=int(rect[0]), y=int(rect[1])), "Action")
    return cropped, QRectF(float(rect[0]), float(rect[1]), float(width), float(height))


def _copy(image) -> bool:
    from core.clipboard_utils import deliver_image_async

    return deliver_image_async(image) is not None


def _quick_save(image, app) -> bool:
    from core.clipboard_utils import deliver_image_async
    from core.save import SaveService

    config = app.config_manager
    paths = config.get_save_paths()
    thread = deliver_image_async(
        image,
        copy_to_clipboard=False,
        save_service=SaveService(config_manager=config),
        save_kwargs=dict(prefix="", paths=paths),
    )
    return thread is not None


def _pin(image, position, app) -> bool:
    from pin.pin_manager import PinManager

    pin_window = PinManager.instance().create_pin(
        image=image,
        position=position,
        config_manager=app.config_manager,
    )
    if pin_window is None:
        log_warning(T("钉图创建失败"), "Action")
        return False
    pin_window.show()
    return True


def _flash_capture_mask(rect, app) -> None:
    """按设置闪一下「截图遮罩」，让用户看到刚才抓的是哪一块。"""
    try:
        if not app.config_manager.get_mouse_capture_overlay_enabled():
            return
        from ui.capture_mask import flash_capture_mask

        log_debug(T("显示截图遮罩: {rect}", rect=rect), "Action")
        flash_capture_mask(rect)
    except Exception as e:
        log_exception(e, T("显示截图遮罩"))


def _record_history(image, rect, app) -> None:
    """把这次静默截图的结果记进历史（失败只记日志，不影响动作本身）。"""
    try:
        from history import record_screenshot

        record_screenshot(image, rect, getattr(app, "config_manager", None))
    except Exception as e:
        log_exception(e, T("记录截图历史"))


def _normalize_app_name(name: str) -> str:
    """比较用的程序名：去空白、忽略大小写、去掉 .app/.exe 后缀。"""
    normalized = str(name or "").strip().casefold()
    for suffix in (".app", ".exe"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


def gesture_ignored_app_name(app) -> str | None:
    """前台程序命中「忽略程序列表」时返回它的名字，否则 None。

    程序名是平台相关的（macOS 是本地化名、Windows 是可执行文件名），因此比较统一走
    ``_normalize_app_name``。列表里的名字由用户手输，只做这一个方向的宽容，不做子串
    匹配——「忽略 Finder」不代表「忽略 Finder 里的所有子窗口」这种暗示。
    """
    from core.platform import focus

    try:
        ignored = app.config_manager.get_mouse_ignored_apps()
    except Exception as e:
        log_exception(e, T("读取忽略程序列表"))
        return None
    if not ignored:
        return None

    name = focus.foreground_app_name()
    if not name:
        return None
    if _normalize_app_name(name) in {_normalize_app_name(item) for item in ignored}:
        return name
    return None


def _ocr_text_options() -> dict:
    """识别结果的处理选项：文本布局与标点处理（设置页「文字识别」组那两个下拉）。

    读配置失败时退回默认（智能分行、不动标点）：这两个选项只影响可读性，
    不该因为它们把整条复制路径带塌。
    """
    try:
        from settings import get_tool_settings_manager

        config = get_tool_settings_manager()
        threshold = config.get_ocr_low_confidence_threshold()
        vision_ready = False
        try:
            from ocr.vision_models import selected_model

            vision_ready = selected_model(config) is not None
        except Exception as e:
            log_exception(e, T("读取视觉模型配置"))
        return {
            "layout": config.get_ocr_text_layout(),
            "punctuation": config.get_ocr_punctuation(),
            "threshold": threshold,
            "vision_ready": vision_ready,
        }
    except Exception as e:
        log_exception(e, T("读取识别结果处理选项"))
        return {"layout": "auto", "punctuation": "none", "threshold": 0.6,
                "vision_ready": False}


class _OcrTextThread(QThread):
    """只做 OCR 的线程；持有 QImage（值类型），不碰任何 GUI 对象。

    ``converter`` 决定识别结果怎么变成文本：默认是阅读顺序的纯文本（按设置里的
    「文本布局」「标点处理」加工），表格那个动作传 ``format_ocr_result_markdown``
    （没有表格特征时它自己也会退回纯文本）。
    """

    #: (识别文本, 置信度提示)；提示为空串表示这次识别不用提醒
    recognized = Signal(str, str)

    def __init__(self, image, converter=None, options=None):
        super().__init__()
        self._image = image
        self._converter = converter
        self._options = dict(options or {})

    def _low_confidence_hint(self, result, options) -> str:
        """置信度偏低就返回一句提示；这里**不发任何网络请求**，只是建议改用视觉模型。"""
        from core.logger import log_info

        try:
            from ocr.quality import low_confidence_hint

            hint = low_confidence_hint(
                result,
                threshold=options.get("threshold", 0.6),
                vision_ready=bool(options.get("vision_ready")),
            )
        except Exception as e:
            log_exception(e, T("判断识别置信度"))
            return ""
        if hint:
            log_info(hint, "Action")
        return hint

    def run(self):
        try:
            from ocr import format_ocr_result_text, is_ocr_available, recognize_text

            converter = self._converter or format_ocr_result_text
            options = self._options or _ocr_text_options()
            # 文本格式化只吃 layout / punctuation；threshold 与 vision_ready 是给置信度
            # 判断用的，一起塞过去会被 format_ocr_result_text 拒掉
            format_options = {key: options[key] for key in ("layout", "punctuation")
                              if key in options}

            if not is_ocr_available():
                log_warning(T("OCR 不可用，无法识别截图文字"), "Action")
                self.recognized.emit("", "")
                return
            result = recognize_text(self._image, return_format="dict")
            text = ""
            hint = ""
            if isinstance(result, dict) and result.get("code") == 100:
                if self._converter is None:
                    text = (format_ocr_result_text(result, **format_options) or "").strip()
                else:
                    text = (converter(result) or "").strip()
                hint = self._low_confidence_hint(result, options)
            self.recognized.emit(text, hint)
        except Exception as e:
            log_exception(e, T("识别截图文字"))
            self.recognized.emit("", "")
        finally:
            self._image = None


class _OcrRawThread(QThread):
    """只做 OCR 的线程，把**原始结果**（带坐标）发给主线程。

    与 ``_OcrTextThread`` 的区别是它不转文本：表格编辑器要的是带坐标的结果，
    转成文本之后就再也切不出行列了。
    """

    recognized = Signal(object)

    def __init__(self, image):
        super().__init__()
        self._image = image

    def run(self):
        try:
            from ocr import is_ocr_available, recognize_text

            if not is_ocr_available():
                log_warning(T("OCR 不可用，无法识别截图文字"), "Action")
                self.recognized.emit(None)
                return
            self.recognized.emit(recognize_text(self._image, return_format="dict"))
        except Exception as e:
            log_exception(e, T("识别截图文字"))
            self.recognized.emit(None)
        finally:
            self._image = None


class _RawResultSink(QObject):
    """原始结果的接收端：表格识别完成后打开编辑器。"""

    def __init__(self, opener):
        super().__init__()
        self._opener = opener

    def on_result(self, result) -> None:
        if not isinstance(result, dict) or result.get("code") != 100:
            log_warning(T("未识别到可用的表格结果"), "Action")
            return
        self._opener(result)

    def on_finished(self) -> None:
        _ocr_thread_refs[:] = [ref for ref in _ocr_thread_refs if ref[1] is not self]


def _recognize_table(image) -> bool:
    """静默截取 → OCR → 打开可编辑表格窗口（表里能合并/拆分/复制粘贴，导出 Markdown/HTML）。"""
    from ocr.table_editor import open_table_editor

    thread = _OcrRawThread(image)
    sink = _RawResultSink(lambda result: _open_table_editor(result, open_table_editor))
    thread.recognized.connect(sink.on_result)
    thread.finished.connect(sink.on_finished)
    _ocr_thread_refs.append((thread, sink))
    thread.start()
    log_debug(T("开始识别表格文字"), "Action")
    return True


def _open_table_editor(result, opener) -> bool:
    """用识别结果建一张表并打开编辑器；没有表格特征时如实提示。"""
    from ocr.table_document import TableDocument

    try:
        document = TableDocument.from_ocr_result(result)
    except Exception as e:
        log_exception(e, T("从识别结果建表"))
        return False
    if document.rows == 0 or document.cols == 0:
        log_warning(T("识别结果里没有表格特征，未打开表格编辑器"), "Action")
        return False
    opener(document)
    return True


class _AutoRouteSink(QObject):
    """自动分流的接收端：拿到本地 OCR 结果后决定走哪条路。

    判断本身在 ``ocr.auto_route``（纯函数、可穷举测试），这里只负责把结论接到既有的
    实现上——每条路由都复用对应动作已经在用的函数，不另写一套识别。
    """

    #: (本地结果, 截图图像, 配置管理器)
    def __init__(self, image, config_manager):
        super().__init__()
        self._image = image
        self._config = config_manager

    def on_result(self, result) -> None:
        from ocr.auto_route import (
            ROUTE_FORMULA, ROUTE_TABLE, ROUTE_TEXT, ROUTE_VISION_DESCRIBE,
            ROUTE_VISION_TEXT, choose_route,
        )

        options = _ocr_text_options()
        route = choose_route(
            result,
            threshold=options.get("threshold", 0.6),
            has_formula=_formula_ready(),
            has_vision=bool(options.get("vision_ready")),
        )
        log_debug(T("自动分流结果: {route}", route=route), "Action")
        if route == ROUTE_TABLE:
            _recognize_table_from_result(result)
        elif route == ROUTE_TEXT:
            _copy_ocr_text_from_result(result, options)
        elif route == ROUTE_FORMULA:
            _recognize_formula(self._image)
        elif route == ROUTE_VISION_TEXT:
            recognize_image_with_vision(self._image, self._config,
                                        task="text")
        elif route == ROUTE_VISION_DESCRIBE:
            recognize_image_with_vision(self._image, self._config,
                                        task="general")
        else:
            _report_auto_route_miss(options)

    def on_finished(self) -> None:
        _ocr_thread_refs[:] = [ref for ref in _ocr_thread_refs if ref[1] is not self]


def _formula_ready() -> bool:
    """本机有没有可用的公式引擎（探测失败按「没有」处理）。"""
    try:
        from ocr.formula import is_formula_available

        return bool(is_formula_available())
    except Exception as e:
        log_exception(e, T("探测公式引擎可用性"))
        return False


def _recognize_table_from_result(result) -> bool:
    from ocr.table_editor import open_table_editor

    return _open_table_editor(result, open_table_editor)


def _copy_ocr_text_from_result(result, options) -> bool:
    """把本地 OCR 结果按设置排版后复制，并按需带上置信度提示。"""
    from ocr import format_ocr_result_text
    from ocr.quality import low_confidence_hint

    text = format_ocr_result_text(
        result,
        layout=options.get("layout", "auto"),
        punctuation=options.get("punctuation", "none"),
    )
    if not text:
        _report_auto_route_miss(options)
        return False

    _copy_to_clipboard_text(text, T("识别到的文字已复制到剪贴板（{count} 字）", count=len(text)))
    hint = ""
    try:
        hint = low_confidence_hint(result, threshold=options.get("threshold", 0.6),
                                   vision_ready=bool(options.get("vision_ready")))
    except Exception as e:
        log_exception(e, T("判断识别置信度"))
    from ocr.result_dialog import maybe_show_ocr_result

    maybe_show_ocr_result(text, "copy_all", hint=hint)
    return True


def _report_auto_route_miss(options) -> None:
    """什么都没识别到：如实说明，并告诉用户下一步能做什么（可操作，不是一句失败）。"""
    from core.i18n import make_tr
    from ui.dialogs import show_warning_dialog

    _tr = make_tr("AutoRecognize")
    message = (_tr("Nothing was recognized in this area. Configure a vision model in "
                   "the settings to have an AI read it.")
               if not options.get("vision_ready")
               else _tr("Nothing was recognized in this area."))
    log_warning(T("自动分流没有识别到内容"), "Action")
    show_warning_dialog(None, _tr("Auto Recognize"), message)


def _auto_recognize(image, app) -> bool:
    """静默截取 → 本地 OCR → 按内容自动分流（表格/文字/公式，最后才考虑视觉模型）。

    分流规则见 ``ocr.auto_route``：本地能取到结果就不联网，这既是成本考虑，也是
    「截图不该因为我们想图省事就上传」这条底线的落地。
    """
    thread = _OcrRawThread(image)
    sink = _AutoRouteSink(image, getattr(app, "config_manager", None))
    thread.recognized.connect(sink.on_result)
    thread.finished.connect(sink.on_finished)
    _ocr_thread_refs.append((thread, sink))
    thread.start()
    log_debug(T("开始自动识别并分流"), "Action")
    return True


def _convert_image_markdown(image, app) -> bool:
    """静默截取 → 交给配置的视觉模型转 Markdown/HTML → 复制并展示结果。"""
    return recognize_image_with_vision(image, getattr(app, "config_manager", None),
                                       task="table")


def _read_image_with_ai(image, app) -> bool:
    """静默截取 → 按**设置里选的任务模板**交给视觉模型 → 复制并展示结果。

    与「转 Markdown」那条的区别只有任务模板：那条固定按表格转，这条跟随用户在设置里
    选的模板（精确取字 / 代码解释 / 表格 / 公式 / 通用识图 / 解题）。
    """
    return recognize_image_with_vision(image, getattr(app, "config_manager", None))


def _solve_problem(image, app) -> bool:
    """静默截取 → 视觉模型的「解题」模板 → 复制并展示结果。

    单独一条动作而不是只留在设置的任务下拉里：解题是"看一眼就要答案"的用法，去设置里
    改模板再回来触发太绕。模板本身仍写在 ``vision_models.TASKS``（单一出处）。
    """
    return recognize_image_with_vision(image, getattr(app, "config_manager", None),
                                       task="solve")


def recognize_image_with_vision(image, config_manager=None, *, task=None,
                                show_window=False) -> bool:
    """共享实现：``task`` 为 None 时用配置里的任务模板，否则用指定的那个。

    ``show_window=True`` 用于「用户就是想在窗口里看结果」的入口（截图工具栏的 AI 解读）：
    那条路径不看去设置里配的弹窗时机，直接开窗。
    """
    from ocr.vision_models import DEFAULT_TASK, TASKS_BY_ID, convert_image, selected_model

    model = selected_model(config_manager)
    target = (config_manager.get_ocr_vision_target()
              if config_manager is not None else "markdown")
    if task is None:
        task = (config_manager.get_ocr_vision_task()
                if config_manager is not None else DEFAULT_TASK)
    if model is None:
        log_warning(T("没有配置可用的视觉模型，无法转换图片"), "Action")
        return False

    result = convert_image(image, model, target=target, task=task)
    if not result:
        log_warning(T("图片转换失败: {error}", error=result.error), "Action")
        return False

    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(result.text)
    log_debug(T("图片已按「{task}」转换为 {target}（{count} 字）",
                task=TASKS_BY_ID.get(task).label if task in TASKS_BY_ID else task,
                target=target, count=len(result.text)), "Action")

    # 按设置弹结果窗（默认不弹）：这里弹的是**可重跑**的视觉模型窗口，留着图与模板，
    # 用户能就地换个模板再读一次，不用回去重新截图
    from ocr.result_dialog import should_show

    if show_window or should_show("copy_all"):
        try:
            from ocr.vision_result_window import show_vision_result

            show_vision_result(image, model, task=task, target=target,
                               initial_text=result.text)
        except Exception as e:
            log_exception(e, T("显示视觉模型结果窗口"))
    return True


#: 落图结果先落在这里，查看器打开它（查看器支持「另存为」，产物因此可导出）
def _rendered_dir():
    from core.platform.paths import app_data_dir

    path = app_data_dir() / "translated"
    path.mkdir(parents=True, exist_ok=True)
    return path


class _ImageTranslationThread(QThread):
    """OCR → 逐行取译文 → 落图。全过程在工作线程里跑，主线程只收成品。

    网络与 OCR 都很慢（一次识别百毫秒级、一次翻译秒级），放在主线程会把界面卡住。
    行数超过上限时不逐行翻译，改用双语模式——那是一次请求，代价可接受。
    """

    rendered = Signal(object, str, int)

    def __init__(self, image, mode: str, params: dict):
        super().__init__()
        self._image = image
        self._mode = mode
        self._params = dict(params or {})

    def run(self):
        from ocr import is_ocr_available, recognize_text
        from translation.image_translation import (
            lines_from_ocr_result,
            render_translated_image,
            translate_lines,
        )

        try:
            if not is_ocr_available():
                log_warning(T("OCR 不可用，无法翻译截图"), "Action")
                self.rendered.emit(None, self._mode, 0)
                return
            result = recognize_text(self._image, return_format="dict")
            lines = lines_from_ocr_result(result)
            if not lines:
                log_warning(T("未识别到文字，无法落图翻译"), "Action")
                self.rendered.emit(None, self._mode, 0)
                return

            mode = self._mode
            failures = 0
            service = None
            try:
                from translation.service import create_default_translation_service

                service = create_default_translation_service(
                    getattr(self, "_config", None))
            except Exception as e:
                log_exception(e, T("创建翻译服务"))
            lines, failures = translate_lines(
                service, lines, self._params.get("target_lang") or "ZH",
                source_lang=self._params.get("source_lang"))
            if service is None or failures == len(lines):
                # 一行都没翻出来：落图没有内容可画，如实报出去
                self.rendered.emit(None, mode, 0)
                return

            translated = [line for line in lines if line.translation.strip()]
            if len(translated) < len(lines) and mode == "replace":
                # 替换模式需要每行都有译文，缺行的框只能保持原文；缺太多时退回双语，
                # 免得留下一张「一半中文一半原文」的图
                if len(translated) * 2 < len(lines):
                    log_warning(T("多数行没有译文，改用双语对照"), "Action")
                    mode = "bilingual"

            image = render_translated_image(self._image, lines, mode)
            self.rendered.emit(image, mode, len(translated))
        except Exception as e:
            log_exception(e, T("截图翻译落图"))
            self.rendered.emit(None, self._mode, 0)
        finally:
            self._image = None


class _ImageTranslationSink(QObject):
    """落图结果接收端：写文件、进剪贴板、打开查看器（主线程）。"""

    def on_rendered(self, image, mode: str, count: int) -> None:
        if image is None or image.isNull():
            log_warning(T("没有可展示的落图结果"), "Action")
            return
        path = ""
        try:
            from PySide6.QtCore import QDateTime

            stamp = QDateTime.currentDateTime().toString("yyyyMMdd_HHmmss_zzz")
            path = str(_rendered_dir() / f"translated_{stamp}.png")
            image.save(path, "PNG")
        except Exception as e:
            log_exception(e, T("保存落图结果"))
            path = ""

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setImage(image)
        log_debug(T("截图翻译落图完成（{mode}，{count} 行，{width}x{height}）",
                    mode=mode, count=count, width=image.width(), height=image.height()),
                  "Action")

        if path:
            try:
                from ui.image_viewer import open_image_viewer

                open_image_viewer(path=path)
            except Exception as e:
                log_exception(e, T("打开图片查看器"))

    def on_finished(self) -> None:
        _image_translation_refs[:] = [
            ref for ref in _image_translation_refs if ref[1] is not self]


#: 活着的落图线程与接收端（不持有引用的话线程对象会被回收）
_image_translation_refs: list = []


def _translation_image_mode(config_manager=None) -> str:
    """落图模式：优先用传进来的配置管理器（工具栏那条路径只有它），否则取全局单例。

    配置读不出来时按「双语对照」——它不遮挡原图，出错时代价最小。
    """
    try:
        manager = config_manager
        if manager is None:
            from settings import get_tool_settings_manager

            manager = get_tool_settings_manager()
        return manager.get_translation_image_mode()
    except Exception as e:
        log_exception(e, T("读取落图模式"))
        return "bilingual"


def translate_image_in_place(image, config_manager=None) -> bool:
    """截图 → OCR → 逐行翻译 → 按设置落图（替换/双语）→ 剪贴板 + 查看器。

    公开入口：静默动作与截图工具栏的「翻译并落图」按钮都走这里。只依赖 config_manager，
    不依赖 MainApp 实例——工具栏那条路径拿不到应用对象。
    """
    from PySide6.QtGui import QImage

    qimage = image if isinstance(image, QImage) else image.toImage()
    qimage = qimage.copy()
    if qimage.isNull():
        log_warning(T("截图内容为空，无法翻译并落图"), "Action")
        return False

    config = config_manager
    try:
        params = config.get_translation_request_params() if config is not None else {}
    except Exception as e:
        log_exception(e, T("读取翻译参数"))
        params = {}

    mode = _translation_image_mode(config)
    thread = _ImageTranslationThread(qimage, mode, params)
    thread._config = config
    sink = _ImageTranslationSink()
    thread.rendered.connect(sink.on_rendered)
    thread.finished.connect(sink.on_finished)
    _image_translation_refs.append((thread, sink))
    thread.start()
    log_debug(T("开始截图翻译落图（{mode}）", mode=mode), "Action")
    return True


class _PrivacyMaskThread(QThread):
    """OCR → 找敏感片段 → 打码；全过程在工作线程（识别慢，主线程不能等）。"""

    masked = Signal(object, int)

    def __init__(self, image):
        super().__init__()
        self._image = image

    def run(self):
        from ocr import is_ocr_available, recognize_text

        try:
            if not is_ocr_available():
                log_warning(T("OCR 不可用，无法自动遮挡"), "Action")
                self.masked.emit(None, 0)
                return
            result = recognize_text(self._image, return_format="dict")
            from core.privacy import mask_regions, mosaic_rectangles

            regions = mask_regions(result)
            if not regions:
                log_debug(T("没有识别到需要遮挡的敏感信息"), "Action")
                self.masked.emit(None, 0)
                return
            self.masked.emit(mosaic_rectangles(self._image, regions), len(regions))
        except Exception as e:
            log_exception(e, T("自动遮挡敏感信息"))
            self.masked.emit(None, 0)
        finally:
            self._image = None


class _PrivacyMaskSink(QObject):
    """遮挡结果接收端：进剪贴板（主线程）。"""

    def on_masked(self, image, count: int) -> None:
        if image is None or image.isNull() or not count:
            log_warning(T("没有可遮挡的内容"), "Action")
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setImage(image)
        log_debug(T("已遮挡 {count} 处敏感信息并复制到剪贴板", count=count), "Action")

    def on_finished(self) -> None:
        _privacy_refs[:] = [ref for ref in _privacy_refs if ref[1] is not self]


_privacy_refs: list = []


def mask_sensitive_info(image) -> bool:
    """截图 → OCR → 把手机号/邮箱/证件号/卡号打码 → 剪贴板。"""
    from PySide6.QtGui import QImage

    qimage = image if isinstance(image, QImage) else image.toImage()
    qimage = qimage.copy()
    if qimage.isNull():
        log_warning(T("截图内容为空，无法遮挡"), "Action")
        return False
    thread = _PrivacyMaskThread(qimage)
    sink = _PrivacyMaskSink()
    thread.masked.connect(sink.on_masked)
    thread.finished.connect(sink.on_finished)
    _privacy_refs.append((thread, sink))
    thread.start()
    log_debug(T("开始自动遮挡敏感信息"), "Action")
    return True


def beautify_export(image, config_manager=None) -> bool:
    """按设置加留白/背景/圆角/投影，并导出多个尺寸，同时把成品放进剪贴板。

    尺寸导出写进「截图保存目录」；一个尺寸都没写出来（比如原图比所有目标都窄）时，
    成品仍然会进剪贴板，并如实返回 False。
    """
    from PySide6.QtGui import QImage

    from core.beautify import apply_layout, export_sizes

    qimage = image if isinstance(image, QImage) else image.toImage()
    if qimage.isNull():
        log_warning(T("截图内容为空，无法美化"), "Action")
        return False

    style = {"margin": 24, "radius": 12, "shadow": 18, "background": "#FFFFFF",
             "widths": ()}
    if config_manager is not None:
        try:
            style = {
                "margin": config_manager.get_beautify_margin(),
                "radius": config_manager.get_beautify_radius(),
                "shadow": config_manager.get_beautify_shadow(),
                "background": config_manager.get_beautify_background(),
                "widths": config_manager.get_beautify_export_widths(),
            }
        except Exception as e:
            log_exception(e, T("读取美化设置"))

    decorated = apply_layout(qimage, margin=style["margin"], background=style["background"],
                             radius=style["radius"], shadow=style["shadow"])
    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setImage(decorated)

    saved = 0
    try:
        from core.save import SaveService

        service = SaveService(config_manager=config_manager)
        for width, scaled in export_sizes(decorated, style["widths"] or ()):
            path = service.save_qimage_async(scaled, prefix="美化", suffix=f"_{width}w")
            if path:
                saved += 1
        log_debug(T("美化导出完成: {count} 个尺寸", count=saved), "Action")
    except Exception as e:
        log_exception(e, T("美化导出"))
    return saved > 0


class _TextClipboardSink(QObject):
    """OCR 结果的接收端：把识别到的文字写进剪贴板（按设置决定是否再弹一次结果窗）。

    接收端必须是主线程的 QObject：信号连到普通函数时 Qt 走直连，回调会在 OCR 线程里
    跑，而剪贴板只能在主线程碰。
    """

    def on_text(self, text: str, hint: str = "") -> None:
        if not text:
            log_warning(T("未识别到文字"), "Action")
            return
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(text)
        log_debug(T("识别到的文字已复制到剪贴板（{count} 字）", count=len(text)), "Action")

        from ocr.result_dialog import maybe_show_ocr_result

        maybe_show_ocr_result(text, "copy_all", hint=hint)

    def on_finished(self) -> None:
        """线程跑完就放开引用（信号回到主线程，这里单线程改列表）。"""
        _ocr_thread_refs[:] = [ref for ref in _ocr_thread_refs if ref[1] is not self]


#: 活着的 OCR 线程与接收端。不持有引用的话线程对象会被回收，run() 还没跑完就崩。
_ocr_thread_refs: list = []


def _copy_text(image) -> bool:
    """静默截取 → OCR → 文字进剪贴板。

    识别放在线程里：一次 OCR 是百毫秒到秒级的 FFI 调用，在鼠标动作的主线程上跑会把
    界面卡住——这正是「全局鼠标动作必须立刻有反馈」最不能接受的部分。
    """
    return _ocr_to_clipboard(image, converter=None, log_message=T("开始识别截图文字"))


def _copy_table_markdown(image) -> bool:
    """静默截取 → OCR → 按表格渲染成 Markdown 进剪贴板。

    输入里没有表格特征时 ``format_ocr_result_markdown`` 自己会退回纯文本，所以这条
    动作在普通段落上也不会给出一个空结果或半张表。
    """
    from ocr import format_ocr_result_markdown

    return _ocr_to_clipboard(
        image, converter=format_ocr_result_markdown, log_message=T("开始识别表格文字")
    )


def _ocr_to_clipboard(image, converter, log_message) -> bool:
    """截图 → OCR → 文本进剪贴板（可选：再弹一次结果窗，见 ocr.result_dialog）。"""
    from PySide6.QtGui import QImage

    qimage = image if isinstance(image, QImage) else image.toImage()
    qimage = qimage.copy()
    if qimage.isNull():
        log_warning(T("截图内容为空，无法识别文字"), "Action")
        return False

    thread = _OcrTextThread(qimage, converter=converter)
    sink = _TextClipboardSink()
    thread.recognized.connect(sink.on_text)
    thread.finished.connect(sink.on_finished)
    _ocr_thread_refs.append((thread, sink))
    thread.start()
    log_debug(log_message, "Action")
    return True


def _configured_formula_engine() -> str:
    """设置的公式引擎名；读不到就用默认（Rust 侧 PP-FormulaNet 绑定）。"""
    try:
        from settings import get_tool_settings_manager

        return get_tool_settings_manager().get_formula_engine()
    except Exception as e:
        log_exception(e, T("读取公式引擎设置"))
        return "ppocr_formula"


def _recognize_formula(image) -> bool:
    """静默截取 → 公式引擎 → LaTeX 进结果窗口并复制到剪贴板。

    用设置里选的引擎（默认 Rust 侧 PP-FormulaNet 绑定）；它不可用但别的引擎可用时
    自动换一个并把这件事记进日志——比直接报「不可用」更接近用户想要的。全都不可用时
    **先提示再返回 False**：静默失败会让用户以为「图里没公式」。
    """
    from ocr import formula as formula_module
    from ocr.formula import is_formula_available, recognize_formula

    if not is_formula_available():
        log_warning(T("公式识别不可用：没有可用的公式引擎"), "Action")
        _show_formula_unavailable()
        return False

    engine_name = _configured_formula_engine()
    available = formula_module.available_formula_engines()
    if engine_name not in available:
        log_warning(
            T("设置的公式引擎不可用，改用 {engine}: {configured}",
              engine=available[0], configured=engine_name),
            "Action",
        )
        engine_name = available[0]

    latex = recognize_formula(image, engine_name=engine_name)
    if not latex:
        log_warning(T("未识别到公式"), "Action")
        return False

    _copy_to_clipboard_text(latex, T("公式已复制到剪贴板（{count} 字）", count=len(latex)))
    _show_formula_result(latex)
    return True


def _copy_to_clipboard_text(text: str, log_message) -> None:
    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)
    log_debug(log_message, "Action")


def _extract_colors(image) -> bool:
    """静默截取 → 本地聚类取主色 → 复制色值并打开配色窗口。

    这一步完全在本地做（见 ``core.palette``）：不联网、不需要用户配任何模型。
    提取不到颜色时**先提示再返回 False**——一张纯透明或坏掉的图配上静默失败，
    用户只会以为功能坏了。
    """
    from core.palette import extract_palette, palette_text

    colors = extract_palette(image)
    if not colors:
        log_warning(T("配色提取没有得到任何颜色，动作未完成"), "Action")
        from core.i18n import make_tr
        from ui.dialogs import show_warning_dialog

        _tr = make_tr("PaletteWindow")
        show_warning_dialog(None, _tr("Palette"),
                            _tr("No color could be extracted from this image."))
        return False

    _copy_to_clipboard_text(
        palette_text(colors),
        T("已复制 {count} 个配色色值", count=len(colors)),
    )
    from ui.palette_window import show_palette_result

    show_palette_result(colors)
    return True


def _show_formula_result(latex: str) -> None:
    """展示识别到的 LaTeX：排版预览 + 可改的源码（渲染器见 ``ocr.latex_render``）。

    窗口起不来时退回纯文本对话框——识别本身已经成功，不该因为显示层的问题让用户
    看不到结果。复制到剪贴板那一步在调用方，早就做完了。
    """
    try:
        from ui.formula_window import show_formula_result

        show_formula_result(latex)
        return
    except Exception as e:
        log_exception(e, T("显示公式结果窗口"))

    from core.i18n import make_tr
    from ui.dialogs import show_text_dialog

    show_text_dialog(None, make_tr("FormulaResult")("Formula Result"), latex)


def _show_formula_unavailable() -> None:
    """公式引擎缺失时的显式提示（不是静默失败）。"""
    from core.i18n import make_tr
    from ui.dialogs import show_warning_dialog

    _tr = make_tr("FormulaResult")
    show_warning_dialog(
        None,
        _tr("Formula Recognition Unavailable"),
        _tr("No formula engine is available on this machine: jietuba does not ship a "
            "formula model. Install a formula engine plugin and try again."),
    )


def _run_app_entry(action_id: str, app) -> bool:
    """打开剪贴板 / 翻译 / 贴图这类应用级入口（托盘、热键、全局鼠标动作共用）。"""
    if action_id == "clipboard":
        app.open_clipboard_window()
        return True
    if action_id == "open_translation":
        # 有选中文本时显示小窗，否则打开完整输入窗口——与旧热键走的是同一个入口
        app.smart_translation_controller.trigger()
        return True
    if action_id == "pin_clipboard_text":
        return _pin_clipboard_text(app)
    if action_id == "open_save_folder":
        return _open_save_folder(app)
    if action_id == "translate_clipboard_image":
        return _translate_clipboard_image(app)
    if action_id == "check_updates":
        return _check_updates(app)
    if action_id == "open_history":
        app.open_history_window()
        return True
    if action_id == "open_image_viewer":
        return bool(app.open_image_viewer())
    if action_id == "open_main_window":
        return bool(app.open_main_window())
    return False


def _translate_clipboard_image(app) -> bool:
    """翻译剪贴板里的图片：OCR → 翻译 → 结果窗口。

    直接复用截图翻译那条链路（``TranslationManager.translate_from_image``）：
    它已经负责「先开结果窗口、后台识别、识别完自动翻译」，另写一套只会多一份行为差。
    """
    image = clipboard_image()
    if image is None:
        log_warning(T("剪贴板里没有图片，无法翻译"), "Action")
        return False

    from PySide6.QtGui import QPixmap

    from translation import TranslationManager

    pixmap = QPixmap.fromImage(image)
    params = {}
    getter = getattr(app.config_manager, "get_translation_request_params", None)
    if getter is not None:
        try:
            params = getter()
        except Exception as e:
            log_exception(e, T("读取翻译参数"))

    log_debug(T("图片翻译: {width}x{height}", width=pixmap.width(), height=pixmap.height()),
              "Action")
    TranslationManager.instance().translate_from_image(pixmap=pixmap, **params)
    return True


def _check_updates(app) -> bool:
    """托盘/热键触发的「检查更新」：读远端 → 如实提示 → 有新版才打开发布页。

    返回「这次检查有没有得到结论」：读不到远端返回 False（用户看到的提示里说明了原因），
    已是最新返回 True——两者都不该被当成「动作执行失败」。
    """
    from PySide6.QtWidgets import QSystemTrayIcon

    from core.updates import run_update_check

    def notify(message: str) -> None:
        log_debug(T("更新检查: {message}", message=message), "Action")
        icon = getattr(app, "tray_icon", None)
        if icon is not None and hasattr(icon, "showMessage"):
            icon.showMessage(T("检查更新").render(), message,
                             QSystemTrayIcon.MessageIcon.Information, 5000)

    result = run_update_check(notify)
    if result.state == "newer" and result.url:
        from core.platform import shell

        shell.open_url(result.url)
    return result.state != "unknown"


def _open_save_folder(app) -> bool:
    """用系统文件管理器打开截图保存目录。"""
    from core.platform import shell

    folder = ""
    getter = getattr(app.config_manager, "get_screenshot_save_path", None)
    if getter is not None:
        try:
            folder = str(getter() or "")
        except Exception as e:
            log_exception(e, T("读取截图保存目录"))

    if not folder:
        log_warning(T("没有配置截图保存目录，无法打开"), "Action")
        return False
    opened = shell.open_path(folder)
    if not opened:
        log_warning(T("打开截图保存目录失败: {folder}", folder=folder), "Action")
    return bool(opened)


def clipboard_text() -> str:
    """剪贴板里的纯文本；拿不到剪贴板时返回空串。"""
    from PySide6.QtWidgets import QApplication

    clipboard = QApplication.clipboard()
    return clipboard.text() if clipboard is not None else ""


def clipboard_image():
    """剪贴板里的图片；剪贴板里不是图（或拿不到剪贴板）时返回 None。"""
    from PySide6.QtWidgets import QApplication

    clipboard = QApplication.clipboard()
    if clipboard is None:
        return None
    image = clipboard.image()
    return None if image is None or image.isNull() else image


def _pin_clipboard_text(app) -> bool:
    """把剪贴板里的文字做成一张贴图（「文字贴图」）。

    渲染成图像贴图而不是另立文本贴图管线：贴图的缩放/透明度/复制/会话恢复都建立在
    QImage 上，这样一行代码就能拥有全部能力，代价是贴出来的字不能再编辑。
    """
    from PySide6.QtGui import QCursor

    text = clipboard_text()
    if not text.strip():
        log_warning(T("剪贴板里没有文字，无法贴图"), "Action")
        return False

    from pin.pin_manager import PinManager
    from pin.pin_text_pin import render_text_image

    config = app.config_manager
    image = render_text_image(
        text,
        font_size=config.get_pin_text_font_size(),
        max_width=config.get_pin_text_max_width(),
    )
    if image.isNull():
        return False

    pin = PinManager.instance().create_pin(
        image=image, position=QCursor.pos(), config_manager=config
    )
    if pin is None:
        return False
    log_debug(T("已把剪贴板文字做成贴图（{count} 字）", count=len(text)), "Action")
    return True


def run_action(action_id: str, app) -> bool:
    """执行一个动作；不认识的 id 记一条日志并返回 False。

    静默动作先抓图再分发；编辑类动作把检测到的区域带进截图编辑器；打开编辑器那类
    动作直接走应用入口，不做额外处理。
    """
    action = ACTIONS_BY_ID.get(action_id)
    if action is None:
        log_warning(T("不认识的动作: {action_id}", action_id=action_id), "Action")
        return False

    if action_id in APP_ENTRY_ACTIONS:
        log_debug(T("动作触发: {action_id}", action_id=action_id), "Action")
        return _run_app_entry(action_id, app)

    if action.editor_mode:
        log_debug(T("动作触发: {action_id}", action_id=action_id), "Action")
        return bool(app.start_screenshot_for_gesture(action.editor_mode, _target_rect(app)))

    if not action.silent_capture:
        log_debug(T("动作触发: {action_id}", action_id=action_id), "Action")
        app.start_screenshot()
        return True

    captured = capture_at_cursor(app)
    if captured is None:
        log_warning(T("静默截图失败，动作未执行: {action_id}", action_id=action_id), "Action")
        return False

    image, rect = captured
    from PySide6.QtCore import QPoint

    log_debug(T("动作触发: {action_id}", action_id=action_id), "Action")
    _flash_capture_mask(rect, app)
    _record_history(image, rect, app)
    if action_id == "screenshot_copy":
        return _copy(image)
    if action_id == "screenshot_quick_save":
        return _quick_save(image, app)
    if action_id == "screenshot_pin":
        return _pin(image, QPoint(int(rect.x()), int(rect.y())), app)
    if action_id == "screenshot_copy_text":
        return _copy_text(image)
    if action_id == "copy_table_markdown":
        return _copy_table_markdown(image)
    if action_id == "recognize_table":
        return _recognize_table(image)
    if action_id == "auto_recognize":
        return _auto_recognize(image, app)
    if action_id == "mask_sensitive_info":
        return mask_sensitive_info(image)
    if action_id == "beautify_export":
        return beautify_export(image, getattr(app, "config_manager", None))
    if action_id == "extract_colors":
        return _extract_colors(image)
    if action_id == "translate_image_in_place":
        return translate_image_in_place(image, getattr(app, "config_manager", None))
    if action_id == "read_image_ai":
        return _read_image_with_ai(image, app)
    if action_id == "solve_problem":
        return _solve_problem(image, app)
    if action_id == "convert_image_markdown":
        return _convert_image_markdown(image, app)
    if action_id == "recognize_formula":
        return _recognize_formula(image)
    return False
