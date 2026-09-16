# -*- coding: utf-8 -*-
"""剪贴板「粘贴回原程序」的编排。

两半的实现都在平台层（``core/platform/focus`` 记前台目标并切回、``pointer`` 注入
粘贴键），这里测 ``clipboard/controllers`` 怎么用它们：

- 窗口显示时记下前台目标（在显示我们自己的窗口之前，否则记到的是自己）；
- 粘贴时先切回目标、延迟一点再发粘贴键——目标是外部程序时，焦点切换是异步的，
  立刻发键会打到自己身上；
- 目标为空时仍然写出粘贴键（用户可能已经手动切好了窗口），但没有任何目标时不该崩。

平台差异本身（Windows 的 SetForegroundWindow、macOS 的 activateWithOptions_、
Cmd+V 与 Ctrl+V 的映射）在 test_platform_clipboard.py 与 test_platform_pointer.py。
"""

import pytest

from clipboard.controllers import clipboard_controller as cc


@pytest.fixture
def controller():
    """只有被调用到的那两个字段的控制器。

    完整构造要一个 ClipboardManager（会连真实剪贴板历史），而这里测的是粘贴编排，
    与数据来源无关，所以直接绕过 __init__。
    """
    instance = cc.ClipboardController.__new__(cc.ClipboardController)
    instance.auto_paste_enabled = True
    instance._previous_target = None
    return instance


class TestForegroundIsCapturedBeforeShowing:
    def test_window_show_records_the_target(self, controller, monkeypatch):
        """必须在显示剪贴板窗口之前记录，否则记到的是我们自己。"""
        captured = []
        monkeypatch.setattr(cc, "capture_foreground",
                            lambda: captured.append(True) or "target")
        monkeypatch.setattr(controller, "load_history", lambda: None)

        controller.on_window_show()

        assert captured == [True]
        assert controller._previous_target == "target"


class TestPasteDelegatesToPlatformLayer:
    def _run_paste(self, controller, monkeypatch, target="previous-window",
                   capability=True):
        """执行一次自动粘贴，返回 (切回调用, 发键调用)。

        QTimer.singleShot 换成同步执行，免得用例要等真实的 50ms + 30ms。
        """
        activated, sent = [], []
        monkeypatch.setattr(cc, "activate_foreground", lambda t: activated.append(t))
        monkeypatch.setattr(cc, "send_paste_shortcut", lambda: sent.append(True))
        monkeypatch.setattr(cc, "available", lambda *a, **k: capability)

        import PySide6.QtCore as qtcore

        monkeypatch.setattr(qtcore.QTimer, "singleShot",
                            lambda _ms, fn: fn() if callable(fn) else None)
        controller._previous_target = target
        controller._schedule_paste_to_previous_window()
        return activated, sent

    def test_activates_then_pastes(self, controller, monkeypatch):
        activated, sent = self._run_paste(controller, monkeypatch)
        assert activated == ["previous-window"]
        assert sent == [True]

    def test_pastes_without_a_recorded_target(self, controller, monkeypatch):
        """没记到目标也要发键：用户可能已经手动把窗口切好了。"""
        activated, sent = self._run_paste(controller, monkeypatch, target=None)
        assert sent == [True]
        assert activated == []

    def test_respects_the_auto_paste_setting(self, controller, monkeypatch):
        controller.auto_paste_enabled = False
        activated, sent = self._run_paste(controller, monkeypatch)
        assert (activated, sent) == ([], [])

    def test_unsupported_platform_skips_and_warns_once(self, controller, monkeypatch):
        """平台没有自动粘贴能力时不该装作发了键，也不该每次粘贴都刷日志。"""
        monkeypatch.setattr(cc, "_paste_unsupported_logged", False)
        logged = []
        monkeypatch.setattr(cc, "log_info", lambda *a, **k: logged.append(a))

        activated, sent = self._run_paste(controller, monkeypatch, capability=False)
        controller._schedule_paste_to_previous_window()

        assert (activated, sent) == ([], []), "不该调用焦点切换或发键"
        assert len(logged) == 1, "只该提醒一次"


class TestPlatformLayerIsUsedDirectly:
    def test_controller_no_longer_owns_native_calls(self):
        """控制器里不该再有裸 ctypes.windll 或 AppKit——那正是平台层要收口的东西。"""
        import inspect

        source = inspect.getsource(cc)
        assert "ctypes" not in source
        assert "AppKit" not in source
        assert "keybd_event" not in source

    def test_imports_come_from_the_platform_layer(self):
        from core.platform import focus, pointer

        assert cc.capture_foreground is focus.capture_foreground
        assert cc.activate_foreground is focus.activate_foreground
        assert cc.send_paste_shortcut is pointer.send_paste_shortcut


class TestCapabilityGating:
    def test_auto_paste_capability_reflects_the_platform(self):
        from core.platform import Capability, Support, available, support

        assert support(Capability.PASTE_TO_APP, "windows") is Support.FULL
        assert support(Capability.PASTE_TO_APP, "macos") is Support.DEGRADED
        assert available(Capability.PASTE_TO_APP, "linux") is False


class TestExportedNames:
    def test_controllers_package_no_longer_exports_the_old_helpers(self):
        """旧入口已迁到平台层；留在导出列表里会让人以为还有两条路。"""
        import clipboard.controllers as controllers

        for name in ("get_foreground_window", "set_foreground_window", "send_ctrl_v"):
            assert not hasattr(controllers, name), name


class TestDeliverImageAsyncUsesPlatformClipboard:
    def test_clipboard_write_goes_through_the_platform_layer(self, monkeypatch):
        from PySide6.QtGui import QImage

        from core import clipboard_utils

        calls = []
        monkeypatch.setattr(clipboard_utils, "clipboard_clipboard_copy",
                            lambda img: calls.append(id(img)))

        image = QImage(4, 4, QImage.Format.Format_ARGB32)
        image.fill(0xFF112233)

        assert clipboard_utils.deliver_image_async(image, save_service=None) is None
        assert calls == [id(image)]

    def test_null_image_is_skipped(self):
        from core import clipboard_utils

        assert clipboard_utils.deliver_image_async(None) is None
