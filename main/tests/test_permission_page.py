# -*- coding: utf-8 -*-
"""权限清单（core/platform/permissions）与设置里的权限页。

清单是平台层声明、界面按声明渲染的两段式：这里用假权限条目驱动页面（不依赖本机真的
缺不缺权限），另外单独断言 macOS / Windows / Linux 三张清单的内容——CI 在 Windows 上
跑，断言必须能在任何平台上验证另外两个平台的答案。
"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel, QWidget

from core.platform import capture as platform_capture
from core.platform import hotkey as platform_hotkey
from core.platform import permissions, shell
from settings.tool_settings import ToolSettingsManager
from ui.settings_ui import page_permission
from ui.settings_ui.dialog import SettingsDialog
from ui.settings_ui.page_permission import create_permission_page


def _fake_permission(key=permissions.ACCESSIBILITY, *, granted=False,
                     requires_restart=False, settings_url="x-test:panel",
                     grants_on_request=False):
    """一条假权限：状态与请求次数都可控，用来驱动页面。"""
    state = {"granted": granted, "requests": 0}

    def trusted():
        return state["granted"]

    def request():
        state["requests"] += 1
        if grants_on_request:
            state["granted"] = True
        return state["granted"]

    permission = permissions.Permission(
        key=key,
        trusted=trusted,
        request=request,
        settings_url=settings_url,
        requires_restart=requires_restart,
    )
    return permission, state


def _manager(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "permissions.ini"), QSettings.Format.IniFormat)
    )


@pytest.fixture
def english(monkeypatch):
    """页面文案按源码原文断言，不受运行环境语言影响。"""
    monkeypatch.setattr(page_permission, "_tr", lambda text: text)


# ============================================================================
# 平台层：清单内容与会话态
# ============================================================================

class TestPermissionRequirements:

    def test_macos_declares_the_two_gated_permissions(self):
        requirements = permissions.requirements("macos")

        assert [item.key for item in requirements] == [
            permissions.ACCESSIBILITY,
            permissions.SCREEN_RECORDING,
        ]

    def test_macos_entries_reuse_the_platform_layer_checks(self):
        """清单只做归类，检测与请求仍必须是平台层那一份实现。"""
        by_key = {item.key: item for item in permissions.requirements("macos")}

        assert by_key[permissions.ACCESSIBILITY].trusted is platform_hotkey.input_monitoring_trusted
        assert by_key[permissions.ACCESSIBILITY].request is platform_hotkey.request_input_monitoring_permission
        assert by_key[permissions.SCREEN_RECORDING].trusted is platform_capture.screen_capture_trusted
        assert by_key[permissions.SCREEN_RECORDING].request is platform_capture.request_screen_capture_permission

    def test_only_screen_recording_needs_a_restart(self):
        """屏幕录制权限是进程启动时读一次的，辅助功能授权后立刻生效。"""
        by_key = {item.key: item for item in permissions.requirements("macos")}

        assert by_key[permissions.SCREEN_RECORDING].requires_restart is True
        assert by_key[permissions.ACCESSIBILITY].requires_restart is False

    def test_macos_entries_point_at_their_system_settings_pane(self):
        for item in permissions.requirements("macos"):
            assert item.settings_url.startswith("x-apple.systempreferences:")

    @pytest.mark.parametrize("platform", ["windows", "linux"])
    def test_other_platforms_have_nothing_to_grant(self, platform):
        assert permissions.requirements(platform) == ()


class TestPendingRestart:

    def setup_method(self):
        permissions.reset_session_state()

    def test_granting_during_this_run_is_reported_as_pending(self):
        permission, state = _fake_permission(
            permissions.SCREEN_RECORDING, requires_restart=True
        )

        assert permissions.trusted(permission) is False
        assert permissions.pending_restart(permission) is False

        state["granted"] = True
        assert permissions.pending_restart(permission) is True

    def test_starting_granted_never_pends(self):
        permission, _state = _fake_permission(
            permissions.SCREEN_RECORDING, granted=True, requires_restart=True
        )

        assert permissions.trusted(permission) is True
        assert permissions.pending_restart(permission) is False

    def test_permissions_that_apply_immediately_never_pend(self):
        permission, state = _fake_permission(permissions.ACCESSIBILITY)

        permissions.trusted(permission)
        state["granted"] = True

        assert permissions.pending_restart(permission) is False

    def test_startup_snapshot_wins_over_the_first_page_lookup(self, monkeypatch):
        """用户先授权、后打开设置页时，页面第一次查到的状态不能当成启动态。"""
        permission, state = _fake_permission(
            permissions.SCREEN_RECORDING, requires_restart=True
        )
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: (permission,))
        permissions.note_startup_state()

        state["granted"] = True
        assert permissions.trusted(permission) is True
        assert permissions.pending_restart(permission) is True


class TestStartupSnapshot:

    def test_startup_step_records_the_state_before_anything_is_granted(
        self, monkeypatch
    ):
        """启动链要在抓屏权限那一步记下启动态，否则「已授权但要重启」判断不出来。"""
        from core.bootstrap import PreloadManager

        permission, state = _fake_permission(
            permissions.SCREEN_RECORDING, requires_restart=True
        )
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: (permission,))
        monkeypatch.setattr(platform_capture, "screen_capture_trusted", lambda: True)
        permissions.reset_session_state()

        manager = PreloadManager(
            SimpleNamespace(config_manager=SimpleNamespace(get_app_setting=lambda *a, **k: True))
        )
        manager._ensure_screen_capture_permission()

        state["granted"] = True
        assert permissions.pending_restart(permission) is True


class TestOpenSettings:
    def test_opens_the_declared_panel_through_the_shell(self, monkeypatch):
        opened = []
        monkeypatch.setattr(shell, "open_url", lambda url: opened.append(url) or True)
        permission, _state = _fake_permission(settings_url="x-apple.systempreferences:abc")

        assert permissions.open_settings(permission) is True
        assert opened == ["x-apple.systempreferences:abc"]


# ============================================================================
# 权限页
# ============================================================================

class TestPermissionPage:

    def test_renders_one_row_per_requirement(self, qapp, monkeypatch, english):
        monkeypatch.setattr(
            permissions, "requirements",
            lambda *args, **kwargs: (_fake_permission()[0],),
        )
        page = create_permission_page(SimpleNamespace())

        try:
            assert page.findChild(QWidget, "PermissionRow_accessibility") is not None
            status = page.findChild(QLabel, "PermissionStatus_accessibility")
            assert status.text() == "Not Granted"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_status_follows_the_system_state(self, qapp, monkeypatch, english):
        permission, state = _fake_permission()
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: (permission,))
        page = create_permission_page(SimpleNamespace())

        try:
            status = page.findChild(QLabel, "PermissionStatus_accessibility")
            assert status.text() == "Not Granted"

            state["granted"] = True
            page.permission_list.refresh()

            assert status.text() == "Granted"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_authorize_asks_the_platform_layer_and_refreshes(self, qapp, monkeypatch, english):
        permission, state = _fake_permission(grants_on_request=True)
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: (permission,))
        page = create_permission_page(SimpleNamespace())

        try:
            page.findChild(QWidget, "PermissionGrant_accessibility").click()

            assert state["requests"] == 1
            assert page.findChild(
                QLabel, "PermissionStatus_accessibility"
            ).text() == "Granted"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_authorize_is_disabled_once_granted(self, qapp, monkeypatch, english):
        permission, state = _fake_permission()
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: (permission,))
        page = create_permission_page(SimpleNamespace())

        try:
            button = page.findChild(QWidget, "PermissionGrant_accessibility")
            assert button.isEnabled()

            state["granted"] = True
            page.permission_list.refresh()

            assert not button.isEnabled()
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_restart_button_appears_only_while_pending_restart(
        self, qapp, monkeypatch, english
    ):
        permission, state = _fake_permission(
            permissions.SCREEN_RECORDING, requires_restart=True
        )
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: (permission,))
        page = create_permission_page(SimpleNamespace())

        try:
            button = page.findChild(QWidget, "PermissionRestartButton_screen_recording")
            assert button.isHidden()

            state["granted"] = True
            page.permission_list.refresh()

            assert not button.isHidden()
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_restart_button_asks_for_confirmation_first(self, qapp, monkeypatch, english):
        """重启会退出用户正在用的程序，必须确认；用户点「取消」时不能重开。"""
        from ui.settings_ui import page_permission

        permission, state = _fake_permission(
            permissions.SCREEN_RECORDING, requires_restart=True
        )
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: (permission,))
        restarts = []
        monkeypatch.setattr(
            "ui.permission_actions.restart_application",
            lambda: restarts.append(True) or True,
        )
        confirmed = []
        monkeypatch.setattr(
            page_permission, "show_confirm_dialog",
            lambda parent, title, message: confirmed.append(title) or False,
        )
        page = create_permission_page(SimpleNamespace())

        try:
            state["granted"] = True
            page.permission_list.refresh()
            button = page.findChild(QWidget, "PermissionRestartButton_screen_recording")
            button.click()
            assert confirmed == ["Restart jietuba"]
            assert restarts == []

            monkeypatch.setattr(
                page_permission, "show_confirm_dialog",
                lambda parent, title, message: True,
            )
            button.click()
            assert restarts == [True]
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_open_system_settings_button_uses_the_declared_url(self, qapp, monkeypatch, english):
        opened = []
        monkeypatch.setattr(shell, "open_url", lambda url: opened.append(url) or True)
        permission, _state = _fake_permission(settings_url="x-apple.systempreferences:xyz")
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: (permission,))
        page = create_permission_page(SimpleNamespace())

        try:
            page.findChild(QWidget, "PermissionSettings_accessibility").click()

            assert opened == ["x-apple.systempreferences:xyz"]
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_restart_hint_appears_only_after_granting_a_restart_gated_permission(
        self, qapp, monkeypatch, english
    ):
        permission, state = _fake_permission(
            permissions.SCREEN_RECORDING, requires_restart=True
        )
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: (permission,))
        page = create_permission_page(SimpleNamespace())

        try:
            hint = page.findChild(QLabel, "PermissionRestart_screen_recording")
            assert hint.isHidden()

            state["granted"] = True
            page.permission_list.refresh()

            assert not hint.isHidden()
            assert hint.text() == "Granted. Restart jietuba for it to take effect."
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_polls_the_state_only_while_the_page_is_visible(self, qapp, monkeypatch, english):
        monkeypatch.setattr(
            permissions, "requirements", lambda *a, **k: (_fake_permission()[0],)
        )
        page = create_permission_page(SimpleNamespace())

        try:
            assert page.permission_list._permission_timer.isActive() is False

            page.show()
            assert page.permission_list._permission_timer.isActive() is True

            page.hide()
            assert page.permission_list._permission_timer.isActive() is False
        finally:
            page.deleteLater()
            qapp.processEvents()


# ============================================================================
# 设置对话框接线
# ============================================================================

def _dialog(tmp_path):
    manager = _manager(tmp_path)
    manager.set_log_dir(str(tmp_path))
    return SettingsDialog(manager)


class TestSettingsDialogWiring:

    def test_page_and_navigation_exist_when_the_platform_declares_permissions(
        self, qapp, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            permissions, "requirements", lambda *a, **k: (_fake_permission()[0],)
        )
        dialog = _dialog(tmp_path)

        try:
            assert "permissions" in [item[0] for item in dialog._nav_items]
            assert dialog.content_stack.count() == 13
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_page_is_absent_when_there_is_nothing_to_grant(
        self, qapp, monkeypatch, tmp_path
    ):
        """Windows/Linux 上没有这层系统门槛，不该出现一个空页面。"""
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: ())
        dialog = _dialog(tmp_path)

        try:
            assert "permissions" not in [item[0] for item in dialog._nav_items]
            assert dialog.content_stack.count() == 12
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_show_permission_page_jumps_to_the_page(self, qapp, monkeypatch, tmp_path):
        """抓屏/热键失败时的提示要把用户送到这一页，而不是让人自己去翻设置。"""
        monkeypatch.setattr(
            permissions, "requirements", lambda *a, **k: (_fake_permission()[0],)
        )
        dialog = _dialog(tmp_path)

        try:
            assert dialog.show_permission_page() is True
            assert dialog.content_stack.currentIndex() == 12
            assert dialog.content_title.text() == "Permissions"
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_show_permission_page_reports_absence_without_the_page(
        self, qapp, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(permissions, "requirements", lambda *a, **k: ())
        dialog = _dialog(tmp_path)

        try:
            assert dialog.show_permission_page() is False
        finally:
            dialog.deleteLater()
            qapp.processEvents()
