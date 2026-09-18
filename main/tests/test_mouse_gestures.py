# -*- coding: utf-8 -*-
"""全局鼠标动作（块②的机制部分）：绑定配置、手势匹配、监听启停、动作执行。

平台层的手势词汇与监听器本身在 test_platform_pointer.py 里测；动作注册表与「静默截图」
的落点在这里测。真机验证（合成 Ctrl+滚轮触发一次静默截图）见工作区记忆里的手法说明。
"""

import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRectF, QSettings
from PySide6.QtGui import QImage

from core import actions
from core.platform import pointer as platform_pointer
from core.shortcut_manager import ShortcutManager
from settings.tool_settings import ToolSettingsManager


@pytest.fixture
def manager(monkeypatch):
    """不走单例、也不真的挂全局鼠标钩子的 ShortcutManager。"""
    import core.shortcut_manager as sm

    monkeypatch.setattr(sm.platform_hotkey, "keyboard_backend_is_native", lambda: False)
    monkeypatch.setattr(sm.platform_pointer, "create_mouse_listener",
                        lambda **kwargs: SimpleNamespace(stop=lambda: None))
    mgr = ShortcutManager()
    yield mgr
    mgr.unregister_all_hotkeys()


@pytest.fixture
def config(tmp_path):
    settings = QSettings(str(tmp_path / "gestures.ini"), QSettings.Format.IniFormat)
    return ToolSettingsManager(qsettings=settings)


class TestBindingsConfig:
    def test_default_is_empty(self, config):
        assert config.get_mouse_gestures() == {}

    def test_round_trip(self, config):
        config.set_mouse_gesture("screenshot_copy", "ctrl", "wheel_up")
        config.set_mouse_gesture("screenshot_pin", "", "middle_click")

        assert config.get_mouse_gestures() == {
            "screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"},
            "screenshot_pin": {"modifier": "", "gesture": "middle_click"},
        }

    def test_unbind_removes_the_action(self, config):
        config.set_mouse_gesture("screenshot_copy", "ctrl", "wheel_up")
        config.set_mouse_gesture("screenshot_copy", "", "")

        assert config.get_mouse_gestures() == {}

    def test_broken_json_is_an_empty_table(self, config):
        """手工把配置改坏不该让设置窗口打不开。"""
        config.qsettings.setValue("app/mouse_gestures", "{不是 json")

        assert config.get_mouse_gestures() == {}

    def test_non_dict_entries_are_dropped(self, config):
        config.qsettings.setValue(
            "app/mouse_gestures",
            json.dumps({"screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"},
                        "bad": "不是字典"}),
        )

        assert set(config.get_mouse_gestures()) == {"screenshot_copy"}


class TestCaptureBehaviorConfig:
    def test_overlay_defaults_to_off(self, config):
        assert config.get_mouse_capture_overlay_enabled() is False

    def test_overlay_round_trip(self, config):
        config.set_mouse_capture_overlay_enabled(True)
        assert config.get_mouse_capture_overlay_enabled() is True

    def test_ignored_apps_default_to_empty(self, config):
        assert config.get_mouse_ignored_apps() == []

    def test_ignored_apps_round_trip_and_drop_blanks(self, config):
        config.set_mouse_ignored_apps([" Finder ", "", "chrome", "  "])

        assert config.get_mouse_ignored_apps() == ["Finder", "chrome"]

    def test_broken_ignored_apps_fall_back_to_empty(self, config):
        """坏数据当空表：宁可什么都不过滤，也不要让鼠标动作整体失效。"""
        config.qsettings.setValue("app/mouse_ignored_apps", "{不是 json")

        assert config.get_mouse_ignored_apps() == []

    def test_non_list_ignored_apps_fall_back_to_empty(self, config):
        config.qsettings.setValue("app/mouse_ignored_apps", json.dumps({"a": 1}))

        assert config.get_mouse_ignored_apps() == []


class TestGestureMatching:
    def test_unknown_action_is_dropped(self, manager, caplog):
        manager.set_mouse_gestures({"no_such_action": {"modifier": "", "gesture": "wheel_up"}})
        assert manager.has_mouse_gestures() is False

    @pytest.mark.parametrize("binding", [
        {"modifier": "", "gesture": "left_double_click"},   # 不参与全局手势
        {"modifier": "hyper", "gesture": "wheel_up"},       # 不认识的修饰键
        {"modifier": "", "gesture": ""},                    # 没填手势
    ])
    def test_invalid_bindings_are_dropped(self, manager, binding):
        manager.set_mouse_gestures({"screenshot_copy": binding})
        assert manager.has_mouse_gestures() is False

    def test_scroll_triggers_the_bound_action(self, manager, monkeypatch):
        fired = []
        manager.mouse_gesture_triggered.connect(fired.append)
        manager.set_mouse_gestures({"screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"}})

        monkeypatch.setattr(platform_pointer, "pressed_modifiers", lambda: frozenset({"ctrl"}))
        manager._on_gesture_scroll(0, 0, 0, 3)          # 向上滚 = wheel_up

        assert fired == ["screenshot_copy"]

    def test_scroll_without_the_modifier_does_nothing(self, manager, monkeypatch):
        fired = []
        manager.mouse_gesture_triggered.connect(fired.append)
        manager.set_mouse_gestures({"screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"}})

        monkeypatch.setattr(platform_pointer, "pressed_modifiers", lambda: frozenset())
        manager._on_gesture_scroll(0, 0, 0, 3)

        assert fired == []

    def test_no_modifier_binding_requires_no_modifier(self, manager, monkeypatch):
        """要求「无修饰键」的绑定不能在按着 Ctrl 滚轮时被顺带触发。"""
        fired = []
        manager.mouse_gesture_triggered.connect(fired.append)
        manager.set_mouse_gestures({"screenshot_pin": {"modifier": "", "gesture": "wheel_down"}})

        monkeypatch.setattr(platform_pointer, "pressed_modifiers", lambda: frozenset({"ctrl"}))
        manager._on_gesture_scroll(0, 0, 0, -3)
        assert fired == []

        monkeypatch.setattr(platform_pointer, "pressed_modifiers", lambda: frozenset())
        manager._on_gesture_scroll(0, 0, 0, -3)
        assert fired == ["screenshot_pin"]

    def test_button_press_triggers_and_release_does_not(self, manager, monkeypatch):
        fired = []
        manager.mouse_gesture_triggered.connect(fired.append)
        manager.set_mouse_gestures({"screenshot_pin": {"modifier": "", "gesture": "middle_click"}})
        monkeypatch.setattr(platform_pointer, "pressed_modifiers", lambda: frozenset())

        button = SimpleNamespace(name="middle")
        manager._on_gesture_click(0, 0, button, True)
        manager._on_gesture_click(0, 0, button, False)

        assert fired == ["screenshot_pin"]

    def test_suppressed_gestures_are_ignored(self, manager, monkeypatch):
        """「禁用其他快捷键」期间手势也不该触发。"""
        fired = []
        manager.mouse_gesture_triggered.connect(fired.append)
        manager.set_mouse_gestures({"screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"}})
        monkeypatch.setattr(platform_pointer, "pressed_modifiers", lambda: frozenset({"ctrl"}))
        manager.set_global_hotkeys_suppressed(True)

        manager._on_gesture_scroll(0, 0, 0, 3)

        assert fired == []


class TestListenerLifecycle:
    def test_listener_starts_only_when_bound(self, manager, monkeypatch):
        import core.shortcut_manager as sm

        created, stopped = [], []

        class _Listener:
            def stop(self):
                stopped.append(True)

        monkeypatch.setattr(sm.platform_pointer, "create_mouse_listener",
                            lambda **kwargs: created.append(kwargs) or _Listener())

        manager.set_mouse_gestures({})
        assert created == []

        manager.set_mouse_gestures({"screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"}})
        assert len(created) == 1
        assert created[0]["on_scroll"] is not None
        assert created[0]["on_click"] is not None

        # 再设一次同样的绑定：不该重复起监听
        manager.set_mouse_gestures({"screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"}})
        assert len(created) == 1

        manager.set_mouse_gestures({})
        assert stopped == [True]

    def test_listener_start_failure_is_reported_not_raised(self, manager, monkeypatch):
        import core.shortcut_manager as sm

        monkeypatch.setattr(sm.platform_pointer, "create_mouse_listener", lambda **kwargs: None)

        manager.set_mouse_gestures({"screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"}})

        assert manager._mouse_gesture_listener is None


class _FakeApp:
    """只提供动作执行需要的东西。"""

    def __init__(self, config):
        self.config_manager = config
        self.screenshots = 0
        self.gesture_calls = []

    def start_screenshot(self):
        self.screenshots += 1

    def start_screenshot_for_gesture(self, mode, rect=None):
        self.gesture_calls.append((mode, rect))
        self.screenshots += 1
        return True


def _fake_capture(monkeypatch, image=None, rect=(120, 130, 200, 190)):
    """把静默截图替换成固定图；返回捕获到的调用记录。"""
    calls = []
    picture = image or QImage(80, 60, QImage.Format.Format_ARGB32)

    def fake_capture_at_cursor(app):
        calls.append(app)
        return picture, QRectF(*rect[:2], rect[2] - rect[0], rect[3] - rect[1])

    monkeypatch.setattr(actions, "capture_at_cursor", fake_capture_at_cursor)
    return calls, picture


class TestRunAction:
    def test_unknown_action_is_refused(self, monkeypatch, config):
        monkeypatch.setattr("core.logger.log_warning", lambda *_a, **_k: None)
        assert actions.run_action("nope", _FakeApp(config)) is False

    def test_editor_action_goes_through_the_app(self, monkeypatch, config):
        app = _FakeApp(config)
        monkeypatch.setattr(actions, "_copy", lambda image: True, raising=False)

        assert actions.run_action("screenshot", app) is True
        assert app.screenshots == 1

    def test_copy_action_sends_the_captured_image(self, monkeypatch, config):
        _fake_capture(monkeypatch)
        copied = []
        monkeypatch.setattr("core.clipboard_utils.deliver_image_async",
                            lambda image, **kwargs: copied.append((image, kwargs)) or "thread")

        assert actions.run_action("screenshot_copy", _FakeApp(config)) is True
        assert len(copied) == 1
        assert copied[0][1].get("copy_to_clipboard", True) is True

    def test_quick_save_action_saves_without_copying(self, monkeypatch, config):
        _fake_capture(monkeypatch)
        calls = []
        monkeypatch.setattr("core.clipboard_utils.deliver_image_async",
                            lambda image, **kwargs: calls.append(kwargs) or "thread")

        assert actions.run_action("screenshot_quick_save", _FakeApp(config)) is True
        assert calls and calls[0]["copy_to_clipboard"] is False
        assert calls[0]["save_service"] is not None

    def test_pin_action_creates_a_pin_at_the_captured_position(self, monkeypatch, config):
        _fake_capture(monkeypatch, rect=(120, 130, 200, 190))
        created = []

        class _Pin:
            def show(self):
                created.append("shown")

        class _Manager:
            @staticmethod
            def instance():
                return _Manager()

            def create_pin(self, **kwargs):
                created.append((kwargs["position"], kwargs["config_manager"]))
                return _Pin()

        monkeypatch.setattr("pin.pin_manager.PinManager", _Manager)

        assert actions.run_action("screenshot_pin", _FakeApp(config)) is True
        assert created[0] == (QPoint(120, 130), config)
        assert created[1] == "shown"

    def test_capture_failure_is_reported(self, monkeypatch, config):
        monkeypatch.setattr("core.logger.log_warning", lambda *_a, **_k: None)
        monkeypatch.setattr(actions, "capture_at_cursor", lambda app: None)

        assert actions.run_action("screenshot_copy", _FakeApp(config)) is False


class TestTargetRect:
    def test_none_mode_grabs_the_whole_screen(self, monkeypatch, config):
        config.set_ui_detection("none")
        assert actions._target_rect(_FakeApp(config)) is None

    def test_window_mode_uses_the_finder(self, monkeypatch, config):
        config.set_ui_detection("window")
        app = _FakeApp(config)

        class _Finder:
            def __init__(self, *a, **k):
                self.windows = []

            def find_windows(self):
                pass

            def find_rect_at_point(self, x, y, mode=None, margin=0, fallback_rect=None):
                assert mode == "window"
                return [10, 20, 110, 120]

        monkeypatch.setattr("core.platform.window.WindowFinder", _Finder)
        monkeypatch.setattr("core.platform.window.is_window_enumeration_available",
                            lambda: True)
        monkeypatch.setattr("PySide6.QtGui.QCursor.pos", staticmethod(lambda: QPoint(50, 50)))

        assert actions._target_rect(app) == [10, 20, 110, 120]


class TestEditorModeActions:
    """翻译/长截图/GIF 都走「打开编辑器 + 带上检测到的区域」。"""

    @pytest.mark.parametrize("action_id,mode", [
        ("screenshot_translate", actions.EDITOR_MODE_TRANSLATE),
        ("long_screenshot", actions.EDITOR_MODE_LONG),
        ("gif_capture", actions.EDITOR_MODE_GIF),
    ])
    def test_editor_action_passes_mode_and_rect(self, monkeypatch, config, action_id, mode):
        monkeypatch.setattr(actions, "_target_rect", lambda app: [10, 20, 110, 120])
        app = _FakeApp(config)

        assert actions.run_action(action_id, app) is True
        assert app.gesture_calls == [(mode, [10, 20, 110, 120])]

    def test_editor_action_without_a_detected_rect_still_opens(self, monkeypatch, config):
        monkeypatch.setattr(actions, "_target_rect", lambda app: None)
        app = _FakeApp(config)

        assert actions.run_action("long_screenshot", app) is True
        assert app.gesture_calls == [(actions.EDITOR_MODE_LONG, None)]

    def test_every_registered_action_has_a_runner(self, config):
        """注册表里不该出现「有名字但没人认领」的动作。"""
        handled_by_app_entry = {"screenshot", *actions.APP_ENTRY_ACTIONS}
        for action in actions.ACTIONS:
            assert (
                action.silent_capture
                or action.editor_mode
                or action.id in handled_by_app_entry
            ), action.id


class TestIgnoredApps:
    def test_no_list_means_nothing_is_ignored(self, monkeypatch, config):
        monkeypatch.setattr("core.platform.focus.foreground_app_name", lambda: "Finder")

        assert actions.gesture_ignored_app_name(_FakeApp(config)) is None

    @pytest.mark.parametrize("listed,front", [
        ("Finder", "Finder"),          # 同名
        ("finder", "Finder"),          # 大小写不敏感
        ("Finder.app", "Finder"),      # 用户写了 .app 后缀
        ("Finder", "Finder.app"),      # 系统给的名字带后缀
        ("chrome", "chrome.exe"),      # Windows 的可执行文件名
    ])
    def test_names_match_loosely(self, monkeypatch, config, listed, front):
        config.set_mouse_ignored_apps([listed])
        monkeypatch.setattr("core.platform.focus.foreground_app_name", lambda: front)

        assert actions.gesture_ignored_app_name(_FakeApp(config)) == front

    def test_other_apps_are_not_ignored(self, monkeypatch, config):
        config.set_mouse_ignored_apps(["Finder"])
        monkeypatch.setattr("core.platform.focus.foreground_app_name", lambda: "Safari")

        assert actions.gesture_ignored_app_name(_FakeApp(config)) is None

    def test_matching_is_not_a_substring_match(self, monkeypatch, config):
        """「忽略 Finder」不等于「忽略 Finder 里的所有子窗口」。"""
        config.set_mouse_ignored_apps(["Find"])
        monkeypatch.setattr("core.platform.focus.foreground_app_name", lambda: "Finder")

        assert actions.gesture_ignored_app_name(_FakeApp(config)) is None

    def test_unknown_foreground_app_cannot_be_ignored(self, monkeypatch, config):
        config.set_mouse_ignored_apps(["Finder"])
        monkeypatch.setattr("core.platform.focus.foreground_app_name", lambda: None)

        assert actions.gesture_ignored_app_name(_FakeApp(config)) is None


class TestCopyTextAction:
    def test_copy_text_refuses_a_null_image(self, monkeypatch, config):
        monkeypatch.setattr("core.logger.log_warning", lambda *_a, **_k: None)

        assert actions._copy_text(QImage()) is False

    def test_copy_text_runs_off_the_main_thread(self, monkeypatch, config, qapp):
        monkeypatch.setattr("core.logger.log_warning", lambda *_a, **_k: None)
        # OCR 不可用时线程立刻收尾；这样测的是线程生命周期而不是识别结果
        monkeypatch.setattr("ocr.is_ocr_available", lambda: False)

        assert actions._copy_text(QImage(20, 20, QImage.Format.Format_ARGB32)) is True
        thread = actions._ocr_thread_refs[-1][0]
        assert thread.wait(5000) is True
        qapp.processEvents()          # 收尾信号要回到主线程才会放开引用
        assert actions._ocr_thread_refs == []

    def test_recognized_text_goes_to_the_clipboard(self, qapp):
        sink = actions._TextClipboardSink()
        sink.on_text("识别结果")

        assert qapp.clipboard().text() == "识别结果"

    def test_empty_text_is_not_written(self, monkeypatch, qapp):
        monkeypatch.setattr("core.logger.log_warning", lambda *_a, **_k: None)
        qapp.clipboard().setText("旧内容")

        actions._TextClipboardSink().on_text("")

        assert qapp.clipboard().text() == "旧内容"


class TestGestureEntryPoint:
    """MainApp 这一层只做两件事：查忽略列表、把动作交给 run_action。"""

    def _app(self, config):
        return _FakeApp(config)

    def test_ignored_app_skips_the_action(self, monkeypatch, config):
        from main_app import MainApp

        config.set_mouse_ignored_apps(["Finder"])
        monkeypatch.setattr("core.platform.focus.foreground_app_name", lambda: "Finder")
        ran = []
        monkeypatch.setattr(actions, "run_action", lambda aid, app: ran.append(aid) or True)

        MainApp._on_mouse_gesture(self._app(config), "screenshot_copy")

        assert ran == []

    def test_other_apps_run_the_action(self, monkeypatch, config):
        from main_app import MainApp

        config.set_mouse_ignored_apps(["Finder"])
        monkeypatch.setattr("core.platform.focus.foreground_app_name", lambda: "Safari")
        ran = []
        monkeypatch.setattr(actions, "run_action", lambda aid, app: ran.append(aid) or True)

        MainApp._on_mouse_gesture(self._app(config), "screenshot_copy")

        assert ran == ["screenshot_copy"]

    def test_action_failure_is_logged_not_raised(self, monkeypatch, config):
        from main_app import MainApp

        monkeypatch.setattr("core.logger.log_exception", lambda *_a, **_k: None)

        def _boom(_aid, _app):
            raise RuntimeError("boom")

        monkeypatch.setattr(actions, "run_action", _boom)
        MainApp._on_mouse_gesture(self._app(config), "screenshot_copy")


class TestEditorModePlumbing:
    """「打开编辑器并进入某模式」要穿过一次异步抓屏才到达窗口。"""

    def _main_app(self, window, pending):
        return SimpleNamespace(
            config_manager=SimpleNamespace(),
            screenshot_window=window,
            _pending_editor_call=pending,
            _activate_blocking_modal=lambda: False,
            _prompt_missing_capture_permission=lambda: None,
        )

    def test_gesture_request_is_stored_before_capturing(self, monkeypatch, config):
        from main_app import MainApp

        app = self._main_app(None, None)
        app.start_screenshot = lambda: setattr(app, "_started", True)

        assert MainApp.start_screenshot_for_gesture(app, "long", [1, 2, 3, 4]) is True
        assert app._pending_editor_call == ("long", [1, 2, 3, 4])
        assert app._started is True

    def test_pending_request_reaches_the_window(self, monkeypatch, qapp):
        from main_app import MainApp

        applied = []
        window = SimpleNamespace(
            prepare_new_session=lambda image, rect: None,
            apply_editor_mode=lambda mode, rect: applied.append((mode, rect)) or True,
        )
        app = self._main_app(window, ("long", [10, 20, 110, 120]))

        MainApp._on_capture_ready(app, None, None)

        assert applied == [("long", [10, 20, 110, 120])]
        assert app._pending_editor_call is None

    def test_plain_capture_does_not_enter_a_mode(self, monkeypatch, qapp):
        from main_app import MainApp

        applied = []
        window = SimpleNamespace(
            prepare_new_session=lambda image, rect: None,
            apply_editor_mode=lambda mode, rect: applied.append((mode, rect)),
        )
        app = self._main_app(window, None)

        MainApp._on_capture_ready(app, None, None)

        assert applied == []

    def test_modal_window_drops_the_request(self, monkeypatch, qapp):
        """抓屏期间弹出模态窗口时这次不打开编辑器，请求也不能留到下一次。"""
        from main_app import MainApp

        applied = []
        window = SimpleNamespace(apply_editor_mode=lambda mode, rect: applied.append(mode))
        app = self._main_app(window, ("long", None))
        app._activate_blocking_modal = lambda: True

        MainApp._on_capture_ready(app, None, None)

        assert applied == []
        assert app._pending_editor_call is None

    def test_busy_capture_drops_the_request(self, monkeypatch, config):
        """已有会话/抓屏线程在跑时不会打开新会话，留下的请求会污染下一次普通截图。"""
        from main_app import MainApp

        class _Running:
            @staticmethod
            def isRunning():
                return True

        app = SimpleNamespace(
            config_manager=config,
            screenshot_window=None,
            _pending_editor_call=("long", None),
            _capture_thread=_Running(),
            _activate_blocking_modal=lambda: False,
        )
        MainApp.start_screenshot(app)

        assert app._pending_editor_call is None


class TestGestureSelectionRect:
    """平台层给的是 ``[左, 上, 右, 下]``，不是 QRectF（真机上踩过一次）。"""

    def test_platform_rect_list_becomes_a_selection_rect(self):
        from ui.screenshot_window import gesture_selection_rect

        rect = gesture_selection_rect([277, 70, 1805, 1190])

        assert (rect.x(), rect.y(), rect.width(), rect.height()) == (277.0, 70.0, 1528.0, 1120.0)

    def test_a_rectf_passes_through(self):
        from ui.screenshot_window import gesture_selection_rect

        assert gesture_selection_rect(QRectF(1, 2, 3, 4)) == QRectF(1, 2, 3, 4)

    @pytest.mark.parametrize("value", [None, [], [1, 2]])
    def test_malformed_input_degrades_to_an_empty_rect(self, value):
        from ui.screenshot_window import gesture_selection_rect

        assert gesture_selection_rect(value).isEmpty()


class TestCaptureMask:
    def test_mask_is_shown_when_enabled(self, monkeypatch, config, qapp):
        config.set_mouse_capture_overlay_enabled(True)
        shown = []
        monkeypatch.setattr("ui.capture_mask.flash_capture_mask",
                            lambda rect: shown.append(rect))

        actions._flash_capture_mask(QRectF(10, 20, 30, 40), _FakeApp(config))

        assert shown == [QRectF(10, 20, 30, 40)]

    def test_mask_is_skipped_when_disabled(self, monkeypatch, config, qapp):
        shown = []
        monkeypatch.setattr("ui.capture_mask.flash_capture_mask",
                            lambda rect: shown.append(rect))

        actions._flash_capture_mask(QRectF(10, 20, 30, 40), _FakeApp(config))

        assert shown == []

    def test_mask_failure_does_not_break_the_action(self, monkeypatch, config, qapp):
        """遮罩只是提示，出问题不能把已经抓到的图丢掉。"""
        config.set_mouse_capture_overlay_enabled(True)
        monkeypatch.setattr("core.logger.log_exception", lambda *_a, **_k: None)

        def _boom(rect):
            raise RuntimeError("no screen")

        monkeypatch.setattr("ui.capture_mask.flash_capture_mask", _boom)
        actions._flash_capture_mask(QRectF(10, 20, 30, 40), _FakeApp(config))


class TestMaskWidget:
    def test_overlay_covers_the_virtual_desktop(self, qapp):
        from ui.capture_mask import CaptureMaskOverlay

        overlay = CaptureMaskOverlay(QRectF(10, 20, 30, 40), duration_ms=0)
        try:
            primary = qapp.primaryScreen()
            expected = primary.virtualGeometry()
            assert overlay.geometry() == expected
            assert overlay._hole.width() == 30
            assert overlay._hole.height() == 40
        finally:
            overlay.close()
            overlay.deleteLater()
            qapp.processEvents()

    def test_invalid_rect_shows_nothing(self, qapp):
        from ui.capture_mask import flash_capture_mask

        assert flash_capture_mask(QRectF()) is None
