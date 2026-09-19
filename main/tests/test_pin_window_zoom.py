# -*- coding: utf-8 -*-
"""
贴图窗口的缩放、透明度与拖拽测试（pin/pin_window.py）

pin_window.py 有 580 条语句、覆盖率 16%，是 pin 包最大的窟窿。它的 __init__
会建画布、建 CanvasView、建 OCR 管理器、注册到快捷键单例、并且无条件 show()，
真实构造要打十几个桩，性价比很低。

但它最核心的交互算术全部内联在 wheelEvent 里——缩放倍率的上下限钳制、100%
吸附、透明度步长与边界——这些是用户每天都在碰的路径，算错的表现是窗口缩到
看不见、或者放大到超出屏幕。拖拽位移换算同理。这些逻辑只读 self 上的几个数值，
不需要窗口真的存在。

隔离方式：以未绑定方式调用真实实现，用 SimpleNamespace 充当 self。
这里必须用 SimpleNamespace 而不是 MagicMock，有两个原因：源码用
hasattr(self, '_image_transform') 决定基准尺寸从哪来，MagicMock 会让它恒为真；
而且 _thumbnail_mode 等是类上的 property，假 self 用普通属性才能绕开它们。
"""
from contextlib import contextmanager
from types import MethodType, SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt

from pin import pin_actions, pin_window as pin_window_module
from pin.pin_window import PinWindow

NO_MOD = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier

BASE_W, BASE_H = 400, 300
# 源码用 max(50/宽, 50/高) 保证窗口任一边不小于 50 像素
MIN_SCALE = max(50.0 / BASE_W, 50.0 / BASE_H)


class _FakeWheelEvent:
    def __init__(self, delta, modifiers=NO_MOD):
        self._delta = delta
        self._mods = modifiers
        self.ignored = 0

    def angleDelta(self):
        return QPoint(0, self._delta)

    def modifiers(self):
        return self._mods

    def ignore(self):
        self.ignored += 1


def _bind_action_dispatch(fake):
    """把「手势 → 动作」的分派绑到假窗口上。

    wheelEvent 现在只负责认出是哪个手势（滚轮上/下、带不带 Ctrl），算术在 apply_zoom /
    adjust_opacity 里、绑定关系在动作表里。把真实实现绑到假窗口上，测的就还是同一段算术。
    """
    fake.gesture = lambda kind: pin_actions.DEFAULT_PIN_MOUSE_ACTIONS.get(
        kind, pin_actions.ACTION_NONE)
    fake.run_gesture = lambda kind: pin_actions.run_pin_action(fake.gesture(kind), fake)
    fake.apply_zoom = lambda direction, fine=False: PinWindow.apply_zoom(
        fake, direction, fine=fine)
    fake.adjust_opacity = lambda direction: PinWindow.adjust_opacity(fake, direction)
    return fake


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)

    @property
    def called(self):
        return bool(self.calls)


def _zoom_self(scale_factor=1.0, transform_size=None, thumbnail=False):
    """
    组装一个只够 wheelEvent 缩放分支使用的假窗口。

    不设 canvas（源码 `if self.canvas` 判空后跳过缓存失效），
    transform_size 为 None 时也不设 _image_transform，让 hasattr 走 else 分支
    从 _orig_size 取基准尺寸。
    """
    fake = SimpleNamespace(
        _thumbnail_mode=thumbnail,
        _is_scaling=False,
        _orig_size=QSize(BASE_W, BASE_H),
        scale_factor=scale_factor,
        config_manager=None,          # 没有配置就是默认步长（1.05）
        canvas=None,
        x=lambda: 10,
        y=lambda: 20,
        setGeometry=_Recorder(),
        update=_Recorder(),
        _scale_timer=SimpleNamespace(start=_Recorder()),
        _show_zoom_percent=_Recorder(),
    )
    if transform_size is not None:
        fake._image_transform = SimpleNamespace(
            display_size=lambda orig: transform_size)
    return _bind_action_dispatch(fake)


def _opacity_self(opacity=1.0, thumbnail=False):
    return _bind_action_dispatch(SimpleNamespace(
        _thumbnail_mode=thumbnail,
        _win_opacity=opacity,
        config_manager=None,          # 没有配置就是默认步长（0.05）
        setWindowOpacity=_Recorder(),
        _show_hint_label=_Recorder(),
    ))


def _scroll(fake, times, delta=120, modifiers=NO_MOD):
    for _ in range(times):
        PinWindow.wheelEvent(fake, _FakeWheelEvent(delta, modifiers))


# ============================================================================
# 缩放
# ============================================================================

class TestZoomClamping:

    def test_zooming_in_saturates_at_four_times(self):
        fake = _zoom_self()
        _scroll(fake, 40)
        assert fake.scale_factor == 4.0

    def test_zooming_out_saturates_at_the_minimum_scale(self):
        """下限保证窗口最短边不小于 50 像素，否则贴图会缩成看不见的一点"""
        fake = _zoom_self()
        _scroll(fake, 40, delta=-120)
        assert fake.scale_factor == pytest.approx(MIN_SCALE)

    def test_the_minimum_scale_follows_the_image_shape(self):
        """细长图和方图的下限不同，取宽高两个约束里更严的那个"""
        tall = _zoom_self(transform_size=QSize(1000, 60))
        _scroll(tall, 60, delta=-120)
        assert tall.scale_factor == pytest.approx(max(50.0 / 1000, 50.0 / 60))

    def test_window_never_shrinks_below_fifty_pixels_on_its_shorter_side(self):
        fake = _zoom_self()
        _scroll(fake, 40, delta=-120)
        _, _, width, height = fake.setGeometry.calls[-1]
        assert min(width, height) >= 50

    def test_a_single_step_scales_by_five_percent(self):
        fake = _zoom_self()
        _scroll(fake, 1)
        assert fake.scale_factor == pytest.approx(1.05)

    def test_zooming_out_uses_the_reciprocal_so_a_round_trip_is_lossless(self):
        """
        缩小必须用放大倍率的倒数而不是 0.95，否则放大再缩小会累积误差，
        窗口回不到原始尺寸。
        """
        fake = _zoom_self()
        _scroll(fake, 1)
        _scroll(fake, 1, delta=-120)
        assert fake.scale_factor == 1.0

    def test_passing_through_one_hundred_percent_snaps_exactly(self):
        """互逆浮点运算会在 1.0 附近留下极小误差，源码用 1e-6 阈值吸附"""
        fake = _zoom_self()
        for _ in range(5):
            _scroll(fake, 1)
        for _ in range(5):
            _scroll(fake, 1, delta=-120)
        assert fake.scale_factor == 1.0


class TestZoomGeometry:

    def test_window_size_is_computed_from_the_base_size_not_the_current_one(self):
        """
        每次都从原图逻辑尺寸乘缩放比算，避免按已取整的窗口尺寸反复取整而漂移。
        """
        fake = _zoom_self()
        _scroll(fake, 1)
        x, y, width, height = fake.setGeometry.calls[-1]
        assert (width, height) == (round(BASE_W * 1.05), round(BASE_H * 1.05))

    def test_window_position_is_preserved_while_scaling(self):
        fake = _zoom_self()
        _scroll(fake, 3)
        for x, y, _, _ in fake.setGeometry.calls:
            assert (x, y) == (10, 20)

    def test_rotated_images_scale_from_the_transformed_size(self):
        """旋转 90 度后宽高互换，缩放必须以变换后的尺寸为基准"""
        fake = _zoom_self(transform_size=QSize(BASE_H, BASE_W))
        _scroll(fake, 1)
        _, _, width, height = fake.setGeometry.calls[-1]
        assert (width, height) == (round(BASE_H * 1.05), round(BASE_W * 1.05))

    def test_size_never_degenerates_to_zero(self):
        tiny = _zoom_self(transform_size=QSize(1, 1))
        _scroll(tiny, 40, delta=-120)
        _, _, width, height = tiny.setGeometry.calls[-1]
        assert width >= 1 and height >= 1

    def test_scaling_marks_the_smoothing_flag_and_arms_the_timer(self):
        """滚动中用快速缩放，停下来后由定时器触发一次高质量重绘"""
        fake = _zoom_self()
        _scroll(fake, 1)
        assert fake._is_scaling is True
        assert fake._scale_timer.start.called
        assert fake._show_zoom_percent.called
        assert fake.update.called

    def test_the_canvas_cache_is_invalidated_when_a_canvas_exists(self):
        fake = _zoom_self()
        invalidate = _Recorder()
        fake.canvas = SimpleNamespace(invalidate_cache=invalidate)
        _scroll(fake, 1)
        assert invalidate.called


class TestZoomPercentLabel:

    def test_percentage_is_rounded_not_truncated(self):
        cases = {1.0: "100%", 1.05: "105%", 0.16666666666666666: "17%",
                 4.0: "400%", 0.999: "100%"}
        for scale, expected in cases.items():
            label = _Recorder()
            fake = SimpleNamespace(scale_factor=scale, _show_hint_label=label)
            PinWindow._show_zoom_percent(fake)
            assert label.calls == [(expected,)], scale


# ============================================================================
# 透明度
# ============================================================================

class TestOpacity:

    def test_ctrl_scroll_down_reduces_opacity_by_five_percent(self):
        fake = _opacity_self(1.0)
        _scroll(fake, 1, delta=-120, modifiers=CTRL)
        assert fake._win_opacity == pytest.approx(0.95)
        assert fake.setWindowOpacity.calls == [(pytest.approx(0.95),)]

    def test_opacity_is_clamped_to_fully_opaque(self):
        fake = _opacity_self(1.0)
        _scroll(fake, 5, modifiers=CTRL)
        assert fake._win_opacity == 1.0

    def test_opacity_is_clamped_at_fifteen_percent(self):
        """再往下就几乎看不见了，下限保证窗口不会被误操作成隐形"""
        fake = _opacity_self(0.15)
        _scroll(fake, 5, delta=-120, modifiers=CTRL)
        assert fake._win_opacity == 0.15

    def test_opacity_walks_down_and_back_up(self):
        fake = _opacity_self(1.0)
        _scroll(fake, 4, delta=-120, modifiers=CTRL)
        assert fake._win_opacity == pytest.approx(0.80)
        _scroll(fake, 2, modifiers=CTRL)
        assert fake._win_opacity == pytest.approx(0.90)

    def test_ctrl_scroll_does_not_touch_the_scale(self):
        fake = _opacity_self(1.0)
        fake.scale_factor = 1.0
        fake.setGeometry = _Recorder()
        _scroll(fake, 1, modifiers=CTRL)
        assert fake.scale_factor == 1.0
        assert fake.setGeometry.calls == []

    def test_opacity_label_truncates_instead_of_rounding(self):
        """
        已知缺陷，不是回归：透明度标签用 int(_win_opacity * 100) 截断，
        而累积浮点误差让 0.9 变成 0.8999999999999999，标签因此显示 89%。
        同文件的 _show_zoom_percent 用的是 int(round(...))，两处不一致。
        修成 round 之后这条用例会失败，届时应把预期改成 90%。
        """
        fake = _opacity_self(1.0)
        _scroll(fake, 2, delta=-120, modifiers=CTRL)
        assert fake._win_opacity == pytest.approx(0.90)
        assert fake._show_hint_label.calls[-1] == ("α 89%",)

    def test_the_first_step_down_happens_to_label_correctly(self):
        fake = _opacity_self(1.0)
        _scroll(fake, 1, delta=-120, modifiers=CTRL)
        assert fake._show_hint_label.calls[-1] == ("α 95%",)


# ============================================================================
# 缩略图模式下滚轮被忽略
# ============================================================================

class TestThumbnailModeIgnoresWheel:

    def test_scale_is_left_untouched(self):
        fake = _zoom_self(thumbnail=True)
        event = _FakeWheelEvent(120)
        PinWindow.wheelEvent(fake, event)
        assert fake.scale_factor == 1.0
        assert fake.setGeometry.calls == []
        assert event.ignored == 1

    def test_ctrl_scroll_is_ignored_too(self):
        fake = _opacity_self(1.0, thumbnail=True)
        event = _FakeWheelEvent(-120, CTRL)
        PinWindow.wheelEvent(fake, event)
        assert fake._win_opacity == 1.0
        assert event.ignored == 1
        assert fake.setWindowOpacity.calls == []


# ============================================================================
# 拖拽
# ============================================================================

@contextmanager
def _system_move(answer):
    """把「交给系统拖动」这个平台边界换成固定答案，并记录被问过的窗口。

    这是窗口管理器那一侧（平台层），用替身；被测的是 PinWindow 拿到答案之后
    走哪条路：系统接管就自己收尾，被拒就自己每帧搬窗口。
    """
    calls = []

    def fake(window):
        calls.append(window)
        return answer

    original = pin_window_module.window_ops.start_system_move
    pin_window_module.window_ops.start_system_move = fake
    try:
        yield calls
    finally:
        pin_window_module.window_ops.start_system_move = original


def _platform_accepts_system_move():
    return _system_move(True)


def _platform_refuses_system_move():
    return _system_move(False)


def _drag_self(dragging=False, start_pos=QPoint(100, 100),
               window_pos=QPoint(500, 400), *, selected=False, followed=False):
    fake = SimpleNamespace(
        _is_dragging=dragging,
        _drag_start_pos=start_pos,
        _drag_start_window_pos=window_pos,
        _toolbar_follows_natively=followed,
        toolbar=None,
        move=_Recorder(),
        setCursor=_Recorder(),
        pos=lambda: window_pos,
        x=lambda: window_pos.x(),
        y=lambda: window_pos.y(),
        tr=lambda text: text,
        is_selected=lambda: selected,
        end_window_drag=_Recorder(),    # 系统接管后由 start_window_drag 调用
    )
    # 真实实现按未绑定方式调用，但它内部还会走 self._let_system_drag()，所以把那份
    # 真实实现绑到这个假 self 上，测的仍是「问平台 → 按答案决定走哪条路」。
    # 「窗口有没有移动余地」单独测（TestSystemDragRoom），这里固定为「有」。
    fake._system_drag_has_room = lambda: True
    fake._let_system_drag = MethodType(PinWindow._let_system_drag, fake)
    return fake


class TestWindowDrag:

    def test_starting_a_drag_records_the_anchor_and_changes_the_cursor(self):
        fake = _drag_self()
        with _platform_refuses_system_move():
            PinWindow.start_window_drag(fake, QPoint(150, 160))
        assert fake._is_dragging is True
        assert fake._drag_start_pos == QPoint(150, 160)
        assert fake._drag_start_window_pos == QPoint(500, 400)
        assert fake.setCursor.calls == [(Qt.CursorShape.ClosedHandCursor,)]

    def test_the_system_takes_over_when_the_platform_accepts_it(self):
        """系统拖动是阻塞的（返回即松手），所以按下时就同步收尾，不再自己搬窗口。"""
        fake = _drag_self()
        with _platform_accepts_system_move():
            PinWindow.start_window_drag(fake, QPoint(150, 160))
        assert fake.end_window_drag.called
        assert fake.move.calls == []

    def test_a_selected_pin_keeps_the_manual_path(self):
        """多选成组拖动不能交给系统：系统一次只搬一个窗口，同组其它贴图得我们搬。"""
        fake = _drag_self(selected=True)
        with _platform_accepts_system_move() as calls:
            PinWindow.start_window_drag(fake, QPoint(150, 160))
        assert calls == []
        assert not fake.end_window_drag.called

    def test_a_platform_refusal_falls_back_to_manual_dragging(self):
        fake = _drag_self()
        with _platform_refuses_system_move() as calls:
            PinWindow.start_window_drag(fake, QPoint(150, 160))
        assert len(calls) == 1                     # 问过平台，被拒
        assert not fake.end_window_drag.called
        PinWindow.update_window_drag(fake, QPoint(160, 170))
        assert fake.move.calls == [(QPoint(510, 410),)]

    def test_the_window_follows_the_pointer_delta(self):
        fake = _drag_self(dragging=True)
        PinWindow.update_window_drag(fake, QPoint(130, 90))
        # 指针右移 30、上移 10，窗口同量移动
        assert fake.move.calls == [(QPoint(530, 390),)]

    def test_several_moves_are_all_measured_from_the_original_anchor(self):
        """位移始终相对按下时的锚点算，而不是累加上一次的增量"""
        fake = _drag_self(dragging=True)
        for point, expected in ((QPoint(110, 110), QPoint(510, 410)),
                                (QPoint(120, 120), QPoint(520, 420)),
                                (QPoint(90, 95), QPoint(490, 395))):
            PinWindow.update_window_drag(fake, point)
            assert fake.move.calls[-1] == (expected,)

    def test_moving_without_a_started_drag_does_nothing(self):
        fake = _drag_self(dragging=False)
        PinWindow.update_window_drag(fake, QPoint(999, 999))
        assert fake.move.calls == []

    def test_ending_a_drag_restores_the_cursor(self):
        fake = _drag_self(dragging=True)
        fake._sync_toolbar_with_window = _Recorder()
        PinWindow.end_window_drag(fake)
        assert fake._is_dragging is False
        assert fake.setCursor.calls == [(Qt.CursorShape.ArrowCursor,)]

    def test_ending_a_drag_that_never_started_is_a_no_op(self):
        fake = _drag_self(dragging=False)
        fake._sync_toolbar_with_window = _Recorder()
        PinWindow.end_window_drag(fake)
        assert fake.setCursor.calls == []
        assert not fake._sync_toolbar_with_window.called

    def test_a_visible_toolbar_is_kept_in_sync(self):
        sync = _Recorder()
        fake = _drag_self()
        fake.toolbar = SimpleNamespace(
            isVisible=lambda: True, sync_with_pin_window=sync)
        PinWindow._sync_toolbar_with_window(fake)
        assert sync.called

    def test_a_hidden_toolbar_is_not_synced(self):
        sync = _Recorder()
        fake = _drag_self()
        fake.toolbar = SimpleNamespace(
            isVisible=lambda: False, sync_with_pin_window=sync)
        PinWindow._sync_toolbar_with_window(fake)
        assert not sync.called

    def test_an_attached_toolbar_is_not_moved_by_us_any_more(self):
        """工具栏挂成原生跟随窗口后由系统带着走，我们每帧再搬一次只是白花时间。"""
        sync = _Recorder()
        fake = _drag_self(followed=True)
        fake.toolbar = SimpleNamespace(
            isVisible=lambda: True, sync_with_pin_window=sync)
        PinWindow._sync_toolbar_with_window(fake)
        assert not sync.called


class TestSystemDragRoom:
    """「窗口在屏幕可用区里还留得下移动余地吗」——决定拖动交给系统还是自己搬。

    系统的窗口拖动会把窗口夹在屏幕可用区里（实测可拖动范围 = 可用区 − 窗口尺寸），
    贴着屏幕大小的贴图交给系统就等于动不了，因此大到一定程度必须继续自己搬。
    """

    @staticmethod
    def _room(width, height):
        window = SimpleNamespace(
            width=lambda: width, height=lambda: height,
            screen=lambda: SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 2000, 1200)))
        return PinWindow._system_drag_has_room(window)

    def test_a_small_pin_has_room(self):
        assert self._room(800, 600) is True

    def test_a_full_screen_pin_has_no_room(self):
        assert self._room(1900, 1150) is False

    def test_the_rule_looks_at_both_axes(self):
        assert self._room(800, 1150) is False      # 高度顶满
        assert self._room(1900, 600) is False      # 宽度顶满

    def test_a_missing_screen_is_a_no(self):
        window = SimpleNamespace(width=lambda: 100, height=lambda: 100,
                                 screen=lambda: None)
        assert PinWindow._system_drag_has_room(window) is False


# ============================================================================
# 依赖子管理器的属性回退
# ============================================================================

class TestManagerBackedProperties:
    """
    这四个属性都写成 `self._xxx.yyy if hasattr(self, '_xxx') else 兜底`，
    保护窗口在子管理器还没建好或已清理时不至于抛 AttributeError。
    property 是类上的描述符，用 fget 直接喂假 self 即可测。
    """

    def test_values_are_read_through_to_the_ocr_manager(self):
        layer = object()
        fake = SimpleNamespace(_ocr_mgr=SimpleNamespace(
            ocr_text_layer=layer, has_result=True, text_selection_enabled=True))
        assert PinWindow.ocr_text_layer.fget(fake) is layer
        assert PinWindow._ocr_has_result.fget(fake) is True
        assert PinWindow._text_selection_enabled.fget(fake) is True

    def test_a_missing_ocr_manager_falls_back_safely(self):
        bare = SimpleNamespace()
        assert PinWindow.ocr_text_layer.fget(bare) is None
        assert PinWindow._ocr_has_result.fget(bare) is False
        assert PinWindow._text_selection_enabled.fget(bare) is False

    def test_thumbnail_state_is_read_through_to_its_manager(self):
        for active in (True, False):
            fake = SimpleNamespace(_thumbnail=SimpleNamespace(active=active))
            assert PinWindow._thumbnail_mode.fget(fake) is active

    def test_a_missing_thumbnail_manager_reports_inactive(self):
        assert PinWindow._thumbnail_mode.fget(SimpleNamespace()) is False


# ============================================================================
# 交互参数改成可配置之后（贴图设置页）
# ============================================================================

class TestConfigurableSteps:
    """步长以前写死在 wheelEvent 里，现在读配置；范围与设置页共用一份常量。"""

    def test_zoom_step_comes_from_the_config(self):
        fake = _zoom_self()
        fake.config_manager = SimpleNamespace(get_pin_zoom_step=lambda: 1.25)

        _scroll(fake, 1)

        assert fake.scale_factor == pytest.approx(1.25)

    def test_zoom_out_uses_the_reciprocal_of_the_configured_step(self):
        fake = _zoom_self()
        fake.config_manager = SimpleNamespace(get_pin_zoom_step=lambda: 1.25)

        _scroll(fake, 1, delta=-120)

        assert fake.scale_factor == pytest.approx(1 / 1.25)

    def test_opacity_step_comes_from_the_config(self):
        fake = _opacity_self()
        fake.config_manager = SimpleNamespace(get_pin_opacity_step=lambda: 0.2)

        _scroll(fake, 1, delta=-120, modifiers=CTRL)

        assert fake._win_opacity == pytest.approx(0.8)

    def test_a_broken_config_getter_falls_back_to_the_default_step(self, monkeypatch):
        monkeypatch.setattr("core.logger.log_exception", lambda *_a, **_k: None)
        fake = _zoom_self()

        def _boom():
            raise RuntimeError("配置坏了")

        fake.config_manager = SimpleNamespace(get_pin_zoom_step=_boom)

        _scroll(fake, 1)

        assert fake.scale_factor == pytest.approx(1.05)


class TestPinConfigHelpers:
    def test_missing_manager_or_getter_uses_the_default(self):
        from pin.pin_window import pin_config_float, pin_config_value

        assert pin_config_value(None, "get_pin_zoom_step", 1.05) == 1.05
        assert pin_config_value(SimpleNamespace(), "get_pin_zoom_step", 1.05) == 1.05
        assert pin_config_float(None, "get_pin_zoom_step", 1.05, (1.01, 1.30)) == 1.05

    def test_values_are_clamped_to_the_shared_range(self):
        from pin.pin_window import pin_config_float

        high = SimpleNamespace(get_pin_zoom_step=lambda: 9.0)
        low = SimpleNamespace(get_pin_zoom_step=lambda: 0.5)

        assert pin_config_float(high, "get_pin_zoom_step", 1.05, (1.01, 1.30)) == 1.30
        assert pin_config_float(low, "get_pin_zoom_step", 1.05, (1.01, 1.30)) == 1.01

    @pytest.mark.parametrize("bad", ["abc", None, object()])
    def test_unusable_values_fall_back_to_the_default(self, bad):
        from pin.pin_window import pin_config_float

        fake = SimpleNamespace(get_pin_zoom_step=lambda: bad)

        assert pin_config_float(fake, "get_pin_zoom_step", 1.05, (1.01, 1.30)) == 1.05

    def test_reading_failure_is_reported_not_raised(self, monkeypatch):
        from pin.pin_window import pin_config_float

        monkeypatch.setattr("core.logger.log_exception", lambda *_a, **_k: None)

        def _boom():
            raise RuntimeError("配置坏了")

        fake = SimpleNamespace(get_pin_zoom_step=_boom)

        assert pin_config_float(fake, "get_pin_zoom_step", 1.05, (1.01, 1.30)) == 1.05

    def test_bool_helper_coerces(self):
        from pin.pin_window import pin_config_bool

        assert pin_config_bool(SimpleNamespace(get_pin_shadow_enabled=lambda: False),
                              "get_pin_shadow_enabled", True) is False
        assert pin_config_bool(None, "get_pin_shadow_enabled", True) is True


# ============================================================================
# 手势 → 动作的分派（贴图手势表）
# ============================================================================

class TestGestureDispatch:
    """wheelEvent 只认手势，具体动作由 pin/pin_actions.py 的表决定。"""

    def test_gesture_reads_the_configured_table(self):
        fake = SimpleNamespace(config_manager=SimpleNamespace(
            get_pin_mouse_actions=lambda: {"wheel_up": "copy_content"}))

        assert PinWindow.gesture(fake, "wheel_up") == "copy_content"

    def test_gesture_falls_back_to_defaults_for_missing_keys(self):
        fake = SimpleNamespace(config_manager=SimpleNamespace(get_pin_mouse_actions=lambda: {}))

        assert PinWindow.gesture(fake, "wheel_up") == "zoom_in"

    def test_gesture_without_a_config_manager_uses_defaults(self):
        assert PinWindow.gesture(SimpleNamespace(config_manager=None), "right_click") == (
            "context_menu")

    def test_run_gesture_executes_the_bound_action(self):
        calls = []
        fake = SimpleNamespace(
            config_manager=SimpleNamespace(
                get_pin_mouse_actions=lambda: {"middle_click": "copy_content"}),
            copy_to_clipboard=lambda: calls.append("copy"),
        )
        fake.gesture = lambda kind: PinWindow.gesture(fake, kind)

        assert PinWindow.run_gesture(fake, "middle_click") is True
        assert calls == ["copy"]

    def test_unbound_gesture_does_nothing(self):
        calls = []
        fake = SimpleNamespace(
            config_manager=SimpleNamespace(
                get_pin_mouse_actions=lambda: {"middle_click": "none"}),
            copy_to_clipboard=lambda: calls.append("copy"),
        )
        fake.gesture = lambda kind: PinWindow.gesture(fake, kind)

        assert PinWindow.run_gesture(fake, "middle_click") is False
        assert calls == []

    def test_thumbnails_do_not_zoom(self):
        """缩略图模式把滚轮让给别的控件（源码里 event.ignore()）。"""
        fake = _zoom_self(thumbnail=True)

        _scroll(fake, 1)

        assert fake.scale_factor == 1.0

    def test_locked_pins_do_not_zoom(self, caplog):
        fake = _zoom_self()
        fake._locked = True

        _scroll(fake, 1)

        assert fake.scale_factor == 1.0


class TestApplyZoom:
    def test_fine_zoom_uses_a_smaller_step(self):
        fake = _zoom_self()
        fake.config_manager = SimpleNamespace(get_pin_zoom_step=lambda: 1.25)

        PinWindow.apply_zoom(fake, 1, fine=True)

        # 精细步长 = 1 + (1.25 - 1) / 5 = 1.05
        assert fake.scale_factor == pytest.approx(1.05)

    def test_zero_direction_is_a_no_op(self):
        fake = _zoom_self()

        assert PinWindow.apply_zoom(fake, 0) is False
        assert fake.scale_factor == 1.0
