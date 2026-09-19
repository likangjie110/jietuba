# -*- coding: utf-8 -*-
"""贴图窗口的动作表（pin/pin_actions.py）：注册表、配置收敛与分发。

贴图上的手势（滚轮/中键/双击/右键）能做什么由这张表决定。默认表必须复刻改造前
写死在 pin_window.py 里的行为，否则老用户升上来会发现「滚轮不缩放了」。
动作真正干什么由贴图窗口负责，所以这里用假窗口验证「哪个动作调了哪个方法」。
"""
from types import SimpleNamespace

import pytest

from pin import pin_actions


class _FakePin:
    """记录被调用的贴图窗口方法。"""

    def __init__(self, **results):
        self.calls = []
        self._results = results

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return self._results.get(name, True)

    def apply_zoom(self, direction, *, fine=False):
        return self._record("apply_zoom", direction, fine=fine)

    def adjust_opacity(self, direction):
        return self._record("adjust_opacity", direction)

    def reset_to_original_size(self):
        return self._record("reset_to_original_size")

    def save_image(self):
        return self._record("save_image")

    def rotate_image_cw(self):
        return self._record("rotate_image_cw")

    def toggle_stay_on_top(self):
        return self._record("toggle_stay_on_top")

    def toggle_border_effect(self):
        return self._record("toggle_border_effect")

    def copy_to_clipboard(self):
        return self._record("copy_to_clipboard")

    def copy_recognized_text(self):
        return self._record("copy_recognized_text")

    def request_translation(self):
        return self._record("request_translation")

    def toggle_thumbnail_mode(self):
        return self._record("toggle_thumbnail_mode")

    def toggle_toolbar(self):
        return self._record("toggle_toolbar")

    def toggle_lock(self):
        return self._record("toggle_lock")

    def show_context_menu_at_cursor(self):
        return self._record("show_context_menu_at_cursor")

    def close_selected_pins(self):
        return self._record("close_selected_pins")

    def copy_and_close(self):
        return self._record("copy_and_close")

    def close_window(self):
        return self._record("close_window")

    # 块 4 新增的贴图动作
    def toggle_click_through(self):
        return self._record("toggle_click_through")

    def toggle_focus_mode(self):
        return self._record("toggle_focus_mode")

    def close_other_pins(self):
        return self._record("close_other_pins")

    def load_image_from_file(self):
        return self._record("load_image_from_file")

    def recognize_text_now(self):
        return self._record("recognize_text_now")

    def start_crop_mode(self):
        return self._record("start_crop_mode")

    def apply_filter(self, kind):
        return self._record("apply_filter", kind)


class TestRegistry:
    def test_gestures_and_actions_are_unique(self):
        assert len(set(pin_actions.GESTURES)) == len(pin_actions.GESTURES)
        assert len(set(pin_actions.PIN_ACTIONS_BY_ID)) == len(pin_actions.PIN_ACTIONS)

    def test_defaults_cover_every_gesture(self):
        assert set(pin_actions.DEFAULT_PIN_MOUSE_ACTIONS) == set(pin_actions.GESTURES)

    def test_defaults_only_use_known_actions(self):
        for gesture, action_id in pin_actions.DEFAULT_PIN_MOUSE_ACTIONS.items():
            assert pin_actions.is_known_action(action_id), (gesture, action_id)

    def test_defaults_reproduce_the_old_hardcoded_behaviour(self):
        """改造前：滚轮缩放、Ctrl+滚轮透明度、右键菜单、中键与双击不做事。"""
        defaults = pin_actions.DEFAULT_PIN_MOUSE_ACTIONS
        assert defaults[pin_actions.GESTURE_WHEEL_UP] == "zoom_in"
        assert defaults[pin_actions.GESTURE_WHEEL_DOWN] == "zoom_out"
        assert defaults[pin_actions.GESTURE_CTRL_WHEEL_UP] == "opacity_up"
        assert defaults[pin_actions.GESTURE_CTRL_WHEEL_DOWN] == "opacity_down"
        assert defaults[pin_actions.GESTURE_RIGHT_CLICK] == "context_menu"
        assert defaults[pin_actions.GESTURE_MIDDLE_CLICK] == pin_actions.ACTION_NONE
        assert defaults[pin_actions.GESTURE_DOUBLE_CLICK] == pin_actions.ACTION_NONE


class TestNormalize:
    def test_missing_bindings_fall_back_to_defaults(self):
        assert pin_actions.normalize(None) == pin_actions.DEFAULT_PIN_MOUSE_ACTIONS
        assert pin_actions.normalize({}) == pin_actions.DEFAULT_PIN_MOUSE_ACTIONS

    def test_known_bindings_override_the_defaults(self):
        table = pin_actions.normalize({"wheel_up": "copy_and_close"})

        assert table["wheel_up"] == "copy_and_close"
        assert table["wheel_down"] == pin_actions.DEFAULT_PIN_MOUSE_ACTIONS["wheel_down"]

    def test_unbound_gesture_is_allowed(self):
        assert pin_actions.normalize({"middle_click": "none"})["middle_click"] == "none"

    def test_unknown_gesture_is_dropped(self, caplog):
        table = pin_actions.normalize({"triple_click": "close"})

        assert "triple_click" not in table

    def test_unknown_action_is_dropped(self, caplog):
        table = pin_actions.normalize({"wheel_up": "explode"})

        assert table["wheel_up"] == pin_actions.DEFAULT_PIN_MOUSE_ACTIONS["wheel_up"]


class TestResolve:
    def test_reads_through_the_config_manager(self):
        config = SimpleNamespace(get_pin_mouse_actions=lambda: {"wheel_up": "close"})

        assert pin_actions.resolve(config)["wheel_up"] == "close"

    def test_missing_config_manager_uses_defaults(self):
        assert pin_actions.resolve(None) == pin_actions.DEFAULT_PIN_MOUSE_ACTIONS

    def test_broken_getter_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setattr("core.logger.log_exception", lambda *_a, **_k: None)

        def _boom():
            raise RuntimeError("配置坏了")

        config = SimpleNamespace(get_pin_mouse_actions=_boom)

        assert pin_actions.resolve(config) == pin_actions.DEFAULT_PIN_MOUSE_ACTIONS


class TestRunAction:
    @pytest.mark.parametrize(
        ("action_id", "expected"),
        [
            ("zoom_in", ("apply_zoom", (1,), {"fine": False})),
            ("zoom_out", ("apply_zoom", (-1,), {"fine": False})),
            ("fine_zoom_in", ("apply_zoom", (1,), {"fine": True})),
            ("fine_zoom_out", ("apply_zoom", (-1,), {"fine": True})),
            ("opacity_up", ("adjust_opacity", (1,), {})),
            ("opacity_down", ("adjust_opacity", (-1,), {})),
            ("reset_size", ("reset_to_original_size", (), {})),
            ("save", ("save_image", (), {})),
            ("rotate_cw", ("rotate_image_cw", (), {})),
            ("toggle_stay_on_top", ("toggle_stay_on_top", (), {})),
            ("toggle_border", ("toggle_border_effect", (), {})),
            ("copy_content", ("copy_to_clipboard", (), {})),
            ("copy_text", ("copy_recognized_text", (), {})),
            ("translate", ("request_translation", (), {})),
            ("toggle_thumbnail", ("toggle_thumbnail_mode", (), {})),
            ("toggle_toolbar", ("toggle_toolbar", (), {})),
            ("toggle_lock", ("toggle_lock", (), {})),
            ("context_menu", ("show_context_menu_at_cursor", (), {})),
            ("close_selected", ("close_selected_pins", (), {})),
            ("copy_and_close", ("copy_and_close", (), {})),
            ("close", ("close_window", (), {})),
        ],
    )
    def test_every_action_calls_the_matching_window_method(self, action_id, expected):
        window = _FakePin()

        assert pin_actions.run_pin_action(action_id, window) is True
        assert window.calls == [expected]

    def test_every_registered_action_has_a_handler(self):
        """注册表里不该有「有名字但没人认领」的动作。"""
        for action in pin_actions.PIN_ACTIONS:
            assert pin_actions.run_pin_action(action.id, _FakePin()) is True, action.id

    def test_none_action_does_nothing(self):
        window = _FakePin()

        assert pin_actions.run_pin_action(pin_actions.ACTION_NONE, window) is False
        assert window.calls == []

    def test_unknown_action_is_reported_not_raised(self, caplog):
        window = _FakePin()

        assert pin_actions.run_pin_action("explode", window) is False
        assert window.calls == []

    def test_window_refusing_the_operation_reports_false(self):
        """窗口拒绝执行（例如 OCR 还没有结果）时要把 False 传回去。"""
        window = _FakePin(copy_recognized_text=False)

        assert pin_actions.run_pin_action("copy_text", window) is False


class TestSelectionAction:
    """「关闭选中的贴图」也是贴图动作表里的一员，可以绑到手势上。"""

    def test_action_is_registered(self):
        assert pin_actions.PIN_ACTIONS_BY_ID["close_selected"].label == "Close Selected Pins"

    def test_it_closes_the_selected_pins(self):
        window = _FakePin()

        assert pin_actions.run_pin_action("close_selected", window) is True
        assert window.calls == [("close_selected_pins", (), {})]

    def test_window_refusing_reports_false(self):
        window = _FakePin(close_selected_pins=False)

        assert pin_actions.run_pin_action("close_selected", window) is False
