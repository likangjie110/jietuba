"""
钉图管理器 - 单例模式管理所有钉图窗口实例
"""

from pathlib import Path
from typing import List, Optional, Tuple
from PySide6.QtCore import Qt, QObject, Signal, QPoint
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from core import log_debug, log_info, log_error
from core.logger import log_exception, T

from core.platform import window_ops

# 置顶的实现（Win32 的 SetWindowPos 与其它平台的 Qt 标志回退）在平台层，
# 见 core/platform/window_ops.set_topmost。


def _set_topmost(pin, on: bool) -> None:
    """切换某个钉图窗口的置顶状态。"""
    window_ops.set_topmost(pin, on)


def resolve_pin_position(position: QPoint, image_size, config_manager) -> QPoint:
    # 说明：image_size 只有「屏幕中央」模式用得上；拿不到尺寸时退回选区位置。
    """按设置决定新贴图出现在哪。

    - ``selection``（默认）：跟着选区位置（贴图出现在刚框住的东西旁边）
    - ``cursor``：鼠标位置
    - ``center``：主屏中央（窗口自身居中，而不是左上角落在中央）
    """
    from pin.pin_window import pin_config_value

    mode = str(pin_config_value(config_manager, "get_pin_new_position", "selection") or "")
    if mode == "cursor":
        from PySide6.QtGui import QCursor

        return QCursor.pos()
    if mode == "center" and image_size is not None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            center = screen.availableGeometry().center()
            return QPoint(center.x() - image_size.width() // 2,
                          center.y() - image_size.height() // 2)
    return position


def apply_pin_order(pin_window, existing_pins: List, config_manager) -> None:
    """按设置决定新贴图压在已有贴图上面还是下面。

    置顶标志（WindowStaysOnTopHint）管的是「压住别的应用」，贴图之间的前后顺序由
    窗口的 raise/lower 决定：新建的窗口默认在最上面，``bottom`` 时把它放下去、
    再把其它贴图提起来。
    """
    from pin.pin_window import pin_config_value

    if str(pin_config_value(config_manager, "get_pin_order", "top") or "") != "bottom":
        return
    try:
        pin_window.lower()
        for other in existing_pins:
            other.raise_()
    except Exception as e:
        log_exception(e, T("调整贴图前后顺序"))


class PinManager(QObject):
    """
    钉图管理器 - 单例模式（仅主线程调用）
    
    职责:
    - 创建和跟踪所有钉图窗口
    - 批量操作（关闭所有、显示所有）
    - 内存管理和清理
    
    注意: 此单例仅在 Qt 主线程中使用，不保证线程安全。
    """
    
    _instance = None
    
    # 信号
    pin_created = Signal(object)  # 钉图创建信号 (PinWindow)
    pin_closed = Signal(object)   # 钉图关闭信号 (PinWindow)
    all_pins_closed = Signal()    # 所有钉图关闭信号
    selection_changed = Signal()  # 多选集合变化（各窗口据此刷新选中外观）
    
    def __new__(cls, *args, **kwargs):
        """确保单例：通过 __new__ 控制实例创建，静默返回已有实例"""
        if cls._instance is not None:
            return cls._instance
        return super().__new__(cls)
    
    @classmethod
    def instance(cls):
        """获取单例实例（仅主线程调用）"""
        if cls._instance is None:
            cls._instance = PinManager()
        return cls._instance
    
    # 保留旧方法名作为别名，确保向后兼容
    @classmethod
    def get_instance(cls):
        """获取单例实例（已弃用，请使用 instance()）"""
        return cls.instance()
    
    def __init__(self):
        # 避免 QObject.__init__ 被重复调用
        # 注意：不能用 hasattr()，因为 QObject 未初始化时会抛 RuntimeError
        if '_initialized' in self.__dict__:
            return
        super().__init__()
        self._initialized = True
        self.pin_windows: List = []  # 所有钉图窗口列表
        # 多选：被选中的窗口按点击顺序排列，拖动其中一张会带动整组
        self._selected: List = []
        self._topmost_suppressed = False    # 是否已压制置顶
        self._suppressed_pins: List = []    # 被压制的 pin 窗口列表（用于精确恢复）
        
        log_info(T("钉图管理器已初始化"), "PinManager")
    
    def create_pin(
        self,
        image: QImage,
        position: QPoint,
        config_manager,
        drawing_items: Optional[List] = None,
        selection_offset: Optional[QPoint] = None,
        number_next: Optional[int] = None,
    ):
        """
        创建新钉图窗口
        
        Args:
            image: 选区底图（只包含选区的纯净背景，不含绘制）
            position: 初始位置（全局坐标）
            config_manager: 配置管理器
            drawing_items: 绘制项目列表（从截图窗口继承的向量图形）
            selection_offset: 选区在原场景中的偏移量（用于转换绘制项目坐标）
            number_next: 源场景的下一个序号值（用于同步计数器）
            
        Returns:
            PinWindow: 创建的钉图窗口实例
        """
        from pin.pin_window import PinWindow

        existing = list(self.pin_windows)
        image_size = image.size() if image is not None else None

        # 创建钉图窗口
        pin_window = PinWindow(
            image=image,
            position=resolve_pin_position(position, image_size, config_manager),
            config_manager=config_manager,
            drawing_items=drawing_items,
            selection_offset=selection_offset,
            number_next=number_next,
        )
        apply_pin_order(pin_window, existing, config_manager)

        # 连接关闭信号
        pin_window.closed.connect(lambda: self._on_pin_closed(pin_window))
        
        # 添加到列表
        self.pin_windows.append(pin_window)
        
        # 发送创建信号
        self.pin_created.emit(pin_window)
        
        log_debug(T("钉图已创建 (共 {count} 个)", count=len(self.pin_windows)), "PinManager")
        
        return pin_window
    
    def _on_pin_closed(self, pin_window):
        """钉图窗口关闭回调"""
        if pin_window in self._selected:
            # 关掉的窗口不能留在选中集合里，否则下次拖动会去动一个已销毁的窗口
            self._selected.remove(pin_window)
            self.selection_changed.emit()
        if pin_window in self.pin_windows:
            self.pin_windows.remove(pin_window)
            self.pin_closed.emit(pin_window)
            
            log_debug(T("钉图已关闭 (剩余 {count} 个)", count=len(self.pin_windows)), "PinManager")

            # 如果所有钉图都关闭了，发送信号
            if len(self.pin_windows) == 0:
                self.all_pins_closed.emit()
                log_debug(T("所有钉图已关闭"), "PinManager")
    
    def remove_pin(self, pin_window):
        """
        手动移除钉图窗口（不关闭窗口）
        
        Args:
            pin_window: 要移除的钉图窗口
        """
        if pin_window in self.pin_windows:
            self.pin_windows.remove(pin_window)
            log_debug(T("钉图已移除 (剩余 {count} 个)", count=len(self.pin_windows)), "PinManager")
    
    # ── 多选 ──────────────────────────────────────────
    #
    # 选中集合属于管理器而不是单个窗口：拖动一张时要带动整组，画选中框时也要知道
    # 别处选了什么。窗口只负责「把 Ctrl/Cmd+点击告诉自己属于哪个集合」。


    # ==================================================================
    # 分组
    # ==================================================================

    def groups(self) -> dict:
        """当前分组：``{组名: [贴图窗口]}``（按名字排序，便于界面展示）。"""
        buckets: dict = {}
        for pin in self.get_all_pins():
            name = getattr(pin, "group_name", "") or ""
            if not name:
                continue
            buckets.setdefault(name, []).append(pin)
        return {name: buckets[name] for name in sorted(buckets)}

    def group_names(self) -> List[str]:
        return list(self.groups().keys())

    def add_to_group(self, pin, name: str) -> bool:
        """把一张贴图加进某个分组（组不存在就新建）。"""
        group = str(name or "").strip()
        if pin is None or not group:
            return False
        try:
            pin.group_name = group
        except Exception as e:
            log_exception(e, T("加入贴图分组"))
            return False
        log_debug(T("贴图加入分组: {name}", name=group), "PinManager")
        return True

    def remove_from_group(self, pin) -> bool:
        """把贴图移出分组。"""
        if pin is None or not getattr(pin, "group_name", ""):
            return False
        pin.group_name = ""
        log_debug(T("贴图已移出分组"), "PinManager")
        return True

    def close_group(self, name: str) -> int:
        """关掉一个分组里的所有贴图，返回关掉的数量。"""
        pins = list(self.groups().get(str(name or "").strip(), []))
        for pin in pins:
            self.remove_pin(pin)
        log_debug(T("关闭贴图分组: {name} ({count} 张)", name=name, count=len(pins)),
                  "PinManager")
        return len(pins)

    def delete_empty_groups(self) -> List[str]:
        """清掉「已经没有成员」的分组名，返回被清掉的名字（有内容的分组不动）。

        分组是贴在窗口上的属性，窗口一关，组名就只留在这里的登记表里；不主动清的话
        右键菜单会攒下一堆空组。所以这里维护一份「见过的组名」，只删其中没有成员的。
        """
        known = set(getattr(self, "_known_groups", set()))
        alive = set(self.groups().keys())
        removed = sorted(known - alive)
        self._known_groups = alive
        if removed:
            log_debug(T("已清理空分组: {names}", names=", ".join(removed)), "PinManager")
        return removed

    def remember_group(self, name: str) -> None:
        """登记一个组名（新建分组时调用），供「清理空分组」判断。"""
        group = str(name or "").strip()
        if not group:
            return
        known = set(getattr(self, "_known_groups", set()))
        known.add(group)
        self._known_groups = known

    # ==================================================================
    # 焦点模式 / 关闭其它
    # ==================================================================

    def is_focus_mode(self, pin) -> bool:
        return getattr(self, "_focus_pin", None) is pin

    def toggle_focus_mode(self, pin) -> bool:
        """焦点模式：只显示这一张，再按一次还原（其它贴图只是隐藏，不关闭）。"""
        if pin is None:
            return False
        if self.is_focus_mode(pin):
            for other in self.get_all_pins():
                other.show()
            self._focus_pin = None
            log_debug(T("退出焦点模式"), "PinManager")
            return True
        self._focus_pin = pin
        for other in self.get_all_pins():
            if other is pin:
                other.show()
            else:
                other.hide()
        log_debug(T("进入焦点模式"), "PinManager")
        return True

    def restore_visibility(self) -> None:
        """退出焦点模式（关闭/恢复贴图时兜底）。"""
        if getattr(self, "_focus_pin", None) is None:
            return
        self._focus_pin = None
        for pin in self.get_all_pins():
            pin.show()

    def close_other_pins(self, keep) -> int:
        """关掉除 ``keep`` 以外的所有贴图，返回关掉的数量。"""
        others = [pin for pin in self.get_all_pins() if pin is not keep]
        for pin in others:
            self.remove_pin(pin)
        if others:
            log_debug(T("关闭其它贴图: {count} 张", count=len(others)), "PinManager")
        return len(others)

    def selected_pins(self) -> List:
        """当前被选中的贴图（按选中顺序）。"""
        return [pin for pin in self._selected if pin in self.pin_windows]

    def is_selected(self, pin) -> bool:
        return pin in self._selected

    def select(self, pin, selected: bool = True) -> bool:
        """选中/取消选中一张贴图；返回它现在的选中状态。"""
        if selected:
            if pin not in self._selected:
                self._selected.append(pin)
                self.selection_changed.emit()
        elif pin in self._selected:
            self._selected.remove(pin)
            self.selection_changed.emit()
        return pin in self._selected

    def toggle_selection(self, pin) -> bool:
        """切换一张贴图的选中状态；返回切换后的状态。"""
        return self.select(pin, not self.is_selected(pin))

    def clear_selection(self) -> None:
        if not self._selected:
            return
        self._selected.clear()
        self.selection_changed.emit()

    def move_selected(self, anchor, delta: QPoint) -> None:
        """把选中集合里的其它贴图按 ``delta`` 平移（``anchor`` 自己已经动过了）。"""
        for pin in self.selected_pins():
            if pin is anchor:
                continue
            try:
                pin.move(pin.pos() + delta)
                if pin.toolbar and pin.toolbar.isVisible():
                    pin.toolbar.sync_with_pin_window()
            except Exception as e:
                log_exception(e, T("移动选中的贴图"))

    def close_selected(self) -> int:
        """关闭所有选中的贴图，返回关掉几张（批量关闭，不逐张确认）。"""
        pins = self.selected_pins()
        if not pins:
            log_debug(T("没有选中的贴图"), "PinManager")
            return 0
        log_debug(T("关闭选中的 {count} 张贴图", count=len(pins)), "PinManager")
        self._selected.clear()
        for pin in pins:
            try:
                pin.close_window(confirm=False)
            except Exception as e:
                log_error(T("关闭选中的贴图失败: {e}", e=e), "PinManager")
        self.selection_changed.emit()
        return len(pins)

    def close_all(self):
        """关闭所有钉图窗口"""
        if len(self.pin_windows) == 0:
            log_debug(T("没有钉图窗口需要关闭"), "PinManager")
            return

        log_debug(T("开始关闭 {count} 个钉图窗口...", count=len(self.pin_windows)), "PinManager")

        # 复制列表，避免在迭代时修改
        pins_to_close = self.pin_windows.copy()

        for pin_window in pins_to_close:
            try:
                # 批量关闭不再逐张确认（见 PinWindow.close_window）
                pin_window.close_window(confirm=False)
            except Exception as e:
                log_error(T("关闭钉图窗口失败: {e}", e=e), "PinManager")

        # 清空列表
        self.pin_windows.clear()

        log_debug(T("所有钉图窗口已关闭"), "PinManager")
        self.all_pins_closed.emit()
    
    def get_all_pins(self) -> List:
        """
        获取所有钉图窗口
        
        Returns:
            List[PinWindow]: 钉图窗口列表
        """
        return self.pin_windows.copy()
    
    def count(self) -> int:
        """
        获取钉图数量
        
        Returns:
            int: 当前钉图数量
        """
        return len(self.pin_windows)
    
    def has_pins(self) -> bool:
        """
        是否存在钉图
        
        Returns:
            bool: 是否有钉图窗口
        """
        return len(self.pin_windows) > 0
    
    def show_all(self):
        """显示所有钉图窗口"""
        for pin_window in self.pin_windows:
            pin_window.show()
        
        log_debug(T("显示了 {count} 个钉图窗口", count=len(self.pin_windows)), "PinManager")
    
    def hide_all(self):
        """隐藏所有钉图窗口"""
        for pin_window in self.pin_windows:
            pin_window.hide()
        
        log_debug(T("隐藏了 {count} 个钉图窗口", count=len(self.pin_windows)), "PinManager")

    def move_all_to_screen_center(self):
        """移动所有钉图窗口到各自所在屏幕的中心。"""
        for pin_window in self.get_all_pins():
            try:
                screen = QApplication.screenAt(pin_window.geometry().center())
                if screen is None:
                    screen = QApplication.primaryScreen()
                if screen is None:
                    continue

                screen_rect = screen.availableGeometry()
                x = screen_rect.x() + (screen_rect.width() - pin_window.width()) // 2
                y = screen_rect.y() + (screen_rect.height() - pin_window.height()) // 2
                pin_window.move(x, y)
            except Exception as e:
                log_error(T("移动钉图到屏幕中心失败: {e}", e=e), "PinManager")

        log_debug(T("已移动 {count} 个钉图到屏幕中心", count=len(self.pin_windows)), "PinManager")

    def set_all_thumbnail_mode(self, active: bool):
        """批量进入或退出缩略图模式。"""
        changed = 0
        for pin_window in self.get_all_pins():
            try:
                if bool(pin_window._thumbnail_mode) != active:
                    pin_window.toggle_thumbnail_mode()
                    changed += 1
            except Exception as e:
                log_error(T("切换钉图缩略图模式失败: {e}", e=e), "PinManager")

        if active:
            log_debug(T("{changed} 个钉图已进入缩略图模式", changed=changed), "PinManager")
        else:
            log_debug(T("{changed} 个钉图已退出缩略图模式", changed=changed), "PinManager")

    def save_all_to_directory(self, directory: str, prefix: str = "pins") -> Tuple[int, int]:
        """
        保存所有钉图到指定目录。

        Returns:
            Tuple[int, int]: (保存成功数量, 保存失败数量)
        """
        output_dir = Path(directory)
        output_dir.mkdir(parents=True, exist_ok=True)

        saved = 0
        failed = 0
        for index, pin_window in enumerate(self.get_all_pins(), start=1):
            file_path = output_dir / f"{prefix}_{index:03d}.png"
            try:
                ok = False

                # ruff B023：闭包引用了循环变量，但 _do_save 总是在本轮迭代内被
                # 同步调用（_with_edit_paused 内部直接 func()），因此不存在延迟
                # 求值取到最后一轮变量的问题。若将来把回调改成延迟/异步执行，
                # 必须改成默认参数绑定 pin_window 和 file_path。
                def _do_save():
                    nonlocal ok
                    ok = pin_window.get_current_image().save(str(file_path))  # noqa: B023

                if hasattr(pin_window, "_with_edit_paused"):
                    pin_window._with_edit_paused(_do_save)
                else:
                    _do_save()

                if ok:
                    saved += 1
                else:
                    failed += 1
                    log_error(T("保存钉图失败: {file_path}", file_path=file_path), "PinManager")
            except Exception as e:
                failed += 1
                log_error(T("保存钉图失败: {e}", e=e), "PinManager")

        log_info(T("批量保存钉图完成: 成功 {saved}, 失败 {failed}", saved=saved, failed=failed), "PinManager")
        return saved, failed
    
    # ------------------------------------------------------------------
    # 使用 Win32 SetWindowPos 直接切换 TOPMOST/NOTOPMOST，
    # ------------------------------------------------------------------
    def suppress_topmost(self):
        """将所有置顶 pin 窗口降级为普通窗口（NOTOPMOST）。"""
        if self._topmost_suppressed:
            return
        self._topmost_suppressed = True
        self._suppressed_pins.clear()
        for pin in self.pin_windows:
            if pin.windowFlags() & Qt.WindowType.WindowStaysOnTopHint:
                _set_topmost(pin, False)
                self._suppressed_pins.append(pin)
        if self._suppressed_pins:
            log_debug(T("已压制 {count} 个钉图的置顶状态", count=len(self._suppressed_pins)), "PinManager")

    def restore_topmost(self):
        """恢复被压制的 pin 窗口为 TOPMOST。"""
        if not self._topmost_suppressed:
            return
        self._topmost_suppressed = False

        for pin in self._suppressed_pins:
            if pin not in self.pin_windows:
                continue
            _set_topmost(pin, True)
        self._suppressed_pins.clear()

    def cleanup(self):
        """清理管理器（应用退出时调用）"""
        log_debug(T("清理管理器..."), "PinManager")
        self.close_all()
        PinManager._instance = None
        log_info(T("管理器已清理"), "PinManager")


# 便捷函数
def get_pin_manager():
    """获取钉图管理器单例"""
    return PinManager.instance()
