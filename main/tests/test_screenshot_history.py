# -*- coding: utf-8 -*-
"""截图历史：保留策略决策、真实存储、来源判定与入史入口。

这里全部驱动真实对象：真 `HistoryStore`（真写文件、真读回）、真 `record_screenshot`
（真配置对象）。只有「这一点上是哪个窗口」这一层在用例里注入替身——那是平台 API，
生产走 `core/platform/window`，理由与 test_platform_* 里给替身打桩一致。
"""

import json
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect, QSettings
from PySide6.QtGui import QColor, QImage

from history import HistoryEntry, HistoryStore, plan_eviction, record_screenshot, reset_store
from history import source as source_module
from settings.tool_settings import ToolSettingsManager


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "history")


@pytest.fixture
def config(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "history.ini"), QSettings.Format.IniFormat))


@pytest.fixture(autouse=True)
def _isolated_shared_store(tmp_path):
    """入史入口用的是进程共享存储：每条用例换成自己的目录。

    teardown **不能**置空共享存储——那会把 conftest 的会话级隔离一起撤销，之后任何
    入史用例就写进开发机真实的历史目录（真的发生过）。交给 conftest 的用例级守卫在
    下一个用例开始前重新指向会话临时目录。
    """
    reset_store(HistoryStore(tmp_path / "shared"))
    yield


def image(width=40, height=30, color="#3366CC") -> QImage:
    picture = QImage(width, height, QImage.Format.Format_RGB32)
    picture.fill(QColor(color))
    return picture


def entries_of(store, count, *, size=40, source="region", start=None, step=60):
    """按 step 秒递增写入 count 条；返回写进去的条目（最旧的在前）。"""
    base = time.time() - count * step if start is None else start
    written = []
    for index in range(count):
        entry = store.add(image(size, size), source=source, created_at=base + index * step)
        assert entry is not None
        written.append(entry)
    return written


# ── 保留策略（纯决策） ────────────────────────────────

class TestPlanEviction:
    def _entries(self, count, *, size_bytes=100, step=60, now=None):
        now = now if now is not None else time.time()
        return [
            HistoryEntry(id=f"e{index}", created_at=now - (count - index) * step,
                         path=f"/tmp/e{index}.png", width=1, height=1, size_bytes=size_bytes)
            for index in range(count)
        ]

    def test_no_limits_evicts_nothing(self):
        doomed, _reasons = plan_eviction(self._entries(5), now=time.time())
        assert doomed == ()

    def test_retention_days_drops_only_expired(self):
        now = time.time()
        entries = self._entries(4, step=86400)          # 四条分别距今 4/3/2/1 天
        doomed, reasons = plan_eviction(entries, now=now, retention_days=2)
        assert set(doomed) == {"e0", "e1"}
        assert all(reasons[i] == "expired" for i in doomed)

    def test_max_entries_keeps_the_newest(self):
        now = time.time()
        entries = self._entries(5, step=60)
        doomed, reasons = plan_eviction(entries, now=now, max_entries=2)
        assert set(doomed) == {"e0", "e1", "e2"}
        assert all(reasons[i] == "over_count" for i in doomed)

    def test_disk_cap_drops_oldest_until_under_the_cap(self):
        now = time.time()
        entries = self._entries(5, size_bytes=1000, step=60)
        doomed, reasons = plan_eviction(entries, now=now, max_disk_bytes=2500)
        assert set(doomed) == {"e0", "e1", "e2"}         # 剩 2 条 = 2000 字节
        assert all(reasons[i] == "over_disk" for i in doomed)

    def test_first_triggering_reason_wins(self):
        now = time.time()
        entries = self._entries(4, step=86400, size_bytes=1000)
        doomed, reasons = plan_eviction(entries, now=now, retention_days=2, max_entries=3)
        assert reasons[doomed[0]] == "expired"


# ── 真实存储 ──────────────────────────────────────────

class TestStore:
    def test_add_writes_the_file_and_the_metadata(self, store):
        entry = store.add(image(64, 48), source="window", label="编辑器")
        assert isinstance(entry, HistoryEntry)
        assert entry.source == "window" and entry.label == "编辑器"
        assert (entry.width, entry.height) == (64, 48)
        assert entry.size_bytes > 0 and entry.exists()
        assert store.entries() == [entry]

    def test_add_rejects_an_empty_image(self, store):
        assert store.add(QImage()) is None
        assert store.entries() == []

    def test_entries_are_newest_first(self, store):
        written = entries_of(store, 3)
        assert [entry.id for entry in store.entries()] == [written[2].id, written[1].id,
                                                          written[0].id]

    def test_the_index_survives_a_restart(self, store, tmp_path):
        entry = store.add(image(), source="monitor", label="Built-in Retina Display")

        reopened = HistoryStore(tmp_path / "history")
        loaded = reopened.entries()
        assert len(loaded) == 1
        assert loaded[0].id == entry.id and loaded[0].label == entry.label
        assert reopened.image(entry.id) is not None

    def test_a_corrupt_index_is_treated_as_an_empty_history(self, store, tmp_path):
        root = tmp_path / "history"
        root.mkdir(parents=True, exist_ok=True)
        (root / "index.json").write_text("{ this is not json", encoding="utf-8")

        assert HistoryStore(root).entries() == []

    def test_bad_rows_are_dropped_without_losing_the_good_ones(self, store, tmp_path):
        entry = store.add(image())
        index = tmp_path / "history" / "index.json"
        rows = json.loads(index.read_text(encoding="utf-8"))
        rows.append({"id": "broken", "created_at": "不是时间"})
        rows.append("也不是字典")
        index.write_text(json.dumps(rows), encoding="utf-8")

        reopened = HistoryStore(tmp_path / "history")
        assert [item.id for item in reopened.entries()] == [entry.id]

    def test_image_round_trips_the_pixels(self, store):
        entry = store.add(image(20, 10, "#FF0000"))
        loaded = store.image(entry.id)
        assert loaded is not None
        assert loaded.size().width() == 20 and loaded.pixelColor(5, 5).red() > 200

    def test_remove_deletes_the_file(self, store):
        entry = store.add(image())
        assert store.remove(entry.id) is True
        assert store.entries() == []
        assert not entry.exists()

    def test_total_bytes_adds_up(self, store):
        entries = entries_of(store, 3)
        assert store.total_bytes() == sum(entry.size_bytes for entry in entries)


class TestFiltering:
    def test_filter_by_source(self, store):
        entries_of(store, 2, source="window")
        entries_of(store, 3, source="monitor")
        assert len(store.filtered(source="window")) == 2
        assert len(store.filtered(source="monitor")) == 3
        assert len(store.filtered(source="region")) == 0

    def test_filter_by_date_range(self, store):
        now = time.time()
        entries_of(store, 3, start=now - 3 * 86400, step=86400)   # 三条：距今 3/2/1 天
        recent = store.filtered(start=now - 1.5 * 86400)
        older = store.filtered(end=now - 1.5 * 86400)
        assert len(recent) == 1 and len(older) == 2

    def test_filters_combine(self, store):
        now = time.time()
        entries_of(store, 2, source="window", start=now - 2 * 86400, step=86400)
        entries_of(store, 2, source="region", start=now - 2 * 86400, step=86400)
        assert len(store.filtered(source="window", start=now - 1.5 * 86400)) == 1


class TestRetentionOnDisk:
    def test_max_entries_evicts_and_shrinks_the_disk_usage(self, store):
        entries_of(store, 5)
        before = store.total_bytes()

        removed = store.apply_retention(max_entries=2)

        assert removed == 3
        assert len(store.entries()) == 2
        assert store.total_bytes() < before
        for entry in store.entries():
            assert entry.exists(), "留下来的条目必须还能打开"
            assert store.image(entry.id) is not None

    def test_disk_cap_evicts_oldest_until_under(self, store):
        entries_of(store, 6)
        per_entry = store.entries()[0].size_bytes
        cap = per_entry * 2 + per_entry // 2

        store.apply_retention(max_disk_bytes=cap)

        assert store.total_bytes() <= cap
        assert 0 < len(store.entries()) < 6

    def test_retention_days_evicts_expired_only(self, store):
        now = time.time()
        entries_of(store, 2, start=now - 10 * 86400, step=86400)
        entries_of(store, 2, start=now - 3600, step=1)

        removed = store.apply_retention(retention_days=7, now=now)

        assert removed == 2
        assert len(store.entries()) == 2

    def test_clear_removes_everything(self, store):
        entries_of(store, 3)
        assert store.clear() == 3
        assert store.entries() == []
        assert list(store.root.glob("*.png")) == []


# ── 来源判定 ──────────────────────────────────────────

class TestSourceClassification:
    def test_element_wins_over_window(self):
        selection = QRect(100, 100, 200, 100)
        detected = (100, 100, 300, 200)
        assert source_module.classify(selection, detected=detected,
                                      detected_is_element=True) == source_module.SOURCE_ELEMENT

    def test_window_when_the_detection_is_window_level(self):
        selection = QRect(100, 100, 200, 100)
        assert source_module.classify(selection, detected=(100, 100, 300, 200),
                                      detected_is_element=False) == source_module.SOURCE_WINDOW

    def test_monitor_when_the_selection_covers_a_screen(self):
        screen = QRect(0, 0, 1440, 900)
        assert source_module.classify(QRect(0, 0, 1440, 900),
                                      screens=[screen]) == source_module.SOURCE_MONITOR

    def test_region_when_nothing_matches(self):
        assert source_module.classify(QRect(10, 10, 50, 50),
                                      detected=(500, 500, 600, 600),
                                      screens=[QRect(0, 0, 1440, 900)]) == source_module.SOURCE_REGION

    def test_tolerance_absorbs_the_one_pixel_selection_inset(self):
        selection = QRect(101, 101, 198, 98)
        assert source_module.classify(selection, detected=(100, 100, 300, 200),
                                      detected_is_element=True) == source_module.SOURCE_ELEMENT

    def test_rects_close_handles_lists_and_qrects(self):
        assert source_module.rects_close(QRect(0, 0, 10, 10), [0, 0, 10, 10])
        assert not source_module.rects_close(QRect(0, 0, 10, 10), None)


class TestDescribe:
    def test_uses_the_injected_probe_for_the_label(self):
        found = source_module.describe(
            QRect(100, 100, 200, 100),
            probe=lambda _x, _y: (source_module.SOURCE_WINDOW, [100, 100, 300, 200], "编辑器"),
            screens=[],
        )
        assert found == (source_module.SOURCE_WINDOW, "编辑器")

    def test_a_monitor_selection_gets_no_label(self):
        found = source_module.describe(QRect(0, 0, 1440, 900), probe=lambda _x, _y: (None, None, ""),
                                       screens=[QRect(0, 0, 1440, 900)])
        assert found == (source_module.SOURCE_MONITOR, "")

    def test_a_probe_failure_falls_back_to_region(self):
        def boom(_x, _y):
            raise RuntimeError("平台层炸了")

        assert source_module.describe(QRect(1, 2, 3, 4), probe=boom, screens=[]) == (
            source_module.SOURCE_REGION, "")


# ── 入史入口 ──────────────────────────────────────────

class TestRecordScreenshot:
    def test_records_with_source_and_time_when_enabled(self, config, tmp_path):
        found = record_screenshot(
            image(120, 90), QRect(100, 100, 200, 100), config,
            screens=[],
            probe=lambda _x, _y: (source_module.SOURCE_WINDOW, [100, 100, 300, 200], "编辑器"),
        )
        assert found is not None
        assert found.source == "window" and found.label == "编辑器"
        assert abs(found.created_at - time.time()) < 5
        assert (found.width, found.height) == (120, 90)
        assert found.path.endswith(".png")

    def test_skips_everything_when_the_feature_is_off(self, config):
        config.set_history_enabled(False)
        assert record_screenshot(image(), QRect(0, 0, 10, 10), config, screens=[]) is None
        from history import get_store

        assert get_store().entries() == []

    def test_retention_runs_right_after_recording(self, config):
        config.set_history_max_entries(2)
        for _index in range(4):
            record_screenshot(image(20, 20), QRect(0, 0, 10, 10), config, screens=[], probe=None)
        from history import get_store

        assert len(get_store().entries()) == 2

    def test_an_empty_image_is_not_recorded(self, config):
        assert record_screenshot(QImage(), None, config, screens=[]) is None

    def test_a_probe_that_explodes_does_not_break_the_capture(self, config):
        def boom(_x, _y):
            raise RuntimeError("平台层炸了")

        found = record_screenshot(image(), QRect(0, 0, 10, 10), config, screens=[], probe=boom)
        assert found is not None and found.source == "region"


class TestSilentCapturePath:
    """静默截图（托盘/热键/全局鼠标动作）也要入史——那条路径不走截图编辑器。"""

    def test_run_action_records_the_silent_capture(self, qapp, config, monkeypatch, tmp_path):
        from PySide6.QtCore import QRectF

        import core.actions as actions_module
        from history import get_store

        monkeypatch.setattr(config, "get_ui_detection", lambda: "none")
        monkeypatch.setattr(actions_module, "capture_at_cursor",
                            lambda _app: (image(80, 60), QRectF(100, 200, 400, 300)))
        monkeypatch.setattr(actions_module, "_copy", lambda _image: True)
        monkeypatch.setattr(actions_module, "_flash_capture_mask", lambda *a, **k: None)

        app = SimpleNamespace(config_manager=config)
        assert actions_module.run_action("screenshot_copy", app) is True

        entries = get_store().entries()
        assert len(entries) == 1
        entry = entries[0]
        assert (entry.width, entry.height) == (80, 60)
        assert entry.source in ("window", "element", "monitor", "region")
        assert entry.exists()

    def test_the_capture_still_works_when_history_is_off(self, qapp, config, monkeypatch):
        from PySide6.QtCore import QRectF

        import core.actions as actions_module
        from history import get_store

        config.set_history_enabled(False)
        monkeypatch.setattr(actions_module, "capture_at_cursor",
                            lambda _app: (image(40, 30), QRectF(0, 0, 40, 30)))
        monkeypatch.setattr(actions_module, "_copy", lambda _image: True)
        monkeypatch.setattr(actions_module, "_flash_capture_mask", lambda *a, **k: None)

        assert actions_module.run_action("screenshot_copy",
                                         SimpleNamespace(config_manager=config)) is True
        assert get_store().entries() == []

    def test_a_history_failure_does_not_break_the_capture(self, qapp, config, monkeypatch):
        from PySide6.QtCore import QRectF

        import core.actions as actions_module
        from history import get_store

        def boom(*_args, **_kwargs):
            raise RuntimeError("历史炸了")

        monkeypatch.setattr(actions_module, "capture_at_cursor",
                            lambda _app: (image(40, 30), QRectF(0, 0, 40, 30)))
        monkeypatch.setattr(actions_module, "_copy", lambda _image: True)
        monkeypatch.setattr(actions_module, "_flash_capture_mask", lambda *a, **k: None)
        monkeypatch.setattr("history.record_screenshot", boom)

        assert actions_module.run_action("screenshot_copy",
                                         SimpleNamespace(config_manager=config)) is True
        assert get_store().entries() == []


class TestStoreIsolation:
    """入史入口写的是进程共享存储：测试里它必须落在临时目录，绝不能是真机目录。

    这条是护栏，不是实现细节：曾经有一次某个文件的 autouse 夹具在 teardown 里把它置空，
    同一次会话里后面的入史用例就写进了开发机真实历史目录（跑一次测试多出几十张测试图）。
    """

    def test_the_shared_store_never_points_at_the_real_history_dir(self):
        from core.platform.paths import app_data_dir
        from history import get_store

        real = app_data_dir() / "history"
        assert get_store().root != real, f"共享历史存储指向了真机目录: {real}"

    def test_recording_goes_to_the_test_store(self, qapp, config, tmp_path):
        from history import get_store, record_screenshot

        entry = record_screenshot(image(24, 16), QRect(0, 0, 24, 16), config, screens=[],
                                  probe=lambda _x, _y: (None, None, ""))
        assert entry is not None
        assert entry.path.startswith(str(get_store().root))
