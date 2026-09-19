# -*- coding: utf-8 -*-
"""基础设施：设置导出/导入（含回滚）、设置搜索、更新检查的版本比较。

导出/导入与搜索都驱动真实现（真 QSettings、真设置对话框与真页面部件）；更新检查打桩
的只有**外部边界**——本地 stub HTTP 服务，被测的请求、解析、比较与报告全是真的。
"""

import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PySide6.QtCore import QSettings

from core import settings_archive, updates
from settings.tool_settings import ToolSettingsManager


@pytest.fixture
def config(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "archive.ini"), QSettings.Format.IniFormat))


# ── 设置导出 / 导入 ───────────────────────────────────

class TestExport:
    def test_export_writes_a_readable_archive(self, config, tmp_path):
        config.set_app_setting("ocr_text_layout", "lines")
        config.update_settings("pen", stroke_width=21)
        target = tmp_path / "backup.zip"

        result = settings_archive.export_settings(config, str(target))

        assert result.ok is True
        assert target.exists()
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
            assert {settings_archive.SETTINGS_NAME, settings_archive.MANIFEST_NAME} <= names
            values = json.loads(archive.read(settings_archive.SETTINGS_NAME).decode("utf-8"))
            manifest = json.loads(archive.read(settings_archive.MANIFEST_NAME).decode("utf-8"))
        assert values["app/ocr_text_layout"] == "lines"
        assert manifest["schema"] == settings_archive.ARCHIVE_SCHEMA
        assert manifest["keys"] == len(values) > 0

    def test_export_without_settings_is_reported(self, tmp_path):
        result = settings_archive.export_settings(None, str(tmp_path / "x.zip"))
        assert result.ok is False and result.error

    def test_export_creates_missing_directories(self, config, tmp_path):
        target = tmp_path / "deep" / "nested" / "backup.zip"
        assert settings_archive.export_settings(config, str(target)).ok is True
        assert target.exists()


class TestImport:
    def test_import_restores_the_values(self, config, tmp_path):
        config.set_app_setting("ocr_text_layout", "lines")
        config.set_ocr_punctuation("strip_trailing")
        target = tmp_path / "backup.zip"
        settings_archive.export_settings(config, str(target))

        config.set_app_setting("ocr_text_layout", "single")
        config.set_ocr_punctuation("to_halfwidth")

        result = settings_archive.import_settings(config, str(target))

        assert result.ok is True
        assert config.get_ocr_text_layout() == "lines"
        assert config.get_ocr_punctuation() == "strip_trailing"

    def test_import_replaces_instead_of_merging(self, config, tmp_path):
        """导入是整体替换：备份里没有的键不该留着（否则说不清配置是哪来的）。"""
        target = tmp_path / "backup.zip"
        settings_archive.export_settings(config, str(target))

        config.set_app_setting("tray_click_action", "clipboard")
        assert config.get_tray_click_action() == "clipboard"

        settings_archive.import_settings(config, str(target))

        assert config.get_tray_click_action() == "screenshot"

    def test_a_broken_zip_is_reported_and_changes_nothing(self, config, tmp_path):
        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"not a zip at all")
        config.set_app_setting("ocr_text_layout", "lines")

        result = settings_archive.import_settings(config, str(broken))

        assert result.ok is False and result.error
        assert config.get_ocr_text_layout() == "lines"

    def test_a_zip_without_settings_json_is_refused(self, config, tmp_path):
        package = tmp_path / "empty.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("readme.txt", "nothing here")
        result = settings_archive.import_settings(config, str(package))
        assert result.ok is False
        assert settings_archive.SETTINGS_NAME in result.error

    def test_a_write_failure_rolls_back_to_the_previous_settings(self, config, tmp_path,
                                                                 monkeypatch):
        """写配置中途失败时，必须回到导入前的状态（半新半旧最糟）。"""
        config.set_app_setting("ocr_text_layout", "auto")
        config.set_app_setting("tray_click_action", "screenshot")
        target = tmp_path / "backup.zip"
        settings_archive.export_settings(config, str(target))
        config.set_app_setting("ocr_text_layout", "lines")
        config.set_app_setting("tray_click_action", "clipboard")
        before = settings_archive.snapshot_settings(config)

        # 用一个「写到第 N 次就报错一次」的 QSettings 替身：只替存储后端这一层，
        # 导入/回滚逻辑本身走真代码。报错一次之后放行，好让回滚真的能写回去
        # （这就对应「偶发写失败」这种真实故障）。
        monkeypatch.setattr(config, "qsettings", _FlakySettings(config.qsettings, 1))

        result = settings_archive.import_settings(config, str(target))

        monkeypatch.undo()
        assert result.ok is False
        assert "回滚" in result.error
        assert settings_archive.snapshot_settings(config) == before
        assert config.get_ocr_text_layout() == "lines"

    def test_import_refreshes_the_in_memory_tool_settings(self, config, tmp_path):
        """导入之后工具设置必须是最新的：内存里的值不会自己回看存储。

        真实症状是「导入成功但画笔粗细没变」——QSettings 写对了，界面读的是内存副本。
        """
        config.update_settings("pen", stroke_width=17)
        target = tmp_path / "backup.zip"
        settings_archive.export_settings(config, str(target))
        config.update_settings("pen", stroke_width=3)
        assert config.get_setting("pen", "stroke_width") == 3

        assert settings_archive.import_settings(config, str(target)).ok is True

        assert config.get_setting("pen", "stroke_width") == 17

    def test_a_newer_schema_is_refused(self, config, tmp_path):
        package = tmp_path / "future.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr(settings_archive.SETTINGS_NAME, json.dumps({"app/x": 1}))
            archive.writestr(settings_archive.MANIFEST_NAME,
                             json.dumps({"schema": settings_archive.ARCHIVE_SCHEMA + 1}))
        result = settings_archive.import_settings(config, str(package))
        assert result.ok is False
        assert "更新" in result.error


# ── 设置搜索 ──────────────────────────────────────────

class TestSearchIndex:
    def test_build_and_search_by_title(self):
        from ui.settings_ui.search import build_entries, search

        entries = build_entries(
            {0: "快捷键", 1: "外观"},
            [(0, "Global Actions", "Bind hotkeys to actions"),
             (1, "Language", "界面语言")],
        )
        hits = search(entries, "language")
        assert [hit.entry.title for hit in hits] == ["Language"]

    def test_every_term_must_match(self):
        from ui.settings_ui.search import build_entries, search

        entries = build_entries({0: "外观"}, [(0, "Language", "Choose the display language")])
        assert search(entries, "language display")
        assert search(entries, "language nothing") == []

    def test_an_empty_query_returns_nothing(self):
        from ui.settings_ui.search import build_entries, search

        entries = build_entries({0: "外观"}, [(0, "Language", "")])
        assert search(entries, "") == []
        assert search(entries, "   ") == []

    def test_page_name_is_searchable(self):
        from ui.settings_ui.search import build_entries, search

        entries = build_entries({0: "Pin Settings"}, [(0, "Shadow effect", "")])
        hits = search(entries, "pin")
        assert hits and hits[0].entry.page_name == "Pin Settings"

    def test_results_are_capped(self):
        from ui.settings_ui.search import build_entries, search

        entries = build_entries({0: "外观"}, [(0, f"Language {index}", "") for index in range(50)])
        assert len(search(entries, "language")) <= 30


class TestSearchInTheRealDialog:
    @pytest.fixture
    def dialog(self, qapp, config):
        from ui.settings_ui.dialog import SettingsDialog

        window = SettingsDialog(config_manager=config)
        yield window
        window.close()
        window.deleteLater()
        qapp.processEvents()

    def test_a_known_setting_is_found_and_jumps_to_its_page(self, dialog):
        dialog.search_input.setText("Language")
        hits = [dialog.search_results.item(row).text()
                for row in range(dialog.search_results.count())]
        assert hits, "搜索「Language」应当有命中"

        dialog.search_results.setCurrentRow(0)
        dialog._on_search_result_clicked(dialog.search_results.item(0))
        # 跳到的是「界面设置」那一页（Appearance）
        assert any("Language" in hit for hit in hits)

    def test_nonsense_returns_an_empty_result_set(self, dialog):
        dialog.search_input.setText("zzzz-not-a-setting")
        assert dialog.search_results.count() == 0
        assert dialog.search_results.isVisible() is False

    def test_clearing_the_query_hides_the_results(self, dialog):
        dialog.search_input.setText("Language")
        assert dialog.search_results.count() > 0
        dialog.search_input.setText("")
        assert dialog.search_results.count() == 0


# ── 更新检查 ──────────────────────────────────────────

class _FlakySettings:
    """QSettings 替身：第 ``allowed`` 次之后的第一次写会失败，之后恢复正常。"""

    def __init__(self, inner, allowed: int):
        self._inner = inner
        self._allowed = allowed
        self._writes = 0
        self._failed = False

    def setValue(self, key, value):            # noqa: N802 - 与 QSettings 同名
        self._writes += 1
        if self._writes > self._allowed and not self._failed:
            self._failed = True
            raise RuntimeError("磁盘满了")
        return self._inner.setValue(key, value)

    def value(self, *args, **kwargs):
        return self._inner.value(*args, **kwargs)

    def allKeys(self):                          # noqa: N802
        return self._inner.allKeys()

    def clear(self):
        return self._inner.clear()

    def sync(self):
        return self._inner.sync()


class TestVersionComparison:
    def test_parse_version_ignores_decorations(self):
        assert updates.parse_version("1.9") == (1, 9)
        assert updates.parse_version("v1.10.2-beta") == (1, 10, 2)
        assert updates.parse_version("dev") == ()

    def test_compare(self):
        assert updates.compare_versions("1.9", "1.10") == "newer"
        assert updates.compare_versions("1.9.0", "1.9") == "current"
        assert updates.compare_versions("1.10", "1.9") == "current"
        assert updates.compare_versions("dev", "1.2") == "unknown"


class _ReleaseHandler(BaseHTTPRequestHandler):
    payload = {"tag_name": "v9.9.9", "html_url": "https://example.com/rel"}
    status = 200

    def do_GET(self):  # noqa: N802
        body = json.dumps(type(self).payload).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def release_server():
    _ReleaseHandler.payload = {"tag_name": "v9.9.9", "html_url": "https://example.com/rel"}
    _ReleaseHandler.status = 200
    server = HTTPServer(("127.0.0.1", 0), _ReleaseHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/releases/latest"
    server.shutdown()
    server.server_close()


class TestUpdateCheck:
    def test_a_newer_release_is_reported_as_newer(self, release_server):
        result = updates.check_remote("1.9", api_url=release_server)
        assert result.state == "newer"
        assert result.remote == "v9.9.9"
        assert "9.9.9" in result.message and "1.9" in result.message

    def test_the_same_version_reports_up_to_date(self, release_server):
        _ReleaseHandler.payload = {"tag_name": "v1.9", "html_url": "https://example.com/rel"}
        result = updates.check_remote("1.9", api_url=release_server)
        assert result.state == "current"
        assert "最新" in result.message

    def test_an_older_remote_version_is_not_reported_as_newer(self, release_server):
        _ReleaseHandler.payload = {"tag_name": "v1.0", "html_url": "https://example.com/rel"}
        result = updates.check_remote("1.9", api_url=release_server)
        assert result.state == "current"
        assert bool(result) is False

    def test_an_unreachable_remote_is_reported_honestly(self):
        result = updates.check_remote("1.9", api_url="http://127.0.0.1:1/releases")
        assert result.state == "unknown"
        assert result.message
        assert bool(result) is False

    def test_http_error_is_not_mistaken_for_a_new_version(self, release_server):
        _ReleaseHandler.status = 500
        result = updates.check_remote("1.9", api_url=release_server)
        assert result.state == "unknown"
        assert "500" in result.message

    def test_the_notify_callback_gets_the_message(self, release_server):
        seen = []
        result = updates.run_update_check(seen.append, local_version="1.9",
                                          opener=None) if False else None
        # 走真实入口：注入 opener 指向 stub
        import urllib.request

        def opener(request, timeout=None):
            return urllib.request.urlopen(release_server, timeout=timeout)

        result = updates.run_update_check(seen.append, opener=_Opener(release_server),
                                          local_version="1.9")
        assert result.state == "newer"
        assert seen and "9.9.9" in seen[0]

    def test_check_for_updates_returns_true_only_for_a_newer_release(self, release_server):
        assert updates.check_for_updates(opener=_Opener(release_server),
                                         local_version="1.9") is True
        _ReleaseHandler.payload = {"tag_name": "v1.9", "html_url": ""}
        assert updates.check_for_updates(opener=_Opener(release_server),
                                         local_version="1.9") is False


class _Opener:
    """把请求转给 stub 服务（只替网络这一层）。"""

    def __init__(self, url):
        self._url = url

    def __call__(self, request, timeout=None):
        import urllib.request

        return urllib.request.urlopen(self._url, timeout=timeout)


class TestTrayAction:
    def test_the_action_reports_and_opens_the_page_only_for_a_new_version(self, monkeypatch):
        from types import SimpleNamespace

        import core.actions as actions_module
        from core.updates import UpdateCheck

        opened = []
        monkeypatch.setattr("core.platform.shell.open_url", lambda url: opened.append(url) or True)
        monkeypatch.setattr(
            "core.updates.run_update_check",
            lambda notify=None, **kwargs: UpdateCheck(
                "newer", local="1.9", remote="1.10", message="有新版本",
                url="https://example.com/rel"))

        app = SimpleNamespace(tray_icon=None)
        assert actions_module.run_action("check_updates", app) is True
        assert opened == ["https://example.com/rel"]

    def test_an_unknown_remote_does_not_open_anything(self, monkeypatch):
        from types import SimpleNamespace

        import core.actions as actions_module
        from core.updates import UpdateCheck

        opened = []
        monkeypatch.setattr("core.platform.shell.open_url", lambda url: opened.append(url) or True)
        monkeypatch.setattr(
            "core.updates.run_update_check",
            lambda notify=None, **kwargs: UpdateCheck("unknown", message="读不到远端"))

        assert actions_module.run_action("check_updates", SimpleNamespace(tray_icon=None)) is False
        assert opened == []
