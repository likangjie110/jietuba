# -*- coding: utf-8 -*-
"""平台层指针能力（core/platform/pointer）单元测试。

这组测试同时钉住迁移前三个真实缺陷：

1. ``stitch/scroll_window.py`` 把「Win32 鼠标穿透」和「启动 pynput 滚轮监听」写在同一个
   try 块里，非 Windows 上第一句就抛 AttributeError——**监听器永远不会启动**，长截图
   对滚动毫无反应。这里断言两件事已经分开，穿透失败不影响监听。
2. ``translation/smart_translation_controller.py`` 直接调 ``ctypes.windll``，非 Windows
   上智能翻译退化成手输原文。这里断言复制快捷键按平台映射。
3. ``gif/frame_recorder.py`` 的鼠标键状态在 Linux 上恒为 False 且无日志。这里断言
   Linux 会留下一条说明。

Windows / macOS 的分支都在本机（macOS）用假对象驱动，不需要真的跑在那些系统上。
"""

from types import SimpleNamespace

import pytest

from core.platform import pointer


@pytest.fixture(autouse=True)
def _clear_warning_memory():
    pointer.reset_warnings()
    yield
    pointer.reset_warnings()


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(pointer, "IS_WINDOWS", True)
    monkeypatch.setattr(pointer, "IS_MACOS", False)
    return pointer


@pytest.fixture
def as_macos(monkeypatch):
    monkeypatch.setattr(pointer, "IS_WINDOWS", False)
    monkeypatch.setattr(pointer, "IS_MACOS", True)
    return pointer


@pytest.fixture
def as_linux(monkeypatch):
    monkeypatch.setattr(pointer, "IS_WINDOWS", False)
    monkeypatch.setattr(pointer, "IS_MACOS", False)
    return pointer


class TestCursorPosition:
    def test_windows_uses_get_cursor_pos(self, as_windows, monkeypatch):
        class _FakeUser32:
            @staticmethod
            def GetCursorPos(byref):
                pt = byref._obj
                pt.x, pt.y = 120, 340
                return 1

        monkeypatch.setattr(
            pointer.ctypes, "windll", SimpleNamespace(user32=_FakeUser32()), raising=False
        )
        assert pointer.cursor_position() == (120, 340)

    def test_other_platforms_use_qt(self, as_macos):
        from PySide6.QtGui import QCursor

        pos = pointer.cursor_position()
        assert isinstance(pos, tuple) and len(pos) == 2
        assert pos == (QCursor.pos().x(), QCursor.pos().y())


class TestButtonState:
    def test_windows_reads_async_key_state(self, as_windows, monkeypatch):
        class _FakeUser32:
            @staticmethod
            def GetAsyncKeyState(vk):
                # 只把左键报成按下
                return 0x8000 if vk == pointer.VK_LBUTTON else 0

        monkeypatch.setattr(
            pointer.ctypes, "windll", SimpleNamespace(user32=_FakeUser32()), raising=False
        )
        assert pointer.is_button_pressed(pointer.BUTTON_LEFT) is True
        assert pointer.is_button_pressed(pointer.BUTTON_RIGHT) is False

    def test_macos_uses_quartz_without_accessibility(self, as_macos, monkeypatch):
        import sys

        captured = {}

        def _state(source_state, button):
            captured["args"] = (source_state, button)
            return button == 1

        module = type(sys)("Quartz")
        module.CGEventSourceButtonState = _state
        module.kCGEventSourceStateCombinedSessionState = "combined"
        monkeypatch.setitem(sys.modules, "Quartz", module)

        assert pointer.is_button_pressed(pointer.BUTTON_RIGHT) is True
        assert captured["args"] == ("combined", pointer.BUTTON_RIGHT)

    def test_macos_quartz_failure_is_false(self, as_macos):
        assert pointer.is_button_pressed(pointer.BUTTON_LEFT) is False

    def test_linux_reports_unsupported_once(self, as_linux, caplog):
        """恒为 False 是迁移前就有的行为，新增的是「为什么」——按帧调用的地方不能每次都写日志。"""
        assert pointer.is_button_pressed(pointer.BUTTON_LEFT) is False
        assert pointer.is_button_pressed(pointer.BUTTON_LEFT) is False
        assert pointer.is_button_pressed(pointer.BUTTON_RIGHT) is False
        assert "pointer_button_state" in pointer._warned


class TestScrollListener:
    def test_creates_and_starts_a_pynput_listener(self, monkeypatch):
        import sys

        started = []

        class _FakeListener:
            def __init__(self, on_scroll=None):
                self.on_scroll = on_scroll

            def start(self):
                started.append(True)

            def stop(self):
                started.append("stopped")

        module = type(sys)("pynput")
        mouse_module = type(sys)("pynput.mouse")
        mouse_module.Listener = _FakeListener
        module.mouse = mouse_module
        monkeypatch.setitem(sys.modules, "pynput", module)
        monkeypatch.setitem(sys.modules, "pynput.mouse", mouse_module)

        listener = pointer.create_scroll_listener(lambda *a: None)
        assert listener is not None
        assert started == [True]

        pointer.stop_listener(listener)
        assert started == [True, "stopped"]

    def test_missing_pynput_returns_none(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "pynput", None)
        monkeypatch.setitem(sys.modules, "pynput.mouse", None)
        assert pointer.create_scroll_listener(lambda *a: None) is None

    def test_error_callback_is_used(self, monkeypatch):
        import sys

        class _Boom:
            def __init__(self, **_kw):
                raise RuntimeError("没有辅助功能权限")

        module = type(sys)("pynput")
        mouse_module = type(sys)("pynput.mouse")
        mouse_module.Listener = _Boom
        module.mouse = mouse_module
        monkeypatch.setitem(sys.modules, "pynput", module)
        monkeypatch.setitem(sys.modules, "pynput.mouse", mouse_module)

        seen = []
        assert pointer.create_scroll_listener(lambda *a: None, on_error=seen.append) is None
        assert isinstance(seen[0], RuntimeError)

    def test_stopping_none_is_safe(self):
        pointer.stop_listener(None)

    def test_stopping_a_dead_listener_does_not_raise(self):
        class _Dead:
            def stop(self):
                raise RuntimeError("already stopped")

        pointer.stop_listener(_Dead())


class TestScrollInjection:
    def test_windows_sends_a_wheel_delta(self, as_windows, monkeypatch):
        import sys

        events = []

        win32api = type(sys)("win32api")
        win32api.mouse_event = lambda *a: events.append(a)
        win32con = type(sys)("win32con")
        win32con.MOUSEEVENTF_HWHEEL = pointer.MOUSEEVENTF_HWHEEL
        monkeypatch.setitem(sys.modules, "win32api", win32api)
        monkeypatch.setitem(sys.modules, "win32con", win32con)

        assert pointer.scroll_horizontal(1) is True
        assert events == [(pointer.MOUSEEVENTF_HWHEEL, 0, 0, pointer.WHEEL_DELTA, 0)]

    def test_windows_scales_with_clicks(self, as_windows, monkeypatch):
        import sys

        events = []
        win32api = type(sys)("win32api")
        win32api.mouse_event = lambda *a: events.append(a)
        win32con = type(sys)("win32con")
        win32con.MOUSEEVENTF_HWHEEL = pointer.MOUSEEVENTF_HWHEEL
        monkeypatch.setitem(sys.modules, "win32api", win32api)
        monkeypatch.setitem(sys.modules, "win32con", win32con)

        pointer.scroll_horizontal(3)
        assert events[0][3] == 3 * pointer.WHEEL_DELTA

    @pytest.mark.parametrize("platform", ["macos", "linux"])
    def test_other_platforms_report_unsupported(self, platform, monkeypatch):
        """迁移前这里会 ModuleNotFoundError（直接 import win32api），现在明确返回 False。"""
        monkeypatch.setattr(pointer, "IS_WINDOWS", False)
        monkeypatch.setattr(pointer, "IS_MACOS", platform == "macos")
        assert pointer.scroll_horizontal(1) is False
        assert "scroll_inject" in pointer._warned


class TestCopyShortcut:
    def _fake_windll(self, monkeypatch):
        events = []

        class _FakeUser32:
            @staticmethod
            def keybd_event(vk, _scan, flags, _extra):
                events.append((vk, flags))

        monkeypatch.setattr(
            pointer.ctypes, "windll", SimpleNamespace(user32=_FakeUser32()), raising=False
        )
        return events

    def test_windows_sends_ctrl_insert(self, as_windows, monkeypatch):
        """与迁移前 smart_translation 的实现逐字一致：Ctrl+Insert 而不是 Ctrl+C。"""
        events = self._fake_windll(monkeypatch)
        assert pointer.send_copy_shortcut() is True
        assert events == [
            (pointer.VK_CONTROL, 0),
            (pointer.VK_INSERT, 0),
            (pointer.VK_INSERT, pointer.KEYEVENTF_KEYUP),
            (pointer.VK_CONTROL, pointer.KEYEVENTF_KEYUP),
        ]

    def test_macos_uses_cmd_c(self, as_macos, monkeypatch):
        pressed = self._fake_pynput(monkeypatch)
        assert pointer.send_copy_shortcut() is True
        assert pressed == ["cmd", "c"]

    def test_linux_uses_ctrl_c(self, as_linux, monkeypatch):
        pressed = self._fake_pynput(monkeypatch)
        assert pointer.send_copy_shortcut() is True
        assert pressed == ["ctrl", "c"]

    def _fake_pynput(self, monkeypatch):
        import sys

        pressed = []

        class _Controller:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def press(self, key):
                pressed.append(getattr(key, "name", key))

            def release(self, _key):
                pass

        keyboard = type(sys)("pynput.keyboard")
        keyboard.Controller = _Controller
        keyboard.Key = SimpleNamespace(cmd=SimpleNamespace(name="cmd"),
                                       ctrl=SimpleNamespace(name="ctrl"))
        monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard)

        import pynput

        monkeypatch.setattr(pynput, "keyboard", keyboard, raising=False)
        return pressed

    def test_failure_is_reported_not_raised(self, as_windows, monkeypatch):
        class _Boom:
            @staticmethod
            def keybd_event(*_a):
                raise OSError("被拦截")

        monkeypatch.setattr(
            pointer.ctypes, "windll", SimpleNamespace(user32=_Boom()), raising=False
        )
        assert pointer.send_copy_shortcut() is False


class TestScrollWindowRegression:
    """长截图的滚动监听必须与鼠标穿透解耦。

    迁移前的故障：两者共用一个 try 块，Win32 穿透调用在前。非 Windows 上第一句就抛
    AttributeError 直接跳到 except，后面的 ``threading.Thread(_init_listener_bg)``
    永远不执行——长截图对滚动毫无反应，且不报错。
    """

    def test_mouse_transparency_failure_is_swallowed(self):
        """穿透失败必须只记日志：它抛出去就会把后面的监听器启动一起带走。"""
        import stitch.scroll_window as sw

        host = SimpleNamespace(transparent_area=None)  # winId() 取不到 -> 走异常分支
        sw.ScrollCaptureWindow.__dict__["_set_mouse_transparent"](host)  # 不应抛出

    def test_listener_starts_even_when_transparency_fails(self, monkeypatch):
        import stitch.scroll_window as sw

        started = []
        monkeypatch.setattr(
            sw.pointer, "create_scroll_listener", lambda *a, **k: started.append(True)
        )
        host = SimpleNamespace(
            transparent_area=None,
            mouse_listener=None,
            _on_scroll_event=lambda *a: None,
        )
        cls = sw.ScrollCaptureWindow
        cls._set_mouse_transparent(host)
        cls._start_scroll_listener(host)

        # 监听在后台线程里创建，等它一下
        import time

        deadline = time.time() + 3
        while not started and time.time() < deadline:
            time.sleep(0.01)
        assert started == [True], "鼠标穿透失败后滚轮监听仍然必须启动"

    def test_setup_runs_both_steps_without_a_shared_try(self):
        """两个步骤各有自己的异常边界，_setup_mouse_hook 本身不再兜住它们。"""
        import ast
        import inspect
        import textwrap

        import stitch.scroll_window as sw

        source = textwrap.dedent(inspect.getsource(sw.ScrollCaptureWindow._setup_mouse_hook))
        body = ast.parse(source).body[0].body

        assert not any(isinstance(node, ast.Try) for node in body), (
            "_setup_mouse_hook 里不该再有 try：两个步骤要各自独立"
        )
        called = [
            node.value.func.attr
            for node in body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
        ]
        assert called == ["_set_mouse_transparent", "_start_scroll_listener"]

    def test_scroll_window_no_longer_imports_native_input_modules(self):
        from platform_guard import all_imported_names

        import stitch.scroll_window as sw

        imported = all_imported_names(sw)
        assert "win32api" not in imported
        assert "win32con" not in imported

    def test_scroll_window_calls_the_platform_layer(self):
        """滚动注入与滚轮监听都经由 core/platform/pointer，而不是各自实现一遍。"""
        import ast
        import inspect
        import textwrap

        import stitch.scroll_window as sw

        tree = ast.parse(textwrap.dedent(inspect.getsource(sw)))
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "scroll_horizontal" in attrs
        assert "create_scroll_listener" in attrs


class TestFrameRecorderUsesThePlatformLayer:
    def test_no_direct_native_calls_left(self):
        from platform_guard import platform_violations

        import gif.frame_recorder as fr

        assert platform_violations(fr) == []

    def test_delegates_cursor_and_button_state(self, monkeypatch):
        import gif.frame_recorder as fr

        monkeypatch.setattr(pointer, "cursor_position", lambda: (11, 22))
        monkeypatch.setattr(pointer, "is_button_pressed", lambda b: b == 0)

        assert fr.FrameRecorder._get_cursor_pos() == (11, 22)
        assert fr.FrameRecorder._is_left_pressed() is True
        assert fr.FrameRecorder._is_right_pressed() is False
