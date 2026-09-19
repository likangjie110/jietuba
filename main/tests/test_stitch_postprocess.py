# -*- coding: utf-8 -*-
"""长截图后期：接缝修正、固定条消除、超长图分段。

判据都在像素上：位移量正好等于 ±1/±10、固定条里的特征色确实不见了、分段拼回来与原图
逐像素相同。合成图是刻意造的——真机滚动截图有随机性，不适合当断言依据。
"""

import pytest
from types import SimpleNamespace

from PIL import Image, ImageDraw

from stitch import postprocess


def striped(width=40, height=120, *, start=0, band=12) -> Image:
    """横向条纹图：每条 12px 一色，方便一眼看出「哪几行被挪走了」。"""
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for index, top in enumerate(range(0, height, band)):
        color = ((index * 37 + start) % 200, (index * 61 + start) % 200,
                 (index * 91 + start) % 200)
        draw.rectangle([0, top, width, min(top + band - 1, height - 1)], fill=color)
    return image


def frame_with_header(offset: int, *, width=40, height=80, header=16) -> Image:
    """一帧：顶部 16px 是固定标题栏（所有帧一样），下面是随之变化的内容。"""
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, header - 1], fill=(10, 20, 30))     # 固定标题栏
    for index, top in enumerate(range(header, height, 10)):
        shift = (index + offset) % 5
        draw.rectangle([0, top, width, min(top + 9, height - 1)],
                       fill=(40 + shift * 20, 80, 120))
    return image


class TestShiftSeam:
    def test_positive_delta_pushes_content_down(self):
        image = striped(height=120)
        marker = image.getpixel((5, 60))

        output = postprocess.shift_seam(image, 60, 10)

        assert output.size == (image.size[0], 130)
        # 接缝以上不动，以下整体下移 10px
        assert output.getpixel((5, 50)) == image.getpixel((5, 50))
        assert output.getpixel((5, 70)) == marker

    def test_negative_delta_pulls_content_up(self):
        image = striped(height=120)

        output = postprocess.shift_seam(image, 60, -10)

        assert output.size == (image.size[0], 110)
        assert output.getpixel((5, 50)) == image.getpixel((5, 50))
        assert output.getpixel((5, 60)) == image.getpixel((5, 70))

    def test_one_pixel_step_moves_exactly_one_pixel(self):
        image = striped(height=60)
        up = postprocess.shift_seam(image, 30, 1)
        down = postprocess.shift_seam(image, 30, -1)

        assert up.size[1] == 61 and down.size[1] == 59
        assert up.getpixel((5, 31)) == image.getpixel((5, 30))
        assert down.getpixel((5, 30)) == image.getpixel((5, 31))

    def test_zero_delta_returns_a_copy(self):
        image = striped(height=60)
        output = postprocess.shift_seam(image, 30, 0)
        assert output.size == image.size and output is not image

    def test_seam_outside_the_image_is_clamped(self):
        image = striped(height=60)
        assert postprocess.shift_seam(image, 999, 5).size[1] == 65
        assert postprocess.shift_seam(image, -5, -5).size[1] == 55

    def test_removing_more_than_available_stops_at_the_bottom(self):
        image = striped(height=60)
        assert postprocess.shift_seam(image, 55, -50).size[1] == 55


class TestFixedBands:
    def test_detects_a_fixed_header(self):
        frames = [frame_with_header(offset) for offset in range(4)]
        assert postprocess.detect_fixed_band(frames, "top") == 16

    def test_detects_a_fixed_footer(self):
        frames = []
        for offset in range(3):
            frame = frame_with_header(offset)
            draw = ImageDraw.Draw(frame)
            draw.rectangle([0, frame.size[1] - 12, frame.size[0], frame.size[1] - 1],
                           fill=(200, 30, 30))
            frames.append(frame)
        assert postprocess.detect_fixed_band(frames, "bottom") == 12

    def test_content_without_a_fixed_band_reports_zero(self):
        frames = [striped(height=60, start=index * 3) for index in range(3)]
        assert postprocess.detect_fixed_band(frames, "top") == 0

    def test_a_single_frame_is_not_enough_evidence(self):
        assert postprocess.detect_fixed_band([frame_with_header(0)], "top") == 0
        assert postprocess.detect_fixed_band([], "top") == 0

    def test_removing_the_band_drops_its_pixels(self):
        """真实拼接结果里固定标题栏只出现一次（拼接引擎去重），所以成品 = 一个标题栏 + 全部内容。"""
        frames = [frame_with_header(offset) for offset in range(3)]
        header = 16
        stitched = Image.new("RGB", (40, header + 3 * (80 - header)))
        stitched.paste(frames[0].crop((0, 0, 40, header)), (0, 0))
        y = header
        for frame in frames:
            stitched.paste(frame.crop((0, header, 40, 80)), (0, y))
            y += 80 - header

        band = postprocess.detect_fixed_band(frames, "top")
        output = postprocess.remove_fixed_bands(stitched, top=band)

        assert band == header
        assert output.size[1] == stitched.size[1] - header
        header_color = (10, 20, 30)
        assert all(output.getpixel((x, y)) != header_color
                   for y in range(output.size[1]) for x in range(0, 40, 5))
        # 内容整体上移：原来紧跟标题栏的第一行内容现在贴在顶部
        assert output.getpixel((5, 0)) == stitched.getpixel((5, header))

    def test_no_bands_returns_a_copy(self):
        image = striped(height=60)
        output = postprocess.remove_fixed_bands(image, top=0, bottom=0)
        assert output.size == image.size

    def test_taller_bands_than_the_image_are_refused(self):
        image = striped(height=20)
        assert postprocess.remove_fixed_bands(image, top=30).size == image.size


class TestSplitSegments:
    def test_short_image_stays_one_segment(self):
        image = striped(height=100)
        assert len(postprocess.split_segments(image, 200)) == 1

    def test_zero_limit_disables_splitting(self):
        image = striped(height=100)
        assert len(postprocess.split_segments(image, 0)) == 1

    def test_each_segment_respects_the_limit(self):
        image = striped(height=250)

        segments = postprocess.split_segments(image, 100)

        assert [segment.size[1] for segment in segments] == [100, 100, 50]
        assert all(segment.size[1] <= 100 for segment in segments)

    def test_segments_rebuild_the_original_exactly(self):
        image = striped(height=250, start=7)

        segments = postprocess.split_segments(image, 100)

        rebuilt = Image.new(image.mode, image.size)
        y = 0
        for segment in segments:
            rebuilt.paste(segment, (0, y))
            y += segment.size[1]
        assert rebuilt.tobytes() == image.tobytes()

    def test_no_image_gives_no_segments(self):
        assert postprocess.split_segments(None, 100) == []


class _RecordingSaveService:
    """假保存服务：只记录「被要求保存的图片尺寸与后缀」。"""

    def __init__(self):
        self.saved = []

    def save_pil_async(self, image, **kwargs):
        self.saved.append({"size": image.size, "suffix": kwargs.get("suffix"),
                           "prefix": kwargs.get("prefix")})
        return "/tmp/not-a-real-file.png"


class _StubWindow:
    """驱动 ``ScrollWindow`` 的后期与保存逻辑所需的最小接收者。

    真窗口要起一次真实抓屏会话（mss + 置顶遮罩）才能构造，那在测试里既慢又不稳；
    这里把同一批方法挂到替身上跑——被执行的是**产品代码**，只是省掉了窗口本身。
    """

    scroll_direction = "vertical"
    scroll_locked_direction = "down"
    save_directory = ""

    def __init__(self, frames, result, service):
        from stitch.scroll_window import ScrollCaptureWindow

        self.screenshots = frames
        self.stitched_result = result
        self.save_service = service
        self._postprocess_result = lambda: ScrollCaptureWindow._postprocess_result(self)
        self._segment_limit = lambda: ScrollCaptureWindow._segment_limit(self)

    def save(self):
        from stitch.scroll_window import ScrollCaptureWindow

        ScrollCaptureWindow._save_result(self)


def _configure(tmp_settings, *, fixed_bands, segment_height):
    """把设置写进测试用的全局配置单例（产品代码就是从它读的）。"""
    from settings import get_tool_settings_manager

    config = get_tool_settings_manager()
    config.set_stitch_remove_fixed_bands(fixed_bands)
    config.set_stitch_max_segment_height(segment_height)
    return config


class TestSavePathAppliesThePostprocess:
    """真实保存路径：落盘前消除固定条、按上限分段逐段保存。"""

    def test_fixed_band_is_dropped_before_saving(self, tmp_settings):
        _configure(tmp_settings, fixed_bands=True, segment_height=0)

        header = 16
        frames = [frame_with_header(offset) for offset in range(3)]
        result = Image.new("RGB", (40, header + 3 * (80 - header)))
        result.paste(frames[0].crop((0, 0, 40, header)), (0, 0))
        y = header
        for frame in frames:
            result.paste(frame.crop((0, header, 40, 80)), (0, y))
            y += 80 - header
        service = _RecordingSaveService()

        window = _StubWindow(frames, result.copy(), service)
        window.save()

        assert window.stitched_result.size[1] == result.size[1] - header
        assert service.saved and service.saved[0]["size"] == window.stitched_result.size

    def test_long_result_is_saved_as_segments(self, tmp_settings):
        _configure(tmp_settings, fixed_bands=False, segment_height=2048)

        service = _RecordingSaveService()
        window = _StubWindow([striped(height=100)], striped(height=5000), service)

        window.save()

        assert [entry["size"][1] for entry in service.saved] == [2048, 2048, 904]
        assert [entry["suffix"] for entry in service.saved] == ["縦_1", "縦_2", "縦_3"]

    def test_disabled_postprocess_changes_nothing(self, tmp_settings):
        _configure(tmp_settings, fixed_bands=False, segment_height=0)

        frames = [frame_with_header(offset) for offset in range(3)]
        result = Image.new("RGB", (40, 200), "white")
        service = _RecordingSaveService()

        window = _StubWindow(frames, result, service)
        window.save()

        assert window.stitched_result.size == (40, 200)
        assert len(service.saved) == 1


class _SeamStub:
    """驱动 ``ScrollCaptureWindow`` 的接缝修正入口（同一个理由：不构造真窗口）。"""

    scroll_locked_direction = "down"

    def __init__(self, result, frames=(), panel_height=100):

        from stitch.scroll_window import ScrollCaptureWindow

        self.stitched_result = result
        self.screenshots = list(frames)
        self.preview_panel = SimpleNamespace(
            preview_label=SimpleNamespace(height=lambda: panel_height))
        self.refreshed = 0
        self._refresh_preview_panel = lambda: setattr(
            self, "refreshed", self.refreshed + 1)
        self._preview_center_in_result = lambda: (
            ScrollCaptureWindow._preview_center_in_result(self))
        self._ScrollCaptureWindow = ScrollCaptureWindow

    def correct(self, delta):
        return self._ScrollCaptureWindow._on_seam_correct(self, delta)

    def center(self):
        return self._ScrollCaptureWindow._preview_center_in_result(self)


class TestSeamCorrectionEntry:
    """接缝修正在窗口里的入口：位移量正好等于用户点的那个值。"""

    def test_plus_one_changes_the_height_by_exactly_one(self):
        stub = _SeamStub(striped(height=300), frames=[1, 2])

        assert stub.correct(1) is True

        assert stub.stitched_result.size[1] == 301
        assert stub.refreshed == 1

    def test_plus_ten_changes_the_height_by_exactly_ten(self):
        stub = _SeamStub(striped(height=300), frames=[1, 2])

        assert stub.correct(10) is True

        assert stub.stitched_result.size[1] == 310

    def test_minus_ten_changes_the_height_by_exactly_ten(self):
        stub = _SeamStub(striped(height=300), frames=[1, 2])

        assert stub.correct(-10) is True

        assert stub.stitched_result.size[1] == 290

    def test_correction_uses_the_preview_center(self):
        """预览把 300px 压进 100px 面板：面板中心对应结果的 150px 处。"""
        stub = _SeamStub(striped(height=300), frames=[1, 2])

        assert stub.center() == 150

    def test_upward_scroll_maps_the_preview_back_to_the_unflipped_image(self):
        stub = _SeamStub(striped(height=300), frames=[1, 2])
        stub.scroll_locked_direction = "up"

        assert stub.center() == 150

    def test_no_result_means_no_correction(self):
        stub = _SeamStub(None)
        assert stub.correct(5) is False
        assert stub.correct(0) is False


class TestSeamToolbar:
    """长截图工具栏本体：接缝修正菜单真的建得出来、也真的发得出信号。

    这一条是「构造真控件」而不是「调私有方法」——本轮就是它漏掉了：工具栏里
    ``self.tr("...{delta}...", delta=...)`` 会被 QObject.tr 拒掉（不收关键字参数），
    于是 FloatingToolbar 一构造就抛 AttributeError，长截图窗口跟着起不来，
    而当时的用例全在 _StubWindow 上跑，谁也没碰这个控件。
    """

    @pytest.fixture
    def toolbar(self, qapp):
        from stitch.scroll_toolbar import FloatingToolbar

        bar = FloatingToolbar()
        yield bar
        bar.close()

    def test_toolbar_constructs(self, toolbar):
        assert toolbar.seam_btn is not None

    def test_menu_offers_the_four_nudges(self, toolbar):
        labels = [action.text() for action in toolbar.seam_menu.actions()]

        assert len(labels) == 4
        for delta in ("-10", "-1", "+1", "+10"):
            assert any(delta in label for label in labels), (delta, labels)

    def test_each_entry_emits_its_own_delta(self, toolbar):
        emitted = []
        toolbar.seam_correct_requested.connect(emitted.append)

        for action in toolbar.seam_menu.actions():
            action.trigger()

        assert emitted == [-10, -1, 1, 10]

    def test_the_real_window_builds_its_toolbar(self, qapp, monkeypatch):
        """走 ScrollCaptureWindow 建工具栏那条路（不构造整个抓屏会话）。

        真窗口要起抓屏会话才能构造，所以拿一个真 QWidget 当父窗口、把要连的槽挂上去——
        被执行的仍是产品代码 ``_setup_floating_toolbar`` 本身（工具栏就是它 new 出来的）。
        """
        from PySide6.QtWidgets import QWidget

        from stitch.scroll_window import ScrollCaptureWindow

        holder = QWidget()
        holder._toggle_direction = lambda: None
        holder._on_manual_capture = lambda: None
        holder._on_pin = lambda: None
        holder._on_seam_correct = lambda _delta: None
        holder._on_finish = lambda: None
        holder._on_cancel = lambda: None
        holder._position_floating_toolbar = lambda: None

        ScrollCaptureWindow._setup_floating_toolbar(holder)

        assert type(holder.toolbar).__name__ == "FloatingToolbar"
        assert len(holder.toolbar.seam_menu.actions()) == 4
        holder.toolbar.close()
        holder.deleteLater()
