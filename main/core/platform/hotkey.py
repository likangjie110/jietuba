# -*- coding: utf-8 -*-
"""全局热键：键盘组合键与鼠标侧键。

两条完全不同的实现路径：

- **Windows**：系统级 ``RegisterHotKey``。系统帮着匹配，还能探测某个组合是否被别的
  程序占用；触发时经一个原生事件过滤器拿到 ``WM_HOTKEY``（见 ``hotkey_win32.py``）。
- **其它平台**：没有等价的系统接口，只能用 pynput 的低级监听自己匹配组合键。代价是
  需要一个「辅助功能」权限，没授权时监听器活着但收不到任何事件（静默失效），因此这里
  会主动检查并提示。

热键字符串的**解析**（以及修饰键位编码）放在这个模块里而不是 Windows 后端里：它同时
被用来判重（``hotkey_identity``）和解析主键位名，三平台共用同一套写法，用户不必为不同
平台记两套名字。
"""

from core.platform.detection import IS_MACOS, IS_WINDOWS

# ──────────────────────────────────────────────
# 修饰键位
# ──────────────────────────────────────────────
# 沿用 Win32 的 MOD_* 编码：它既是 RegisterHotKey 的参数，也是判重时用的规范形式。
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# ──────────────────────────────────────────────
# 鼠标侧键
# ──────────────────────────────────────────────
# RegisterHotKey 只由键盘触发，侧键（XBUTTON1/2）永远走不通那条路，因此用独立的
# 字符串命名空间表示，与键盘组合键分开解析、分开登记。
MOUSE_BUTTON_BACK = "mouseback"
MOUSE_BUTTON_FORWARD = "mouseforward"
_MOUSE_BUTTON_TOKENS = frozenset({MOUSE_BUTTON_BACK, MOUSE_BUTTON_FORWARD})

# pynput 的 Button.x1 / Button.x2 → 我们的 token。按 name 匹配而不是按枚举对象，
# 这样低级钩子回调里不必持有 pynput 模块的引用。
PYNPUT_BUTTON_NAME_TOKENS = {
    "x1": MOUSE_BUTTON_BACK,
    "x2": MOUSE_BUTTON_FORWARD,
}


def is_mouse_button_hotkey(hotkey_str: str) -> bool:
    """``hotkey_str`` 是否是鼠标侧键 token，而非键盘组合键字符串。"""
    return isinstance(hotkey_str, str) and hotkey_str.strip().lower() in _MOUSE_BUTTON_TOKENS


def parse_hotkey(hotkey: str) -> tuple[int, int]:
    """把 ``'ctrl+shift+a'`` 风格字符串解析成 ``(modifiers, vk)``。

    三平台共用：Windows 拿它去 RegisterHotKey，其它平台拿它做判重与合法性校验
    （主键位名与 pynput 那边保持一致，见 ``to_pynput_hotkey``）。
    """
    if not hotkey or not isinstance(hotkey, str):
        raise ValueError("无效的热键字符串")

    parts = [p.strip().lower() for p in hotkey.split('+') if p.strip()]
    if not parts:
        raise ValueError("热键不能为空")

    mods = 0
    key = None

    for p in parts:
        if p in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif p == "alt":
            mods |= MOD_ALT
        elif p == "shift":
            mods |= MOD_SHIFT
        elif p in ("win", "meta", "super"):
            mods |= MOD_WIN
        else:
            key = p

    if not key:
        raise ValueError("缺少主键位")

    vk = None
    if len(key) == 1 and 'a' <= key <= 'z':
        vk = ord(key.upper())
    elif key.isdigit() and len(key) == 1:
        vk = ord(key)
    elif key.startswith('f') and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 24:
            vk = 0x70 + (n - 1)
    elif key in ("printscreen", "prtsc"):
        vk = 0x2C
    elif key == "esc":
        vk = 0x1B
    elif key in ("`", "oem3", "backquote", "grave"):
        vk = 0xC0
    elif key in ("-", "minus"):
        vk = 0xBD
    elif key in ("=", "equals", "equal"):
        vk = 0xBB
    elif key in ("[", "lbracket"):
        vk = 0xDB
    elif key in ("]", "rbracket"):
        vk = 0xDD
    elif key in ("\\", "backslash"):
        vk = 0xDC
    elif key in (";", "semicolon"):
        vk = 0xBA
    elif key in ("'", "quote"):
        vk = 0xDE
    elif key in (",", "comma"):
        vk = 0xBC
    elif key in (".", "period"):
        vk = 0xBE
    elif key in ("/", "slash"):
        vk = 0xBF

    if vk is None:
        raise ValueError(f"不支持的键: {key}")

    mods |= MOD_NOREPEAT
    return mods, vk


# ──────────────────────────────────────────────
# 非 Windows：pynput
# ──────────────────────────────────────────────

PYNPUT_MODIFIERS = {
    "ctrl": "<ctrl>", "control": "<ctrl>",
    "alt": "<alt>", "option": "<alt>",
    "shift": "<shift>",
    "win": "<cmd>", "meta": "<cmd>", "super": "<cmd>",
    "cmd": "<cmd>", "command": "<cmd>",
}

PYNPUT_NAMED_KEYS = {
    "esc": "<esc>", "escape": "<esc>",
    "enter": "<enter>", "return": "<enter>",
    "space": "<space>", "tab": "<tab>",
    "backspace": "<backspace>", "delete": "<delete>", "del": "<delete>",
    "insert": "<insert>", "home": "<home>", "end": "<end>",
    "pageup": "<page_up>", "pagedown": "<page_down>",
    "printscreen": "<print_screen>", "prtsc": "<print_screen>",
    "up": "<up>", "down": "<down>", "left": "<left>", "right": "<right>",
}


def to_pynput_hotkey(hotkey: str) -> str:
    """``'ctrl+shift+a'`` → ``'<ctrl>+<shift>+a'``（pynput 的写法）。

    主键位沿用 ``parse_hotkey`` 那套命名（字母、数字、F1-F24、若干功能键），
    两边看到的字符串是同一个，用户不用为不同平台记两套写法。
    """
    parts = [p.strip().lower() for p in (hotkey or "").split("+") if p.strip()]
    if len(parts) < 2:
        # 单独一个键做全局热键会吃掉用户所有的正常打字
        raise ValueError("全局热键至少要有一个修饰键")

    *modifier_names, main_key = parts
    tokens = []
    for name in modifier_names:
        token = PYNPUT_MODIFIERS.get(name)
        if token is None:
            raise ValueError(f"不认识的修饰键: {name}")
        tokens.append(token)

    if len(main_key) == 1:
        tokens.append(main_key)
    elif main_key in PYNPUT_NAMED_KEYS:
        tokens.append(PYNPUT_NAMED_KEYS[main_key])
    elif main_key.startswith("f") and main_key[1:].isdigit() and 1 <= int(main_key[1:]) <= 24:
        tokens.append(f"<{main_key}>")
    else:
        raise ValueError(f"不认识的主键位: {main_key}")
    return "+".join(tokens)


def start_keyboard_listener(mapping: dict) -> object | None:
    """按 ``{pynput 热键写法: 回调}`` 建并启动全局键盘监听；失败返回 None。

    ``GlobalHotKeys`` 的组合表在构造时就固定了，因此改动一次就重建一次监听器
    （热键数量是个位数，重建成本远低于自己维护一套按键状态机）。
    回调运行在监听线程里，调用方负责把它排队回主线程。
    """
    from core.logger import log_debug, log_error, T

    if not mapping:
        return None
    try:
        # macOS：pynput 的监听线程一启动就会去读键盘布局，那一步在非主线程上会
        # 直接崩掉进程（见 core/platform/pynput_macos.py）
        from core.platform.pynput_macos import install as install_macos_keyboard_fix

        install_macos_keyboard_fix()

        from pynput import keyboard

        listener = keyboard.GlobalHotKeys(dict(mapping))
        listener.start()
        log_debug(T("键盘热键监听已启动（{count} 个）", count=len(mapping)), "Shortcut")
        return listener
    except Exception as e:
        log_error(f"键盘热键监听启动失败: {e}", module="Hotkey")
        return None


def stop_listener(listener) -> None:
    """停止监听器（幂等）。"""
    if listener is None:
        return
    try:
        listener.stop()
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("停止热键监听"))


def input_monitoring_trusted() -> bool:
    """本程序有没有监听全局输入所需的系统权限。

    macOS 上就是「辅助功能」：pynput 靠 CGEventTap 收键盘事件，没这个权限时它连事件
    tap 都建不出来（日志里只有一句 ``This process is not trusted``），监听线程随即
    退出——监听器对象还在，但一个事件也收不到。其它平台没有这层系统门槛，恒为 True。

    这里刻意不用 ``AXIsProcessTrusted``：pynput 的监听线程会同时去解析这个名字，而
    pyobjc 的惰性函数表在**两个线程并发解析同一个函数名**时会 KeyError，把监听线程
    整个打死（实测：监听器起来后热键静默失效，日志里只有一条线程回溯）。换成只在
    自己这边用的 ``AXIsProcessTrustedWithOptions``（prompt=False 时语义相同）即可。
    """
    if not IS_MACOS:
        return True
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: False}))
    except Exception as e:
        # 查不了（pyobjc 装不上）时监听这条路本来也走不通，报「有权限」，免得再叠一层误导
        from core.logger import log_debug

        log_debug(f"检查输入监听权限失败: {e}", "Hotkey")
        return True


def request_input_monitoring_permission() -> bool:
    """请求输入监听权限，返回请求之后的信任状态。

    macOS 会弹出系统对话框（带一个「打开系统设置」按钮），并把本程序登记进「辅助功能」
    列表；用户勾选之前返回值仍是 False。必须在主线程调用——它要弹窗。
    """
    if not IS_MACOS:
        return True
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("请求辅助功能权限"))
        return input_monitoring_trusted()


# ──────────────────────────────────────────────
# 门面：键盘热键的注册与冲突探测
# ──────────────────────────────────────────────

def keyboard_backend_is_native() -> bool:
    """键盘热键是否走系统级注册（Windows）。"""
    return IS_WINDOWS


def register_native_hotkey(hotkey_id: int, mods: int, vk: int) -> bool:
    """系统级注册（仅 Windows）。调用方先确认 ``keyboard_backend_is_native()``。"""
    if not IS_WINDOWS:
        return False
    from core.platform import hotkey_win32

    return hotkey_win32.register(hotkey_id, mods, vk)


def unregister_native_hotkey(hotkey_id: int) -> None:
    """系统级注销（仅 Windows）。"""
    if not IS_WINDOWS:
        return
    from core.platform import hotkey_win32

    hotkey_win32.unregister(hotkey_id)


def check_native_availability(mods: int, vk: int) -> bool:
    """用临时注册探测该组合是否被别的程序占用（仅 Windows 有这种手段）。"""
    if not IS_WINDOWS:
        return True
    from core.platform import hotkey_win32

    return hotkey_win32.check_availability(mods, vk)


def create_native_event_filter(on_hotkey) -> object | None:
    """创建拦截 ``WM_HOTKEY`` 的原生事件过滤器（仅 Windows）。

    ``on_hotkey(hotkey_id)`` 在过滤器里被同步调用；消息始终被消费掉。
    """
    if not IS_WINDOWS:
        return None
    from core.platform import hotkey_win32

    return hotkey_win32.create_event_filter(on_hotkey)


# ──────────────────────────────────────────────
# 门面：鼠标侧键（仅 Windows 有可用的低级钩子接口）
# ──────────────────────────────────────────────

def mouse_hotkey_supported() -> bool:
    """鼠标侧键全局热键是否可用（pynput 的侧键钩子是 Windows 专有接口）。"""
    return IS_WINDOWS


def start_mouse_listener(event_filter) -> object | None:
    """启动侧键低级钩子监听；失败返回 None。

    ``event_filter(msg, data)`` 由 pynput 在钩子线程上调用，必须保持 O(1)——Windows
    对低级钩子有 LowLevelHooksTimeout，回调超时会被系统静默摘掉钩子（没有异常、没有
    日志，表现为「侧键突然失灵」）。
    """
    if not IS_WINDOWS:
        return None
    from core.platform import hotkey_win32

    return hotkey_win32.start_mouse_listener(event_filter)


def decode_mouse_button(listener, msg, data):
    """把 WH_MOUSE_LL 的消息解成 ``(button_name, pressed)``；不是侧键返回 None。

    封装 pynput 的 ``X_BUTTONS`` 表与 ``mouseData`` 的高 16 位解析——都是 Windows
    专有的细节，调用方只需要「哪个侧键、按下还是抬起」。
    """
    if not IS_WINDOWS:
        return None
    from core.platform import hotkey_win32

    return hotkey_win32.decode_mouse_button(listener, msg, data)


def suppress_mouse_event(listener) -> None:
    """把当前事件从系统里吞掉（其它程序收不到这个侧键）。

    必须成对抑制按下与抬起：只吞掉 DOWN 会让别的程序收到一个没有配对按下的抬起事件。
    """
    if not IS_WINDOWS:
        return
    from core.platform import hotkey_win32

    hotkey_win32.suppress_mouse_event(listener)
