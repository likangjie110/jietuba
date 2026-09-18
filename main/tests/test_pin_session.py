# -*- coding: utf-8 -*-
"""贴图会话（pin/pin_session.py）：退出时保存未关闭的贴图，启动时恢复。

这是「启动恢复未关闭的贴图」的全部逻辑：序列化（图像 + 位置大小 + 透明度 + 锁定）、
数量上限、以及恢复时把位置/比例写回去。测试用假贴图，不建真窗口——真窗口的可见性
另外用真机探针单独验过。

会话目录会被指到 tmp_path：默认落在应用数据目录里，测试必须隔离。
"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from pin import pin_session


@pytest.fixture(autouse=True)
def isolated_session_dir(tmp_path, monkeypatch):
    """把会话目录换到临时目录（源码是 app_data_dir() / 'pins'）。"""
    target = tmp_path / "pins"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pin_session, "session_dir", lambda: target)
    return target


class _FakePin:
    """只实现会话序列化/恢复会用到的那几样。"""

    def __init__(self, width=40, height=30, x=100, y=80, opacity=1.0, locked=False,
                 image_ok=True):
        self._rect = QRect(x, y, width, height)
        self._win_opacity = opacity
        self._locked = locked
        self._image_ok = image_ok
        self.opacity_calls = []

    def get_current_image(self):
        if not self._image_ok:
            return None
        image = QImage(40, 30, QImage.Format.Format_ARGB32)
        image.fill(0xFF224466)
        return image

    def geometry(self):
        return self._rect

    def is_locked(self):
        return self._locked

    def setGeometry(self, rect):
        self._rect = QRect(rect)

    def setWindowOpacity(self, value):
        self.opacity_calls.append(value)

    def toggle_lock(self):
        self._locked = not self._locked


class TestSaveSession:
    def test_saves_image_and_geometry(self, isolated_session_dir):
        pin = _FakePin(x=11, y=22, width=40, height=30, opacity=0.6)

        assert pin_session.save_session([pin], limit=10) == 1

        entries = pin_session.load_session(limit=10)
        assert entries == [{
            "image": "pin_0.png",
            "x": 11, "y": 22, "width": 40, "height": 30,
            "opacity": pytest.approx(0.6), "locked": False,
        }]
        assert (isolated_session_dir / "pin_0.png").exists()

    def test_limit_keeps_the_most_recent_pins(self, isolated_session_dir):
        pins = [_FakePin(x=index) for index in range(5)]

        assert pin_session.save_session(pins, limit=2) == 2

        entries = pin_session.load_session(limit=10)
        assert [entry["x"] for entry in entries] == [3, 4]

    def test_zero_limit_saves_nothing(self, isolated_session_dir):
        assert pin_session.save_session([_FakePin()], limit=0) == 0
        assert pin_session.load_session(limit=10) == []

    def test_saving_replaces_the_previous_session(self, isolated_session_dir):
        pin_session.save_session([_FakePin(x=1), _FakePin(x=2)], limit=10)

        pin_session.save_session([_FakePin(x=9)], limit=10)

        entries = pin_session.load_session(limit=10)
        assert len(entries) == 1
        assert entries[0]["x"] == 9
        # 旧图片也要清掉，不然数据目录会一直长
        assert not (isolated_session_dir / "pin_1.png").exists()

    def test_a_pin_that_cannot_render_is_skipped(self, isolated_session_dir):
        assert pin_session.save_session([_FakePin(image_ok=False)], limit=10) == 0
        assert pin_session.load_session(limit=10) == []

    def test_oversized_pins_are_skipped(self, isolated_session_dir, monkeypatch):
        monkeypatch.setattr(pin_session, "MAX_PIN_BYTES", 1)

        assert pin_session.save_session([_FakePin()], limit=10) == 0
        assert pin_session.load_session(limit=10) == []

    def test_locked_state_is_stored(self, isolated_session_dir):
        pin_session.save_session([_FakePin(locked=True)], limit=10)

        assert pin_session.load_session(limit=10)[0]["locked"] is True


class TestLoadSession:
    def test_missing_file_is_an_empty_session(self):
        assert pin_session.load_session(limit=10) == []

    def test_broken_json_is_an_empty_session(self, isolated_session_dir):
        (isolated_session_dir / pin_session.SESSION_FILE_NAME).write_text(
            "{不是 json", encoding="utf-8")

        assert pin_session.load_session(limit=10) == []

    def test_non_list_payload_is_ignored(self, isolated_session_dir):
        (isolated_session_dir / pin_session.SESSION_FILE_NAME).write_text(
            '{"pins": 3}', encoding="utf-8")

        assert pin_session.load_session(limit=10) == []

    def test_entries_are_capped_by_the_limit(self, isolated_session_dir):
        pin_session.save_session([_FakePin(x=i) for i in range(5)], limit=10)

        assert len(pin_session.load_session(limit=3)) == 3

    def test_zero_limit_reads_nothing(self, isolated_session_dir):
        pin_session.save_session([_FakePin()], limit=10)

        assert pin_session.load_session(limit=0) == []


class TestClearSession:
    def test_clear_removes_images_and_the_file(self, isolated_session_dir):
        pin_session.save_session([_FakePin(), _FakePin()], limit=10)

        pin_session.clear_session()

        assert pin_session.load_session(limit=10) == []
        assert list(isolated_session_dir.glob("pin_*.png")) == []


class _FakeManager:
    def __init__(self):
        self.created = []

    def create_pin(self, *, image, position, config_manager):
        pin = _FakePin(x=position.x(), y=position.y())
        self.created.append((pin, image, config_manager))
        return pin


class TestRestorePins:
    def test_restores_position_size_opacity_and_lock(self, isolated_session_dir):
        pin_session.save_session(
            [_FakePin(x=11, y=22, width=40, height=30, opacity=0.6, locked=True)], limit=10)
        manager = _FakeManager()

        assert pin_session.restore_pins("cfg", limit=10, manager=manager) == 1

        pin, image, config = manager.created[0]
        assert config == "cfg"
        assert image.width() == 40
        assert (pin.geometry().x(), pin.geometry().y()) == (11, 22)
        assert (pin.geometry().width(), pin.geometry().height()) == (40, 30)
        assert pin.opacity_calls == [pytest.approx(0.6)]
        assert pin.is_locked() is True

    def test_scale_factor_follows_the_stored_size(self, isolated_session_dir):
        """恢复出来的比例要跟着会话里的大小走，否则后续滚轮缩放的基准是错的。"""
        pin_session.save_session([_FakePin(width=80, height=60)], limit=10)
        manager = _FakeManager()

        pin_session.restore_pins("cfg", limit=10, manager=manager)

        pin = manager.created[0][0]
        assert pin.scale_factor == pytest.approx(2.0)   # 80 / 40（图像宽 40）

    def test_missing_image_is_skipped_without_raising(self, isolated_session_dir, caplog):
        pin_session.save_session([_FakePin()], limit=10)
        (isolated_session_dir / "pin_0.png").unlink()
        manager = _FakeManager()

        assert pin_session.restore_pins("cfg", limit=10, manager=manager) == 0
        assert manager.created == []

    def test_manager_failure_does_not_stop_the_rest(self, isolated_session_dir):
        pin_session.save_session([_FakePin(), _FakePin()], limit=10)

        class _Picky(_FakeManager):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def create_pin(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("第一张建不出来")
                return super().create_pin(**kwargs)

        manager = _Picky()

        assert pin_session.restore_pins("cfg", limit=10, manager=manager) == 1

    def test_empty_session_restores_nothing(self, isolated_session_dir):
        manager = _FakeManager()

        assert pin_session.restore_pins("cfg", limit=10, manager=manager) == 0
        assert manager.created == []


class TestMainAppHooks:
    """MainApp 上的两个开关：启动按设置恢复、退出按设置保存。"""

    def _app(self, config, pins=()):
        from main_app import MainApp

        manager = SimpleNamespace(get_all_pins=lambda: list(pins))
        return MainApp, SimpleNamespace(config_manager=config, _pin_manager=manager)

    def test_restore_is_skipped_when_the_setting_is_off(self, monkeypatch):
        from main_app import MainApp

        monkeypatch.setattr(pin_session, "restore_pins",
                            lambda *_a, **_k: pytest.fail("关掉设置时不该读会话"))
        app = SimpleNamespace(config_manager=SimpleNamespace(
            get_pin_restore_on_startup=lambda: False))

        assert MainApp.restore_pins_if_enabled(app) == 0

    def test_restore_uses_the_configured_limit(self, monkeypatch):
        from main_app import MainApp

        seen = {}

        def _restore(config, limit):
            seen["config"] = config
            seen["limit"] = limit
            return 2

        monkeypatch.setattr(pin_session, "restore_pins", _restore)
        config = SimpleNamespace(
            get_pin_restore_on_startup=lambda: True,
            get_pin_history_limit=lambda: 7,
        )
        app = SimpleNamespace(config_manager=config)

        assert MainApp.restore_pins_if_enabled(app) == 2
        assert seen == {"config": config, "limit": 7}

    def test_saving_disabled_clears_the_old_session(self, monkeypatch):
        from main_app import MainApp

        cleared = []
        monkeypatch.setattr(pin_session, "clear_session", lambda: cleared.append(True))
        monkeypatch.setattr(pin_session, "save_session",
                            lambda *_a, **_k: pytest.fail("关掉设置时不该保存"))
        app = SimpleNamespace(
            _pin_session_saved=False,
            config_manager=SimpleNamespace(
                get_pin_restore_on_startup=lambda: False,
                get_pin_history_limit=lambda: 10,
            ),
        )

        assert MainApp.save_pin_session_if_enabled(app) == 0
        assert cleared == [True]

    def test_saving_enabled_writes_the_open_pins(self, monkeypatch):
        from main_app import MainApp

        from pin.pin_manager import PinManager

        seen = {}

        def _save(pins, limit):
            seen["pins"] = list(pins)
            seen["limit"] = limit
            return len(seen["pins"])

        monkeypatch.setattr(pin_session, "save_session", _save)
        monkeypatch.setattr(PinManager, "instance",
                            classmethod(lambda cls: SimpleNamespace(get_all_pins=lambda: ["p1", "p2"])))
        app = SimpleNamespace(
            _pin_session_saved=False,
            config_manager=SimpleNamespace(
                get_pin_restore_on_startup=lambda: True,
                get_pin_history_limit=lambda: 3,
            ),
        )

        assert MainApp.save_pin_session_if_enabled(app) == 2
        assert seen == {"pins": ["p1", "p2"], "limit": 3}

    def test_a_second_save_in_the_same_run_is_skipped(self, monkeypatch):
        """退出流程里贴图会被关掉：晚一步的第二次保存不能把会话覆盖成空的。"""
        from main_app import MainApp

        calls = []
        monkeypatch.setattr(pin_session, "save_session",
                            lambda pins, limit: calls.append(list(pins)) or len(calls))
        app = SimpleNamespace(
            _pin_session_saved=False,
            config_manager=SimpleNamespace(
                get_pin_restore_on_startup=lambda: True,
                get_pin_history_limit=lambda: 10,
            ),
        )
        manager = SimpleNamespace(get_all_pins=lambda: ["p1"])
        monkeypatch.setattr("pin.pin_manager.PinManager.instance",
                            classmethod(lambda cls: manager))

        assert MainApp.save_pin_session_if_enabled(app) == 1
        manager.get_all_pins = lambda: []          # 贴图已经被关掉了
        assert MainApp.save_pin_session_if_enabled(app) == 0
        assert calls == [["p1"]]

    def test_quit_saves_before_other_cleanup(self, monkeypatch):
        """会话要在贴图被销毁之前存：cleanup() 之后就什么都存不到了。"""
        from main_app import MainApp

        order = []
        monkeypatch.setattr("main_app.log_exception", lambda *_a, **_k: None)
        monkeypatch.setattr("translation.TranslationManager.cleanup",
                            classmethod(lambda cls: order.append("translation")))
        # 假 self 是 SimpleNamespace：方法得挂在实例上（类上的补丁对它不生效）
        app = SimpleNamespace(
            _logger=None,
            save_pin_session_if_enabled=lambda: order.append("save"),
        )

        MainApp._on_about_to_quit(app)

        assert order == ["save", "translation"]
