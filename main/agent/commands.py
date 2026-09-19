# -*- coding: utf-8 -*-
"""Agent 能调用的能力：状态、截图、识别。

外部 Agent（或任何脚本）通过 ``--json`` 命令或本机 bridge 调到这里。两条入口共用
同一份实现，所以「命令行跑出来的」与「bridge 跑出来的」结果一致。

三条硬约束（外部调用与用户手动操作必须可区分）：

- **不弹窗**：截图走 ``CaptureService``（mss/Rust 抓帧），不经过截图编辑器，不开任何窗口；
- **不抢焦点**：全程不 ``activateWindow``、不动光标、不注入按键；
- **能降级**：没装 OCR、没有屏幕录制权限时如实返回 ``ok: false`` 与原因，而不是抛异常。

返回结构统一为 ``{"ok": bool, "command": str, ...}``，失败时带 ``error``。
"""

import hashlib
import json
import os
import time

from core.logger import T, log_debug, log_exception, log_warning

#: 支持的命令
COMMANDS = ("status", "capture", "ocr")


def _ensure_gui():
    """建一个 QGuiApplication（不是 QApplication）。

    截图后的图像与 OCR 引擎都需要 QPixmap，而 QPixmap 要求有 GUI 应用实例；用
    ``QGuiApplication`` 就够，且它不会创建窗口、不会抢焦点——这正是「Agent 调用不可
    打扰用户」的前提。已经有实例（应用内 bridge）时直接复用。
    """
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


def _app_data_file(name: str):
    from core.platform.paths import app_data_dir

    folder = app_data_dir() / "agent"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def _image_digest(image) -> str:
    """图像内容的短指纹（Agent 用它判断两次截图是不是同一张）。"""
    from PySide6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    payload = bytes(buffer.data())
    buffer.close()
    return hashlib.sha256(payload).hexdigest()[:16]


def capture(*, path: str = "", save: bool = True) -> dict:
    """静默抓取整个虚拟桌面，返回图像信息（不放回任何窗口）。"""
    _ensure_gui()
    from capture.capture_service import CaptureService

    image, rect = CaptureService().capture_all_screens()
    if image is None or image.isNull():
        return {"ok": False, "command": "capture",
                "error": T("抓屏失败（可能是缺少屏幕录制权限）").render()}

    target = path
    if not target and save:
        target = str(_app_data_file("last_capture.png"))
    if target:
        folder = os.path.dirname(target)
        if folder:
            os.makedirs(folder, exist_ok=True)
        if not image.save(target, "PNG"):
            return {"ok": False, "command": "capture",
                    "error": T("截图写入失败: {path}", path=target).render()}
        # 记一份「最近一次截图在哪」：ocr 不带路径时就用它
        try:
            _app_data_file("last_capture.txt").write_text(target, encoding="utf-8")
        except OSError:
            pass

    region = [rect.x(), rect.y(), rect.width(), rect.height()]
    if region[2] <= 0 or region[3] <= 0:
        # 显示器休眠时后端会报 0×0，但抓到的图仍是真实尺寸（本机实测过）。
        # 与其给调用方一个自相矛盾的 region，不如按图像尺寸兜底并说明。
        log_warning(T("抓屏后端报告的屏幕尺寸无效（{width}x{height}），按图像尺寸兜底",
                      width=region[2], height=region[3]), "Agent")
        region = [0, 0, image.width(), image.height()]

    result = {
        "ok": True,
        "command": "capture",
        "image": {
            "path": target,
            "width": image.width(),
            "height": image.height(),
            "digest": _image_digest(image),
            "captured_at": int(time.time()),
            "region": region,
        },
    }
    log_debug(T("Agent 截图: {width}x{height} → {path}", width=image.width(),
                height=image.height(), path=target or "(未落盘)"), "Agent")
    return result


def _last_capture_path() -> str:
    try:
        path = _app_data_file("last_capture.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return path if path and os.path.exists(path) else ""


def ocr(*, path: str = "", save_text: str = "", return_format: str = "text") -> dict:
    """识别一张图的文字；不给路径就用最近一次截图。

    返回文本、行数与平均置信度——置信度是 Agent 判断「要不要改用视觉模型」的依据
    （与 ``ocr/quality.py`` 用的是同一个算法）。
    """
    _ensure_gui()
    from ocr import is_ocr_available, recognize_text

    source = path or _last_capture_path()
    if not source:
        return {"ok": False, "command": "ocr",
                "error": T("没有可识别的图片：请先截图或给出 --from 路径").render()}
    if not os.path.exists(source):
        return {"ok": False, "command": "ocr",
                "error": T("图片不存在: {path}", path=source).render()}
    if not is_ocr_available():
        return {"ok": False, "command": "ocr", "error": T("OCR 不可用").render()}

    from PySide6.QtGui import QImage, QPixmap

    image = QImage(source)
    if image.isNull():
        return {"ok": False, "command": "ocr",
                "error": T("图片读不出来: {path}", path=source).render()}

    result = recognize_text(QPixmap.fromImage(image), return_format="dict")
    if not isinstance(result, dict) or result.get("code") != 100:
        return {"ok": False, "command": "ocr", "source": source,
                "error": T("识别失败: {msg}", msg=str((result or {}).get("msg", ""))).render()}

    from ocr import format_ocr_result_text
    from ocr.quality import average_confidence

    text = (format_ocr_result_text(result) or "").strip()
    rows = [row for row in (result.get("data") or []) if isinstance(row, dict)]

    if save_text:
        try:
            os.makedirs(os.path.dirname(save_text) or ".", exist_ok=True)
            with open(save_text, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as e:
            log_warning(T("识别文本写入失败: {e}", e=e), "Agent")

    log_debug(T("Agent 识别: {count} 行（置信度 {score}）", count=len(rows),
                score=round(average_confidence(result), 3)), "Agent")
    return {
        "ok": True,
        "command": "ocr",
        "source": source,
        "text": text,
        "line_count": len(rows),
        "average_confidence": round(average_confidence(result), 4),
        "length": len(text),
        "text_path": save_text,
    }


def status() -> dict:
    """本机能力状态：Agent 用它决定「下一步能做哪些调用」。"""
    _ensure_gui()
    info = {"ok": True, "command": "status", "app": {}}
    app_info = info["app"]
    try:
        from main_app import APP_VERSION

        app_info["version"] = APP_VERSION
    except Exception as e:
        log_exception(e, T("读取应用版本"))
        app_info["version"] = ""
    try:
        from ocr import is_ocr_available

        app_info["ocr"] = bool(is_ocr_available())
    except Exception:
        app_info["ocr"] = False
    try:
        from ocr.vision_models import load_models
        from settings import get_tool_settings_manager

        manager = get_tool_settings_manager()
        app_info["vision_models"] = len([m for m in load_models(manager) if m.usable])
        app_info["translation_configured"] = bool(
            manager.get_translation_provider() and manager.get_translation_provider() != "local")
    except Exception as e:
        log_exception(e, T("读取视觉模型状态"))
        app_info["vision_models"] = 0
    app_info["commands"] = list(COMMANDS)
    app_info["last_capture"] = _last_capture_path()
    return info


#: 命令名 → 实现
HANDLERS = {
    "status": status,
    "capture": capture,
    "ocr": ocr,
}


def run_command(name: str, options: dict | None = None) -> dict:
    """执行一条命令；不认识的命令如实返回错误，不抛异常。"""
    handler = HANDLERS.get(str(name or "").strip())
    if handler is None:
        return {"ok": False, "command": str(name or ""),
                "error": T("不认识的能力: {name}", name=str(name)).render(),
                "available": list(COMMANDS)}
    try:
        return handler(**(options or {}))
    except TypeError as e:
        # 参数签名不匹配（Agent 传错键）：这是调用方的问题，要说清楚
        return {"ok": False, "command": name,
                "error": T("参数不正确: {e}", e=e).render()}
    except Exception as e:
        log_exception(e, T("执行 Agent 命令 {name}", name=name))
        return {"ok": False, "command": name,
                "error": T("执行失败: {e}", e=e).render()}


def dumps(result: dict) -> str:
    """统一输出：一行 JSON（方便管道里逐行读），中文原样不转义。"""
    return json.dumps(result, ensure_ascii=False, sort_keys=False)
