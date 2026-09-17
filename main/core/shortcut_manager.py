"""
统一快捷键管理器

合并了三套机制：
  1. 系统级全局热键 (Windows RegisterHotKey / WM_HOTKEY)
  2. 鼠标侧键全局热键 (pynput.mouse.Listener 后台线程)
  3. 应用内 Qt KeyPress 事件分发

三者共用同一条优先级 handler 链，解决模块间快捷键冲突问题。

架构：
    ShortcutManager (单例，安装在 QApplication 上)
      ├── 注册/注销 Windows 全局热键
      ├── _HotkeyEventFilter    — 拦截 WM_HOTKEY，交由 handler 链决定是否执行回调
      ├── pynput.mouse.Listener — 后台线程监听鼠标侧键，经 Signal(QueuedConnection)
      │                          转发回主线程，再交由 handler 链决定是否执行回调
      ├── eventFilter           — 拦截 Qt KeyPress，按优先级分发
      └── handler 列表          — 统一的优先级分发链

每个模块实现 ShortcutHandler 接口：
    - is_active() → bool          : 当前是否应该接收按键
    - handle_key(event) → bool    : 处理 Qt KeyPress，返回 True 表示已消费
    - handle_hotkey(hotkey_id, callback) → bool        (可选覆写)
        返回 True = 拦截此次键盘全局热键，不执行原回调
    - handle_mouse_hotkey(token, callback) → bool      (可选覆写)
        返回 True = 拦截此次鼠标侧键全局热键，不执行原回调

优先级（数字越大越优先）：
    200  热键录入框    — 需要捕获系统热键本身
    100  截图模式      — 全屏遮罩
     80  GIF 绘制模式  — 绘制层活跃时
     60  剪贴板窗口    — 弹出时
     50  钉图编辑模式  — 画布工具激活时
     40  钉图普通模式  — 鼠标在钉图上方时
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QEvent, QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QAbstractSpinBox, QComboBox, QGraphicsView,
    QLineEdit, QPlainTextEdit, QTextEdit,
)

from core import log_debug, log_error, safe_event
from core.logger import log_exception, T
from core.platform import hotkey as platform_hotkey
from core.platform.hotkey import (
    PYNPUT_BUTTON_NAME_TOKENS,
    is_mouse_button_hotkey,
    parse_hotkey,
)

# 全局热键的平台机制（RegisterHotKey + WM_HOTKEY 过滤器 / pynput 监听、鼠标侧键钩子）
# 都在 core/platform/hotkey.py；这里只保留「哪个热键该干什么」这类应用级决策。


def hotkey_identity(hotkey_str: str):
    """把热键字符串归一成可比较的身份，用于判断两处绑定是否是同一个键。

    大小写、空格和修饰键顺序都不影响结果，因此 "Ctrl + Shift + A" 与
    "shift+ctrl+a" 得到同一个身份。

    返回 None 表示「没有绑定」——空值与 "ctrl+" 这类尚未录完的前缀都算，
    它们彼此之间不构成冲突，否则多个留空的备用键会互相报重复。
    """
    normalized = (hotkey_str or "").strip().lower()
    if not normalized or normalized.endswith("+"):
        return None
    if is_mouse_button_hotkey(normalized):
        return ("mouse", normalized)
    try:
        mods, vk = parse_hotkey(normalized)
        return ("keyboard", mods, vk)
    except (TypeError, ValueError):
        # 解析不了的值仍按规范化文本判重，至少让两个相同的非法值互相可见；
        # 「这个键本身不可用」由录入框的系统占用检测单独显示。
        return ("invalid", normalized)


# ======================================================================
# Handler 接口
# ======================================================================

class ShortcutHandler(ABC):
    """快捷键处理器接口，各模块实现此接口注册到管理器"""

    @abstractmethod
    def is_active(self) -> bool:
        """当前是否处于活跃状态（应该接收按键）"""
        ...

    @abstractmethod
    def handle_key(self, event) -> bool:
        """
        处理 Qt KeyPress 事件。

        Args:
            event: QKeyEvent

        Returns:
            True  — 已消费此事件
            False — 不处理，交给下一个 handler
        """
        ...

    def handle_hotkey(self, hotkey_id: int, callback: Callable) -> bool:
        """
        处理系统级 WM_HOTKEY 事件（可选覆写）。

        当 Windows 全局热键触发时，在执行原始回调之前，
        ShortcutManager 会先按优先级询问每个活跃 handler。
        返回 True 表示拦截此次热键（不执行原回调）。

        默认实现：不拦截，返回 False。
        """
        return False

    def handle_mouse_hotkey(self, token: str, callback: Callable) -> bool:
        """
        处理鼠标侧键全局热键事件（可选覆写）。

        当鼠标侧键（MOUSE_BUTTON_BACK / MOUSE_BUTTON_FORWARD）触发时，
        在执行原始回调之前，ShortcutManager 会先按优先级询问每个活跃 handler。
        返回 True 表示拦截此次热键（不执行原回调）。

        注意 callback 可能为 None：侧键未绑定任何功能时也会走这条链，好让
        热键录入框能录到它。覆写时不要假设 callback 非空。

        默认实现：不拦截，返回 False。
        """
        return False

    @property
    @abstractmethod
    def priority(self) -> int:
        """优先级，数字越大越优先"""
        ...

    @property
    def handler_name(self) -> str:
        """用于日志的名称"""
        return self.__class__.__name__


# ======================================================================
# 统一管理器（单例）
# ======================================================================

class ShortcutManager(QObject):
    """
    统一快捷键管理器（单例）

    同时管理：
      - Windows RegisterHotKey 全局热键
      - 鼠标侧键全局热键（pynput.mouse.Listener 后台线程）
      - Qt 应用内 KeyPress 事件
    """

    _instance: Optional['ShortcutManager'] = None

    # 类级变量，跟踪当前进程所有已注册的热键 (mods, vk)
    _registered_keys_global: Set[Tuple[int, int]] = set()
    # 类级变量，跟踪当前进程所有已登记的鼠标侧键 token
    _registered_mouse_buttons_global: Set[str] = set()

    # pynput 回调运行在后台线程，用 Signal(QueuedConnection) 转发回主线程，
    # 这样后续的 handler 链分发、回调执行都和键盘热键一样发生在主线程上。
    _mouse_button_triggered = Signal(str)

    # 同上：pynput 键盘监听也在后台线程，回调统一排队回主线程执行
    _keyboard_hotkey_triggered = Signal(str)

    def __init__(self):
        super().__init__()
        # ── handler 链 ──
        self._handlers: List[ShortcutHandler] = []

        # ── 系统热键 ──
        self._id_to_callback: Dict[int, Callable] = {}
        self._id_to_metadata: Dict[int, Tuple[int, int]] = {}  # id → (mods, vk)
        self._next_hotkey_id = 1
        self._global_hotkeys_suppressed = False

        # ── 键盘全局热键（非 Windows：pynput 自己匹配）──
        self._keyboard_hotkeys: Dict[str, Callable] = {}   # 归一化热键字符串 → 回调
        self._keyboard_ids: Dict[str, int] = {}            # 合成 id，只为日志和 handler 链
        self._keyboard_listener = None                     # pynput.keyboard.GlobalHotKeys
        self._keyboard_hotkey_triggered.connect(
            self._on_keyboard_hotkey, Qt.ConnectionType.QueuedConnection
        )

        # ── 鼠标侧键 ──
        self._mouse_callbacks: Dict[str, Callable] = {}
        self._mouse_listener = None   # pynput.mouse.Listener，按需启停
        self._mouse_capture_refs = 0  # 处于聚焦状态的热键录入框数量
        # 钩子线程只读、主线程整体替换的抑制集合，见 _mouse_event_filter
        self._suppressed_mouse_tokens: frozenset = frozenset()
        self._mouse_button_triggered.connect(
            self._on_mouse_button_triggered, Qt.ConnectionType.QueuedConnection
        )

        # 原生事件过滤器（WM_HOTKEY）：只有 Windows 有，其它平台为 None
        self._native_filter = platform_hotkey.create_native_event_filter(
            self._on_native_hotkey
        )

    @classmethod
    def instance(cls) -> 'ShortcutManager':
        if cls._instance is None:
            cls._instance = cls()
            app = QApplication.instance()
            if app:
                app.installEventFilter(cls._instance)
                if cls._instance._native_filter is not None:
                    app.installNativeEventFilter(cls._instance._native_filter)
                log_debug(T("ShortcutManager 已安装（KeyPress + WM_HOTKEY）"), "Shortcut")
        return cls._instance

    # ==================================================================
    # Handler 注册 / 注销
    # ==================================================================

    def register(self, handler: ShortcutHandler):
        """注册一个快捷键处理器"""
        if handler not in self._handlers:
            self._handlers.append(handler)
            self._handlers.sort(key=lambda h: h.priority, reverse=True)
            log_debug(
                T(
                    "注册 handler: {handler_name} (优先级 {priority})，"
                    "当前共 {handler_count} 个",
                    handler_name=handler.handler_name,
                    priority=handler.priority,
                    handler_count=len(self._handlers),
                ),
                "Shortcut",
            )

    def unregister(self, handler: ShortcutHandler):
        """注销一个快捷键处理器"""
        try:
            self._handlers.remove(handler)
            log_debug(T("注销 handler: {handler_name}", handler_name=handler.handler_name), "Shortcut")
        except ValueError:
            pass

    @property
    def global_hotkeys_suppressed(self) -> bool:
        """是否临时吞掉全局热键回调，但保持 Windows 热键注册。"""
        return self._global_hotkeys_suppressed

    def set_global_hotkeys_suppressed(self, suppressed: bool):
        """临时启用/禁用全局热键响应，不注销 Windows 热键。"""
        self._global_hotkeys_suppressed = bool(suppressed)

    def has_registered_hotkeys(self) -> bool:
        """当前是否持有已注册的全局热键（键盘或鼠标侧键）。"""
        return (bool(self._id_to_callback) or bool(self._mouse_callbacks)
                or bool(self._keyboard_hotkeys))

    # ==================================================================
    # Qt KeyPress 分发
    # ==================================================================

    # 这些键属于结构性 / 功能键，即使焦点在文字框里也应交给快捷键系统
    _PASSTHROUGH_KEYS = frozenset({
        Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
        Qt.Key.Key_F1, Qt.Key.Key_F2, Qt.Key.Key_F3, Qt.Key.Key_F4,
        Qt.Key.Key_F5, Qt.Key.Key_F6, Qt.Key.Key_F7, Qt.Key.Key_F8,
        Qt.Key.Key_F9, Qt.Key.Key_F10, Qt.Key.Key_F11, Qt.Key.Key_F12,
    })

    def _is_text_input_active(self, event) -> bool:
        """焦点在文字输入控件上，且按键属于文字输入类（非结构键）"""
        if event.key() in self._PASSTHROUGH_KEYS:
            return False

        focus = QApplication.focusWidget()
        if focus is None:
            return False

        # 常见文字输入控件
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit,
                              QAbstractSpinBox)):
            return True
        if isinstance(focus, QComboBox) and focus.isEditable():
            return True

        # QGraphicsView 中正在编辑 TextItem
        if isinstance(focus, QGraphicsView):
            scene = focus.scene()
            if scene:
                from PySide6.QtWidgets import QGraphicsTextItem
                fi = scene.focusItem()
                if (isinstance(fi, QGraphicsTextItem)
                        and fi.hasFocus()
                        and bool(fi.textInteractionFlags()
                                 & Qt.TextInteractionFlag.TextEditorInteraction)):
                    return True

        return False

    @safe_event
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.KeyPress:
            return False

        # 文字输入控件获焦时，优先让控件处理按键
        if self._is_text_input_active(event):
            return False

        for handler in self._handlers:
            try:
                if handler.is_active():
                    if handler.handle_key(event):
                        log_debug(
                            T(
                                "按键被 {handler_name} 消费 (key=0x{key_hex:X})",
                                handler_name=handler.handler_name,
                                key_hex=event.key(),
                            ),
                            "Shortcut",
                        )
                        return True
            except RuntimeError:
                continue
            except Exception as e:
                log_exception(e, f"ShortcutManager: {handler.handler_name}.handle_key")
                continue

        return False

    # ==================================================================
    # WM_HOTKEY 分发（由 _HotkeyEventFilter 调用）
    # ==================================================================

    def _dispatch_hotkey(self, hotkey_id: int, callback: Callable) -> bool:
        """
        按优先级询问 handler 链是否要拦截此次系统热键。

        Returns:
            True  — 某个 handler 已拦截（不执行原回调）
            False — 没人拦截，应执行原回调
        """
        for handler in self._handlers:
            try:
                if handler.is_active():
                    if handler.handle_hotkey(hotkey_id, callback):
                        log_debug(
                            T(
                                "系统热键被 {handler_name} 拦截 (id={hotkey_id})",
                                handler_name=handler.handler_name,
                                hotkey_id=hotkey_id,
                            ),
                            "Shortcut",
                        )
                        return True
            except RuntimeError:
                continue
            except Exception as e:
                log_exception(e, f"ShortcutManager: {handler.handler_name}.handle_hotkey")
                continue
        return False

    # ==================================================================
    # 鼠标侧键全局热键分发（由 _on_mouse_button_triggered 调用，已在主线程）
    # ==================================================================

    def _dispatch_mouse_hotkey(self, token: str, callback: Callable) -> bool:
        """
        按优先级询问 handler 链是否要拦截此次鼠标侧键热键。

        Returns:
            True  — 某个 handler 已拦截（不执行原回调）
            False — 没人拦截，应执行原回调
        """
        for handler in self._handlers:
            try:
                if handler.is_active():
                    if handler.handle_mouse_hotkey(token, callback):
                        log_debug(
                            T(
                                "鼠标侧键热键被 {handler_name} 拦截 (token={token})",
                                handler_name=handler.handler_name,
                                token=token,
                            ),
                            "Shortcut",
                        )
                        return True
            except RuntimeError:
                continue
            except Exception as e:
                log_exception(e, f"ShortcutManager: {handler.handler_name}.handle_mouse_hotkey")
                continue
        return False

    def _on_mouse_button_triggered(self, token: str):
        """鼠标侧键点击的主线程入口（由 _mouse_button_triggered 信号排队转发而来）"""
        if self._global_hotkeys_suppressed:
            log_debug(
                T("系统热键已临时禁用，忽略鼠标侧键回调 (token={token})", token=token),
                "Shortcut",
            )
            return

        # 先走 handler 链、再查回调：热键录入框需要能录到「尚未绑定任何功能」
        # 的侧键，若先查回调，未绑定时就直接 return 了，handler 链没有机会介入。
        callback = self._mouse_callbacks.get(token)
        if self._dispatch_mouse_hotkey(token, callback):
            return

        if callback is None:
            return

        try:
            callback()
        except Exception as e:
            log_exception(e, T("鼠标侧键热键回调 token={token}", token=token))

    # ==================================================================
    # 系统全局热键注册 / 注销
    # ==================================================================

    def register_hotkey(self, hotkey_str: str, callback: Callable) -> bool:
        """注册一个全局热键（Windows 键盘热键，或鼠标侧键 token）"""
        if is_mouse_button_hotkey(hotkey_str):
            return self._register_mouse_hotkey(hotkey_str.strip().lower(), callback)
        if not platform_hotkey.keyboard_backend_is_native():
            return self._register_keyboard_hotkey(hotkey_str, callback)
        try:
            mods, vk = parse_hotkey(hotkey_str)
        except (TypeError, ValueError) as e:
            log_error(f"Error registering hotkey {hotkey_str}: {e}", module="Hotkey")
            return False

        hid = self._next_hotkey_id
        if platform_hotkey.register_native_hotkey(hid, mods, vk):
            self._id_to_callback[hid] = callback
            self._id_to_metadata[hid] = (mods, vk)
            ShortcutManager._registered_keys_global.add((mods, vk))
            self._next_hotkey_id += 1
            return True
        return False

    # ── 键盘全局热键（非 Windows）──────────────────────────

    _PYNPUT_MODIFIERS = {
        "ctrl": "<ctrl>", "control": "<ctrl>",
        "alt": "<alt>", "option": "<alt>",
        "shift": "<shift>",
        "win": "<cmd>", "meta": "<cmd>", "super": "<cmd>",
        "cmd": "<cmd>", "command": "<cmd>",
    }
    _PYNPUT_NAMED_KEYS = {
        "esc": "<esc>", "escape": "<esc>",
        "enter": "<enter>", "return": "<enter>",
        "space": "<space>", "tab": "<tab>",
        "backspace": "<backspace>", "delete": "<delete>", "del": "<delete>",
        "insert": "<insert>", "home": "<home>", "end": "<end>",
        "pageup": "<page_up>", "pagedown": "<page_down>",
        "printscreen": "<print_screen>", "prtsc": "<print_screen>",
        "up": "<up>", "down": "<down>", "left": "<left>", "right": "<right>",
    }

    @classmethod
    def _to_pynput_hotkey(cls, hotkey: str) -> str:
        """'ctrl+shift+a' → '<ctrl>+<shift>+a'（pynput 的写法）。

        主键位沿用 _parse_hotkey 那套命名（字母、数字、F1-F24、若干功能键），
        两边看到的字符串是同一个，用户不用为不同平台记两套写法。
        """
        parts = [p.strip().lower() for p in (hotkey or "").split("+") if p.strip()]
        if len(parts) < 2:
            # 单独一个键做全局热键会吃掉用户所有的正常打字
            raise ValueError("全局热键至少要有一个修饰键")

        *modifier_names, main_key = parts
        tokens = []
        for name in modifier_names:
            token = platform_hotkey.PYNPUT_MODIFIERS.get(name)
            if token is None:
                raise ValueError(f"不认识的修饰键: {name}")
            tokens.append(token)

        if len(main_key) == 1:
            tokens.append(main_key)
        elif main_key in platform_hotkey.PYNPUT_NAMED_KEYS:
            tokens.append(platform_hotkey.PYNPUT_NAMED_KEYS[main_key])
        elif main_key.startswith("f") and main_key[1:].isdigit() and 1 <= int(main_key[1:]) <= 24:
            tokens.append(f"<{main_key}>")
        else:
            raise ValueError(f"不认识的主键位: {main_key}")
        return "+".join(tokens)

    def _register_keyboard_hotkey(self, hotkey_str: str, callback: Callable) -> bool:
        """登记键盘全局热键（非 Windows）。

        Windows 有 RegisterHotKey：系统负责匹配、还能探测被别的程序占用。macOS
        没有等价 API，只能用 pynput 的键盘监听自己匹配——代价是它需要一个
        「辅助功能」权限，没授权时监听器收不到任何事件（静默失效），所以这里检测到
        没权限就直接告诉用户怎么办。
        """
        normalized = (hotkey_str or "").strip().lower()
        try:
            platform_hotkey.to_pynput_hotkey(normalized)
        except ValueError as e:
            log_error(f"无法解析热键 {hotkey_str}: {e}", module="Hotkey")
            return False

        self._keyboard_hotkeys[normalized] = callback
        self._sync_keyboard_listener()
        return True

    def _sync_keyboard_listener(self) -> None:
        """按当前登记的键盘热键重建监听器（幂等）。

        监听器由平台层建（pynput 的 GlobalHotKeys 组合表在构造时就固定了，改一次就
        重建一次）；回调仍在这里发信号回主线程。
        """
        platform_hotkey.stop_listener(self._keyboard_listener)
        self._keyboard_listener = None
        if not self._keyboard_hotkeys:
            return

        mapping = {
            platform_hotkey.to_pynput_hotkey(hotkey): (
                lambda h=hotkey: self._keyboard_hotkey_triggered.emit(h)
            )
            for hotkey in self._keyboard_hotkeys
        }
        self._keyboard_listener = platform_hotkey.start_keyboard_listener(mapping)

    def _on_native_hotkey(self, hotkey_id: int) -> None:
        """WM_HOTKEY 在主线程的执行入口（由平台层的原生过滤器直接调用）。

        这段判断原先写在过滤器类里；过滤器只保留「解码 Win32 消息 + 始终消费」，
        「要不要执行、要不要先过 handler 链」属于应用级决策，留在管理器里。
        """
        callback = self._id_to_callback.get(hotkey_id)
        if callback is None:
            return
        # 全局热键被临时禁用时，handler 链也不应有机会拦截
        if self._global_hotkeys_suppressed:
            log_debug(T("系统热键已临时禁用，忽略回调 (id={hotkey_id})", hotkey_id=hotkey_id),
                      "Shortcut")
            return
        if self._dispatch_hotkey(hotkey_id, callback):
            return
        try:
            callback()
        except Exception as e:
            log_exception(e, T("热键回调 id={hotkey_id}", hotkey_id=hotkey_id))

    @Slot(str)
    def _on_keyboard_hotkey(self, hotkey: str) -> None:
        """键盘热键在主线程的执行入口（由监听线程发信号过来）。"""
        callback = self._keyboard_hotkeys.get(hotkey)
        if callback is None:
            return
        if self._global_hotkeys_suppressed:
            log_debug(T("系统热键已临时禁用，忽略回调 ({hotkey})", hotkey=hotkey), "Shortcut")
            return
        hotkey_id = self._keyboard_ids.setdefault(
            hotkey, 10000 + len(self._keyboard_ids))
        if self._dispatch_hotkey(hotkey_id, callback):
            return
        try:
            callback()
        except Exception as e:
            log_exception(e, T("热键回调 {hotkey}", hotkey=hotkey))

    def _register_mouse_hotkey(self, token: str, callback: Callable) -> bool:
        """登记一个鼠标侧键 token（同进程内去重，语义对齐 RegisterHotKey 不允许重复注册）。"""
        if token in ShortcutManager._registered_mouse_buttons_global:
            return False
        self._mouse_callbacks[token] = callback
        ShortcutManager._registered_mouse_buttons_global.add(token)
        self._sync_mouse_listener()
        return True

    # ──────────────────────────────────────────────────────────────
    # 鼠标侧键：低级钩子的生命周期与独占
    # ──────────────────────────────────────────────────────────────
    #
    # 侧键不走 Qt 的焦点链（Qt 只在指针悬停于控件上时才派发 mousePressEvent），
    # 只能靠 pynput 的 WH_MOUSE_LL 低级钩子。钩子同时承担两件事：
    #
    #   1. 独占：已绑定给我们的侧键从系统里吞掉，其它程序（浏览器、资源管理器
    #      的后退/前进）收不到，做到「绑了就只有我们好使」。
    #   2. 录入：热键录入框聚焦期间独占全部侧键，这样尚未绑定的侧键也能录到，
    #      且录入这一下不会顺带让后台浏览器退一页。
    #
    # 两者都不成立时钩子必须摘掉，否则解绑之后侧键不会交还给其它程序。

    def begin_mouse_capture(self):
        """热键录入框聚焦：独占全部侧键，使未绑定的侧键也能被录入。"""
        self._mouse_capture_refs += 1
        self._sync_mouse_listener()

    def end_mouse_capture(self):
        """热键录入框失焦：释放独占，未绑定的侧键交还给其它程序。"""
        if self._mouse_capture_refs <= 0:
            return
        self._mouse_capture_refs -= 1
        self._sync_mouse_listener()

    def _desired_suppressed_tokens(self) -> frozenset:
        """当前应当从系统里吞掉的侧键集合。"""
        if self._mouse_capture_refs > 0:
            return platform_hotkey._MOUSE_BUTTON_TOKENS
        return frozenset(self._mouse_callbacks)

    def _sync_mouse_listener(self):
        """按当前状态收敛钩子的启停与抑制集合——状态变化的唯一出口（幂等）。"""
        # 整体替换而非就地增删：赋值在 GIL 下是原子的，钩子线程只会读到替换前
        # 或替换后的完整集合，不会看到中间态。
        self._suppressed_mouse_tokens = self._desired_suppressed_tokens()
        if self._mouse_callbacks or self._mouse_capture_refs > 0:
            self._start_mouse_listener()
        else:
            self._stop_mouse_listener()

    def _mouse_event_filter(self, msg, data):
        """
        WH_MOUSE_LL 钩子线程上的回调，必须保持 O(1)。

        Windows 对低级钩子有 LowLevelHooksTimeout 限制，回调超时会被系统静默
        摘掉钩子——没有异常、没有日志，表现为「侧键突然失灵」。所以这里只做查表
        和信号排队，绝不接触 Qt 对象，也绝不等待主线程。

        派发也必须在这里完成：suppress_event() 抛出的 SuppressException 会打断
        pynput 的 _handle_message，被抑制的事件根本到不了 on_click。
        """
        listener = self._mouse_listener
        if listener is None:
            return False

        decoded = platform_hotkey.decode_mouse_button(listener, msg, data)
        if decoded is None:
            return False
        button_name, pressed = decoded

        token = PYNPUT_BUTTON_NAME_TOKENS.get(button_name)
        if token is None:
            return False

        if pressed:
            self._mouse_button_triggered.emit(token)
        if token in self._suppressed_mouse_tokens:
            # 按下与抬起成对抑制：只吞掉 DOWN 会让其它程序收到一个没有配对按下
            # 的抬起事件，行为未定义。
            platform_hotkey.suppress_mouse_event(listener)
        return False

    def _start_mouse_listener(self):
        """启动侧键监听线程（幂等）。"""
        if self._mouse_listener is not None:
            return
        # filter 在 start() 之后随时可能被钩子线程调用，因此先拿到对象再启动
        self._mouse_listener = platform_hotkey.start_mouse_listener(self._mouse_event_filter)

    def _stop_mouse_listener(self):
        """停止侧键监听线程并摘掉钩子（幂等）。"""
        listener = self._mouse_listener
        if listener is None:
            return
        # 先摘引用：停止过程中若还有事件进来，filter 读到 None 会直接放行，
        # 不会把它吞掉。
        self._mouse_listener = None
        platform_hotkey.stop_listener(listener)

    def check_hotkey_availability(self, hotkey_str: str) -> bool:
        """检查快捷键是否可用（通过临时注册测试）"""
        if is_mouse_button_hotkey(hotkey_str):
            # 鼠标侧键没有系统级冲突探测手段（RegisterHotKey 不支持鼠标按键），
            # 只能在真正注册时通过登记表防重复，这里始终视为可用。
            return True
        if not platform_hotkey.keyboard_backend_is_native():
            # macOS 上 pynput 是旁路监听，别的程序占用了同一个组合也照样能收到，
            # 没有"被占用"这回事；同程序内的重复登记由设置页自己的冲突检测管。
            return True
        try:
            mods, vk = parse_hotkey(hotkey_str)

            if (mods, vk) in ShortcutManager._registered_keys_global:
                return True

            return platform_hotkey.check_native_availability(mods, vk)
        except Exception as e:
            log_exception(e, T("检查快捷键可用性"))
            return False

    def unregister_all_hotkeys(self):
        """注销所有全局热键（Windows 键盘热键 + pynput 键盘热键 + 鼠标侧键登记）"""
        if platform_hotkey.keyboard_backend_is_native():
            for hid in list(self._id_to_callback.keys()):
                platform_hotkey.unregister_native_hotkey(hid)
                meta = self._id_to_metadata.get(hid)
                if meta and meta in ShortcutManager._registered_keys_global:
                    ShortcutManager._registered_keys_global.discard(meta)

        self._id_to_callback.clear()
        self._id_to_metadata.clear()

        self._keyboard_hotkeys.clear()
        self._keyboard_ids.clear()
        self._sync_keyboard_listener()

        for token in self._mouse_callbacks:
            ShortcutManager._registered_mouse_buttons_global.discard(token)
        self._mouse_callbacks.clear()
        self._sync_mouse_listener()

# ======================================================================
# HotkeySystem — 对外公开的热键注册入口（委托给 ShortcutManager 单例）
# ======================================================================

class HotkeySystem:
    """对外公开的热键注册入口，委托给 ShortcutManager 单例。"""

    def __init__(self):
        self._mgr = ShortcutManager.instance()

    def register_hotkey(self, hotkey_str: str, callback: Callable) -> bool:
        return self._mgr.register_hotkey(hotkey_str, callback)

    def check_hotkey_availability(self, hotkey_str: str) -> bool:
        return self._mgr.check_hotkey_availability(hotkey_str)

    def unregister_all(self):
        self._mgr.unregister_all_hotkeys()

    def set_suppressed(self, suppressed: bool):
        self._mgr.set_global_hotkeys_suppressed(suppressed)

    def has_registered_hotkeys(self) -> bool:
        return self._mgr.has_registered_hotkeys()


# ======================================================================
# 应用内快捷键工具
# ======================================================================

# ── 权威键名映射表（双向）──────────────────────────────
# 字符串名 → Qt.Key  和  Qt.Key → 显示名  共享同一份数据源。
# hotkey_edit.py / inapp_key_edit.py / parse_shortcut_to_qt 均从此处导入。

def _build_key_tables():
    """延迟构建（避免模块级导入 Qt）。首次访问后缓存在模块级变量中。"""
    from PySide6.QtCore import Qt as _Qt

    # (显示名, Qt.Key, *别名)  — 别名用于从配置字符串解析
    _RAW = [
        ("Esc",       _Qt.Key.Key_Escape,    "escape"),
        ("Tab",       _Qt.Key.Key_Tab),
        ("Backtab",   _Qt.Key.Key_Backtab),
        ("Backspace", _Qt.Key.Key_Backspace),
        ("Enter",     _Qt.Key.Key_Return,    "return"),
        ("Enter",     _Qt.Key.Key_Enter),
        ("Insert",    _Qt.Key.Key_Insert),
        ("Delete",    _Qt.Key.Key_Delete,    "del"),
        ("Pause",     _Qt.Key.Key_Pause),
        ("Print",     _Qt.Key.Key_Print,     "printscreen", "prtsc"),
        ("SysReq",    _Qt.Key.Key_SysReq),
        ("Clear",     _Qt.Key.Key_Clear),
        ("Home",      _Qt.Key.Key_Home),
        ("End",       _Qt.Key.Key_End),
        ("Left",      _Qt.Key.Key_Left),
        ("Up",        _Qt.Key.Key_Up),
        ("Right",     _Qt.Key.Key_Right),
        ("Down",      _Qt.Key.Key_Down),
        ("PageUp",    _Qt.Key.Key_PageUp),
        ("PageDown",  _Qt.Key.Key_PageDown),
        ("Space",     _Qt.Key.Key_Space),
    ]

    # Qt.Key → 显示名（UI 录入框用）
    qt_key_to_display: Dict[int, str] = {}
    # 小写字符串 → Qt.Key（配置解析用）
    str_to_qt_key: Dict[str, int] = {}

    for entry in _RAW:
        display_name, qt_key = entry[0], entry[1]
        aliases = entry[2:] if len(entry) > 2 else ()

        qt_key_to_display[qt_key] = display_name
        # 用显示名的小写作为主键
        str_to_qt_key[display_name.lower()] = qt_key
        for alias in aliases:
            str_to_qt_key[alias.lower()] = qt_key

    return qt_key_to_display, str_to_qt_key


# 模块级缓存，首次访问时构建
_QT_KEY_TO_DISPLAY: Optional[Dict[int, str]] = None
_STR_TO_QT_KEY: Optional[Dict[str, int]] = None


def get_key_display_map() -> Dict[int, str]:
    """返回 {Qt.Key → 显示名} 字典（UI 录入框使用）"""
    global _QT_KEY_TO_DISPLAY, _STR_TO_QT_KEY
    if _QT_KEY_TO_DISPLAY is None:
        _QT_KEY_TO_DISPLAY, _STR_TO_QT_KEY = _build_key_tables()
    return _QT_KEY_TO_DISPLAY


def get_key_parse_map() -> Dict[str, int]:
    """返回 {小写字符串 → Qt.Key} 字典（配置解析使用）"""
    global _QT_KEY_TO_DISPLAY, _STR_TO_QT_KEY
    if _STR_TO_QT_KEY is None:
        _QT_KEY_TO_DISPLAY, _STR_TO_QT_KEY = _build_key_tables()
    return _STR_TO_QT_KEY

def parse_shortcut_to_qt(text: str):
    """
    将 "ctrl+c" / "pageup" / "shift+c" 风格字符串解析为 (Qt.Key, Qt.KeyboardModifier)。

    返回 None 表示解析失败。供各 ShortcutHandler 在 __init__ 中一次性调用。
    """
    from PySide6.QtCore import Qt as _Qt

    if not text or not isinstance(text, str):
        return None

    parts = [p.strip() for p in text.lower().split("+") if p.strip()]
    if not parts:
        return None

    _MOD_MAP = {
        "ctrl": _Qt.KeyboardModifier.ControlModifier,
        "shift": _Qt.KeyboardModifier.ShiftModifier,
        "alt": _Qt.KeyboardModifier.AltModifier,
    }
    key_map = get_key_parse_map()

    mods = _Qt.KeyboardModifier.NoModifier
    key = _Qt.Key.Key_unknown

    for p in parts:
        if p in _MOD_MAP:
            mods |= _MOD_MAP[p]
        elif p in key_map:
            key = _Qt.Key(key_map[p])
        elif len(p) == 1 and p.isalpha():
            key = _Qt.Key(ord(p.upper()))
        elif p.startswith("f") and p[1:].isdigit():
            fn = int(p[1:])
            if 1 <= fn <= 24:
                key = _Qt.Key(_Qt.Key.Key_F1.value + fn - 1)

    if key == _Qt.Key.Key_unknown:
        return None
    return (key, mods)


def is_reserved_inapp_shortcut(text: str) -> bool:
    """Return whether an in-app binding uses a fixed, non-overridable key."""
    parsed = parse_shortcut_to_qt(text)
    return bool(parsed and parsed[0] == Qt.Key.Key_Escape)


def load_inapp_bindings(keys_of_interest: Optional[List[str]] = None) -> Dict:
    """
    从 config_manager 读取应用内快捷键，返回 {cfg_key: (Qt.Key, Qt.KeyboardModifier)} 字典。

    Args:
        keys_of_interest: 需要读取的配置键列表，为 None 时使用全部默认键。
    """
    from settings import get_tool_settings_manager
    cfg = get_tool_settings_manager()
    if keys_of_interest is None:
        keys_of_interest = [
            "inapp_confirm", "inapp_pin", "inapp_undo", "inapp_redo",
            "inapp_delete",
            "inapp_copy_pin", "inapp_thumbnail", "inapp_toggle_toolbar",
            "inapp_zoom_in", "inapp_zoom_out", "inapp_translate",
        ]

    result = {}
    for k in keys_of_interest:
        text = cfg.get_inapp_shortcut(k)
        if is_reserved_inapp_shortcut(text):
            continue
        parsed = parse_shortcut_to_qt(text) if text else None
        if parsed:
            result[k] = parsed
    return result


def load_move_keys() -> Dict:
    """根据配置构建鼠标微移键表 {Qt.Key: (dx, dy)}"""
    from PySide6.QtCore import Qt as _Qt
    from settings import get_tool_settings_manager
    mode = get_tool_settings_manager().get_inapp_cursor_move_mode()
    move = {}
    if mode in ("both", "wasd"):
        move.update({
            _Qt.Key.Key_W: (0, -1), _Qt.Key.Key_S: (0, 1),
            _Qt.Key.Key_A: (-1, 0), _Qt.Key.Key_D: (1, 0),
        })
    if mode in ("both", "arrows"):
        move.update({
            _Qt.Key.Key_Up: (0, -1), _Qt.Key.Key_Down: (0, 1),
            _Qt.Key.Key_Left: (-1, 0), _Qt.Key.Key_Right: (1, 0),
        })
    return move
 
