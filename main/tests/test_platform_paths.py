# -*- coding: utf-8 -*-
"""平台层路径（core/platform/paths）单元测试。

三个平台的分支在当前机器上都能测：``paths`` 模块把平台常量绑在模块命名空间里，
monkeypatch 它即可切分支，不需要真的跑在另外两个系统上。

重点不是「当前机器落点对不对」，而是**另外两个平台的落点不会再悄悄退回 Windows 路径**
——这正是迁移前的问题：macOS/Linux 上没有 %LOCALAPPDATA%，硬编码的回落值让日志与
离线模型都落进了伪造的 ~/AppData/Local。
"""

import os
from pathlib import Path

import pytest

from core.platform import paths


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setattr(paths, "IS_MACOS", False)
    return paths


@pytest.fixture
def as_macos(monkeypatch):
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_MACOS", True)
    return paths


@pytest.fixture
def as_linux(monkeypatch):
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_MACOS", False)
    return paths


class TestAppDataDir:
    def test_windows_reads_localappdata(self, as_windows, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert paths.app_data_dir() == tmp_path / "Jietuba"

    def test_windows_falls_back_to_home_appdata(self, as_windows, monkeypatch):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert paths.app_data_dir() == Path.home() / "AppData" / "Local" / "Jietuba"

    def test_macos_uses_application_support(self, as_macos):
        assert paths.app_data_dir() == (
            Path.home() / "Library" / "Application Support" / "Jietuba"
        )

    def test_linux_reads_xdg_data_home(self, as_linux, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert paths.app_data_dir() == tmp_path / "Jietuba"

    def test_linux_falls_back_to_local_share(self, as_linux, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert paths.app_data_dir() == Path.home() / ".local" / "share" / "Jietuba"

    @pytest.mark.parametrize("platform", ["macos", "linux"])
    def test_never_lands_in_a_fake_appdata_dir(self, platform, monkeypatch):
        """迁移前 macOS/Linux 都会落进 ~/AppData/Local——一个这两边根本不存在的目录。"""
        monkeypatch.setattr(paths, "IS_WINDOWS", False)
        monkeypatch.setattr(paths, "IS_MACOS", platform == "macos")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert "AppData" not in str(paths.app_data_dir())


class TestLogDir:
    def test_sits_under_app_data_dir(self, as_macos):
        assert paths.log_dir() == paths.app_data_dir() / "Logs"

    def test_is_a_path(self, as_linux):
        assert isinstance(paths.log_dir(), Path)


class TestDefaultScreenshotDir:
    @pytest.mark.parametrize("platform", ["windows", "macos"])
    def test_pictures_folder(self, platform, monkeypatch):
        monkeypatch.setattr(paths, "IS_WINDOWS", platform == "windows")
        monkeypatch.setattr(paths, "IS_MACOS", platform == "macos")
        assert paths.default_screenshot_dir() == os.path.join(
            os.path.expanduser("~"), "Pictures", "jietuba_photos"
        )

    def test_linux_prefers_xdg_pictures_dir(self, as_linux, monkeypatch, tmp_path):
        """发行版会把图片目录本地化（中文环境是 ~/图片），所以优先读 XDG。"""
        monkeypatch.setenv("XDG_PICTURES_DIR", str(tmp_path))
        assert paths.default_screenshot_dir() == str(tmp_path / "jietuba_photos")

    def test_linux_falls_back_to_pictures(self, as_linux, monkeypatch):
        monkeypatch.delenv("XDG_PICTURES_DIR", raising=False)
        assert paths.default_screenshot_dir().endswith(
            os.path.join("Pictures", "jietuba_photos")
        )
        assert paths.default_screenshot_dir().startswith(os.path.expanduser("~"))


class TestDesktopDir:
    def test_windows_and_macos_use_home_desktop(self, as_windows):
        assert paths.desktop_dir() == os.path.join(os.path.expanduser("~"), "Desktop")

    def test_linux_prefers_xdg_desktop_dir(self, as_linux, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DESKTOP_DIR", str(tmp_path))
        assert paths.desktop_dir() == str(tmp_path)


class TestSettingsDefaultsHaveOneSource:
    """配置默认值必须来自平台层，不能再各自抄一份路径。

    迁移前 ``settings/tool_settings.py`` 里有一条自己拼的 log_dir
    （``~/AppData/Local/Jietuba/Logs``），它作为配置默认值会**盖住**平台层的取值——
    两边一旦不同步，改一边完全不生效。
    """

    def test_log_dir_default_is_the_platform_log_dir(self):
        from settings.tool_settings import get_tool_settings_manager

        defaults = get_tool_settings_manager().APP_DEFAULT_SETTINGS
        assert defaults["log_dir"] == str(paths.log_dir())

    def test_screenshot_save_path_default_is_the_platform_default(self):
        from settings.tool_settings import get_tool_settings_manager

        defaults = get_tool_settings_manager().APP_DEFAULT_SETTINGS
        assert defaults["screenshot_save_path"] == paths.default_screenshot_dir()

    def test_log_dir_default_has_no_hardcoded_appdata(self):
        from settings.tool_settings import get_tool_settings_manager

        defaults = get_tool_settings_manager().APP_DEFAULT_SETTINGS
        if not paths.IS_WINDOWS:
            assert "AppData" not in defaults["log_dir"]
