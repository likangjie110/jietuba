# -*- coding: utf-8 -*-
"""贴图的会话保存与恢复（「启动恢复未关闭的贴图」）。

退出时把还开着的贴图存到应用数据目录的 ``pins/`` 下（每张一个 PNG + 一个
``session.json``），下次启动按设置恢复。存的是**用户当时看到的样子**（渲染后的图像、
位置大小、透明度、锁定状态），因为这才是「恢复」的含义。

刻意不存的东西，写在这里免得以后当成漏了：

- 贴图上的画笔痕迹（绘制项目）：那是编辑态数据，恢复出偏差会让用户以为图坏了；
- 缩略图模式与临时置顶状态：属于临时视图状态，恢复回默认更符合直觉。

单张图超过 ``MAX_PIN_BYTES`` 就跳过并记日志：宁可少恢复一张，也不要让应用数据目录
无上限地长。
"""

import json
from pathlib import Path

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QImage

from core.logger import T, log_debug, log_exception, log_warning
from core.platform.paths import app_data_dir

#: 会话文件与图片目录
SESSION_DIR_NAME = "pins"
SESSION_FILE_NAME = "session.json"

#: 单张贴图序列化后的大小上限（超过就跳过，避免数据目录失控）
MAX_PIN_BYTES = 8 * 1024 * 1024


def session_dir() -> Path:
    """贴图会话目录（不存在时创建）。"""
    path = app_data_dir() / SESSION_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _image_path(index: int) -> Path:
    return session_dir() / f"pin_{index}.png"


def _pin_entry(pin, index: int) -> dict | None:
    """把一张贴图收敛成可序列化的条目；存不下来时返回 None。"""
    try:
        image = pin.get_current_image()
        if image is None or image.isNull():
            return None
        path = _image_path(index)
        if not image.save(str(path), "PNG"):
            log_warning(T("贴图图像保存失败，跳过这张"), "PinSession")
            return None
        if path.stat().st_size > MAX_PIN_BYTES:
            log_warning(
                T("贴图太大无法保存（{size} 字节），跳过这张", size=path.stat().st_size),
                "PinSession",
            )
            path.unlink(missing_ok=True)
            return None
        geometry = pin.geometry()
        return {
            "image": path.name,
            "x": geometry.x(),
            "y": geometry.y(),
            "width": geometry.width(),
            "height": geometry.height(),
            "opacity": float(getattr(pin, "_win_opacity", 1.0)),
            "locked": bool(pin.is_locked()),
        }
    except Exception as e:
        log_exception(e, T("序列化贴图"))
        return None


def clear_session() -> None:
    """删掉旧会话（关掉「启动恢复」时用，免得留着过期图片）。"""
    directory = session_dir()
    for path in directory.glob("pin_*.png"):
        try:
            path.unlink()
        except OSError as e:
            log_exception(e, T("清理旧贴图文件"))
    (directory / SESSION_FILE_NAME).unlink(missing_ok=True)


def save_session(pins, limit: int) -> int:
    """保存未关闭的贴图，返回真正存下来的张数。

    ``limit`` 是「贴图历史数量」：超出时保留最近的若干张（贴图列表是按创建顺序排的，
    末尾就是最近的那几张）。
    """
    clear_session()
    if limit <= 0:
        return 0

    kept = list(pins)[-limit:]
    entries = []
    for index, pin in enumerate(kept):
        entry = _pin_entry(pin, index)
        if entry is not None:
            entries.append(entry)

    directory = session_dir()
    try:
        (directory / SESSION_FILE_NAME).write_text(
            json.dumps(entries, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        log_exception(e, T("写入贴图会话文件"))
        return 0
    log_debug(T("已保存 {count} 张贴图的会话", count=len(entries)), "PinSession")
    return len(entries)


def load_session(limit: int) -> list:
    """读回会话条目；文件缺失或坏掉时返回空表。"""
    if limit <= 0:
        return []
    path = session_dir() / SESSION_FILE_NAME
    if not path.exists():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log_exception(e, T("读取贴图会话文件"))
        return []
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)][:limit]


def restore_pins(config_manager, limit: int, manager=None) -> int:
    """按会话条目重建贴图；返回成功恢复的张数。

    位置与大小用会话里的值**显式设回去**：``create_pin`` 会按「新贴图位置」策略摆放
    （可能被设成鼠标位置/屏幕中央），而恢复的含义是回到原处。
    """
    from pin.pin_manager import PinManager

    manager = manager or PinManager.instance()
    restored = 0
    for entry in load_session(limit):
        try:
            image = QImage(str(session_dir() / entry.get("image", "")))
            if image.isNull():
                log_warning(T("贴图会话里的图片读不出来，跳过: {name}", name=entry.get("image")),
                            "PinSession")
                continue
            stored = QRect(
                int(entry.get("x", 0)), int(entry.get("y", 0)),
                int(entry.get("width", image.width())), int(entry.get("height", image.height())),
            )
            pin = manager.create_pin(
                image=image,
                position=QPoint(stored.x(), stored.y()),
                config_manager=config_manager,
            )
            if pin is None:
                continue
            _apply_restored_state(pin, stored, entry, image)
            restored += 1
        except Exception as e:
            log_exception(e, T("恢复贴图"))
    if restored:
        log_debug(T("已恢复 {count} 张贴图", count=restored), "PinSession")
    return restored


def _apply_restored_state(pin, stored: QRect, entry: dict, image: QImage) -> None:
    """把会话里的位置/大小/透明度/锁定写回刚建出来的贴图。"""
    pin.setGeometry(stored)
    if image.width():
        # 恢复出来的显示比例要跟着会话里的大小走，否则后续滚轮缩放的基准是错的
        pin.scale_factor = max(0.05, stored.width() / image.width())
    opacity = float(entry.get("opacity", 1.0))
    pin._win_opacity = opacity
    pin.setWindowOpacity(opacity)
    if entry.get("locked"):
        pin.toggle_lock()
