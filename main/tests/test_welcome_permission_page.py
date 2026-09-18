# -*- coding: utf-8 -*-
"""欢迎向导里的系统权限步骤。

步骤数由平台层声明决定：macOS 多一步（权限），Windows/Linux 仍是 6 步且没有这一页。
"""
import pytest
from PySide6.QtCore import QSettings

from core.platform import permissions
from settings.tool_settings import ToolSettingsManager
from ui.welcome.page_permission import PermissionGuidePage
from ui.welcome.wizard import WelcomeWizard


def _manager(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "welcome.ini"), QSettings.Format.IniFormat)
    )


def _fake_permission(key=permissions.ACCESSIBILITY):
    return permissions.Permission(
        key=key,
        trusted=lambda: True,
        request=lambda: True,
        settings_url="x-test:panel",
    )


def _build(qapp, monkeypatch, tmp_path, requirements):
    monkeypatch.setattr(permissions, "requirements", lambda *a, **k: requirements)
    return WelcomeWizard(_manager(tmp_path))


class TestWizardPermissionStep:

    def test_step_is_added_and_counted(self, qapp, monkeypatch, tmp_path):
        wizard = _build(qapp, monkeypatch, tmp_path, (_fake_permission(),))

        try:
            assert wizard.PAGE_COUNT == 7
            assert len(wizard._pages) == 7
            assert wizard._stack.count() == 7
            # 权限是热键与截图的前置，紧跟欢迎页
            assert isinstance(wizard._pages[1], PermissionGuidePage)
            # 侧栏那一步的标题来自页面标题，说明连到了向导的步骤条上
            assert wizard._step_items[1].accessibleName() != ""
        finally:
            wizard.close()
            wizard.deleteLater()
            qapp.processEvents()

    def test_step_is_absent_without_permission_requirements(self, qapp, monkeypatch, tmp_path):
        """Windows/Linux 没有这层系统门槛：不该出现一步点了没用的权限页。"""
        wizard = _build(qapp, monkeypatch, tmp_path, ())

        try:
            assert wizard.PAGE_COUNT == 6
            assert wizard._stack.count() == 6
            assert not any(
                isinstance(page, PermissionGuidePage) for page in wizard._pages
            )
        finally:
            wizard.close()
            wizard.deleteLater()
            qapp.processEvents()

    def test_reused_list_is_the_settings_one(self, qapp, monkeypatch, tmp_path):
        """向导与设置页共用同一份清单组件，不是各画一套。"""
        wizard = _build(qapp, monkeypatch, tmp_path, (_fake_permission(),))

        try:
            page = wizard._pages[1]
            from ui.settings_ui.page_permission import PermissionList

            assert isinstance(page.permission_list, PermissionList)
            assert [row.permission.key for row in page.permission_list._permission_rows] == [
                permissions.ACCESSIBILITY
            ]
        finally:
            wizard.close()
            wizard.deleteLater()
            qapp.processEvents()

    def test_saving_the_wizard_does_not_fail_on_the_permission_step(
        self, qapp, monkeypatch, tmp_path
    ):
        """向导关闭时会对每一页调 save()，这一页没有配置项，但也不能抛异常。"""
        monkeypatch.setattr(
            "ui.welcome.wizard.log_exception",
            lambda *a, **k: pytest.fail("权限步骤的 save() 抛异常了"),
        )
        wizard = _build(qapp, monkeypatch, tmp_path, (_fake_permission(),))

        wizard.close()
        wizard.deleteLater()
        qapp.processEvents()
