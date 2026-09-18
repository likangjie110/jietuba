# -*- coding: utf-8 -*-
"""macOS：pynput 读键盘布局必须留在主线程，否则进程会被 SIGTRAP 打死。

pynput 的 Darwin 键盘后端用 ctypes 调 Carbon 的输入源接口读当前键盘布局，有两个调用点：

- **监听线程**：``pynput.keyboard._darwin.Listener._run`` 一进线程就 ``with keycode_context()``
  （实现在 ``pynput/_util/darwin.py``）；取到的值只存进 ``self._context``，pynput 自己从不读它。
- **构造线程**：``Controller.__init__`` 调 ``get_unicode_to_keycode_map()`` 建「字符 → 物理
  键码」映射，注入 Cmd+C 这类按键时要用。

那组接口（``TISGetInputSourceProperty``，落到 HIToolbox 的 ``TSMGetInputSourceProperty``）
会去读本进程当前的输入源列表。**程序是前台应用**时（macOS 26.6 实测），
``isValidateInputSourceRef`` 会走进 ``islGetInputSourceListWithAdditions``，而它断言必须在
主队列上（``dispatch_assert_queue``）——这不是异常，是 ``EXC_BREAKPOINT``，崩溃报告里
``SIGTRAP``、线程停在 ctypes 调用里。进程不是前台应用时同样的调用不断言，所以这个坑只在
「窗口刚被激活」这类时刻现形。真实路径：欢迎向导关闭 → ``update_hotkey()`` → 监听线程
启动 → 进程没了。

最小复现（本机实测必崩，退出码 133）：窗口 show 之后 ``NSApp.activateIgnoringOtherApps_(True)``，
再在子线程里调一次 ``keycode_context()``。

所以这里把「问 Carbon 要键盘布局」收成主线程专属：主线程调用照旧真取并把结果缓存下来，
非主线程只读缓存、永远不会自己去调 Carbon；缓存还没建立时给空上下文/空映射并记一次日志
——监听器本来就不读上下文，注入退化成合成 Unicode 字符串，两条路都不会崩。
"""

import contextlib
import threading

from core.platform.detection import IS_MACOS

# pynput 的原始实现，install() 时存下来
_original_keycode_context = None
_original_unicode_map = None

_installed = False
_cached_context = None
_cached_unicode_map = None
_warned_off_main_thread = False


def install() -> bool:
    """把 pynput 的键盘布局查询收成主线程专属（幂等）。

    要在创建 pynput 键盘监听器 / ``keyboard.Controller`` 之前调用。本身不做 Carbon 调用，
    缓存是主线程第一次真正取布局时才填的。非 macOS、pynput 缺失或内部结构变了返回 False
    ——调用方忽略返回值即可，这是防崩补丁，不是功能前置条件。
    """
    global _installed, _original_keycode_context, _original_unicode_map

    if _installed:
        return True
    if not IS_MACOS:
        return False

    try:
        from pynput._util import darwin as util_darwin
        from pynput.keyboard import _darwin as keyboard_darwin
    except Exception as e:
        from core.logger import log_warning, T

        log_warning(
            T("pynput 的 Darwin 后端不可用，跳过键盘布局主线程收口: {e}", e=e),
            "Pynput",
        )
        return False

    if getattr(util_darwin, "keycode_context", None) is _main_thread_keycode_context:
        # 补丁还在（_installed 被重置过），别把补丁本身当原始实现再包一层
        _installed = True
        return True

    original_context = getattr(util_darwin, "keycode_context", None)
    original_map = getattr(util_darwin, "get_unicode_to_keycode_map", None)
    if original_context is None or original_map is None:
        # 结构对不上就别打补丁：打歪了比不打更难查
        from core.logger import log_warning, T

        log_warning(
            T("pynput 内部接口已变化，键盘布局主线程收口未安装（键盘监听可能崩进程）"),
            "Pynput",
        )
        return False

    _original_keycode_context = original_context
    _original_unicode_map = original_map

    util_darwin.keycode_context = _main_thread_keycode_context
    util_darwin.get_unicode_to_keycode_map = _main_thread_unicode_map
    # pynput.keyboard._darwin 是 `from ... import keycode_context` 按名字导入的，
    # Listener._run / Controller.__init__ 取的是它自己模块里的属性，必须一并替换
    keyboard_darwin.keycode_context = _main_thread_keycode_context
    keyboard_darwin.get_unicode_to_keycode_map = _main_thread_unicode_map

    _installed = True
    return True


@contextlib.contextmanager
def _main_thread_keycode_context():
    """``pynput._util.darwin.keycode_context`` 的替代实现。"""
    global _cached_context

    if threading.current_thread() is threading.main_thread():
        with _original_keycode_context() as context:
            _cached_context = context
            yield context
        return

    if _cached_context is not None:
        yield _cached_context
        return

    _warn_once()
    yield (None, None)


def _main_thread_unicode_map():
    """``pynput._util.darwin.get_unicode_to_keycode_map`` 的替代实现。

    原实现会拿上下文去调 ``UCKeyTranslate``，上下文是空的时候传进去的是空指针，
    所以非主线程没有缓存时宁可不建映射（注入退化成合成 Unicode 字符串）。
    """
    global _cached_unicode_map

    if threading.current_thread() is threading.main_thread():
        _cached_unicode_map = _original_unicode_map()
        return _cached_unicode_map

    if _cached_unicode_map is not None:
        return _cached_unicode_map

    _warn_once()
    return {}


def _warn_once() -> None:
    global _warned_off_main_thread

    if _warned_off_main_thread:
        return
    _warned_off_main_thread = True

    from core.logger import log_warning, T

    log_warning(
        T("非主线程请求键盘布局（Carbon 输入源接口只能在主线程调用），已退回缓存值或空值"),
        "Pynput",
    )
