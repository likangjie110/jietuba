# -*- coding: utf-8 -*-
"""权限提示、跨模块动作与「重开本程序」。

三件事都不该在真机上真跑一遍：提示弹窗、打开系统设置、重启进程。所以这里全部用替身
验证「接线对不对」——真正需要真机确认的部分（重启后新实例起不起得来）在代码注释里写明。
"""
import os
import sys
import threading
from types import SimpleNamespace

import pytest

import main_app
from core.platform import permissions, process
from ui import permission_actions, permission_prompt


@pytest.fixture(autouse=True)
def clean_prompt_state():
    permission_prompt.reset_prompt_state()
    yield
    permission_prompt.reset_prompt_state()


def _record_dialog(monkeypatch, action):
    """把提示弹窗换成记录器，返回记录列表。"""
    calls = []

    def fake(parent, title, message, buttons):
        calls.append({
            "parent": parent,
            "title": title,
            "message": message,
            "buttons": [button["id"] for button in buttons],
        })
        return action

    monkeypatch.setattr(permission_prompt, "show_custom_confirm_dialog", fake)
    return calls


class TestPromptMissingPermission:

    def test_prompts_once_per_permission(self, qapp, monkeypatch):
        """抓屏是高频动作：同一条权限每进程只该打扰一次。"""
        calls = _record_dialog(monkeypatch, "later")

        assert permission_prompt.prompt_missing_permission(
            permissions.SCREEN_RECORDING
        ) is True
        assert permission_prompt.prompt_missing_permission(
            permissions.SCREEN_RECORDING
        ) is False

        assert len(calls) == 1

    def test_each_permission_gets_its_own_prompt(self, qapp, monkeypatch):
        calls = _record_dialog(monkeypatch, "later")

        permission_prompt.prompt_missing_permission(permissions.ACCESSIBILITY)
        permission_prompt.prompt_missing_permission(permissions.SCREEN_RECORDING)

        assert len(calls) == 2
        assert calls[0]["message"] != calls[1]["message"]

    def test_offer_carries_the_permission_settings_entry(self, qapp, monkeypatch):
        calls = _record_dialog(monkeypatch, "later")

        permission_prompt.prompt_missing_permission(permissions.ACCESSIBILITY)

        assert calls[0]["buttons"] == ["open", "later"]

    def test_open_action_jumps_to_the_permission_page(self, qapp, monkeypatch):
        _record_dialog(monkeypatch, "open")
        opened = []
        monkeypatch.setattr(
            permission_actions, "open_permission_settings",
            lambda: opened.append(True) or True,
        )

        permission_prompt.prompt_missing_permission(permissions.ACCESSIBILITY)

        assert opened == [True]

    def test_later_action_opens_nothing(self, qapp, monkeypatch):
        _record_dialog(monkeypatch, "later")
        opened = []
        monkeypatch.setattr(
            permission_actions, "open_permission_settings",
            lambda: opened.append(True) or True,
        )

        permission_prompt.prompt_missing_permission(permissions.ACCESSIBILITY)

        assert opened == []

    def test_prompt_is_skipped_off_the_main_thread(self, qapp, monkeypatch):
        """抓屏可能跑在后台线程，Qt 对话框不能在别的线程上 exec。"""
        calls = _record_dialog(monkeypatch, "later")
        results = []

        thread = threading.Thread(
            target=lambda: results.append(
                permission_prompt.prompt_missing_permission(permissions.SCREEN_RECORDING)
            )
        )
        thread.start()
        thread.join()

        assert results == [False]
        assert calls == []


class TestOpenPermissionSettings:

    def test_opens_the_window_and_jumps_to_the_page(self, monkeypatch):
        calls = []
        app = SimpleNamespace(
            open_settings=lambda: calls.append("open"),
            settings_window=SimpleNamespace(
                show_permission_page=lambda: calls.append("jump") or True
            ),
        )
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)

        assert permission_actions.open_permission_settings() is True
        assert calls == ["open", "jump"]

    def test_reports_failure_without_an_app(self, monkeypatch):
        monkeypatch.setattr(main_app, "main_app_instance", lambda: None)

        assert permission_actions.open_permission_settings() is False

    def test_reports_failure_when_the_platform_has_no_permission_page(self, monkeypatch):
        app = SimpleNamespace(
            open_settings=lambda: None,
            settings_window=SimpleNamespace(show_permission_page=lambda: False),
        )
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)

        assert permission_actions.open_permission_settings() is False


class TestRestartApplication:

    def test_restarts_then_quits_the_current_instance(self, monkeypatch):
        quits = []
        app = SimpleNamespace(quit_app=lambda: quits.append(True))
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)
        monkeypatch.setattr(process, "relaunch_application", lambda: True)

        assert permission_actions.restart_application() is True
        assert quits == [True]

    def test_keeps_running_when_the_restart_cannot_be_arranged(self, monkeypatch):
        """重开动作交不出去时不能把程序退掉——那等于把用户的程序关了还不重开。"""
        quits = []
        app = SimpleNamespace(quit_app=lambda: quits.append(True))
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)
        monkeypatch.setattr(process, "relaunch_application", lambda: False)

        assert permission_actions.restart_application() is False
        assert quits == []


class TestHotkeyPermissionWiring:

    def test_facade_forwards_the_permission_signal(self):
        """HotkeySystem 是应用层唯一入口，它得把权限信号转出来（不去碰内部单例）。"""
        from core.shortcut_manager import HotkeySystem

        sentinel = object()
        stub = SimpleNamespace(_mgr=SimpleNamespace(input_permission_missing=sentinel))

        assert HotkeySystem.permission_missing.fget(stub) is sentinel

    def test_main_app_connects_the_signal_to_the_prompt(self):
        """这条接线断了只会在启动时炸；用源码断言把它钉住。"""
        import inspect

        from main_app import MainApp

        assert "permission_missing.connect" in inspect.getsource(MainApp.__init__)

    def test_capture_path_prompts_from_the_main_thread_callback(self):
        """抓屏提示挂在截图完成回调上（主线程），不能挂在后台抓屏服务里。"""
        import inspect

        from main_app import MainApp

        source = inspect.getsource(MainApp._on_capture_ready)
        assert "_prompt_missing_capture_permission" in source


class TestRelaunchApplication:

    @staticmethod
    def _record_popen(monkeypatch):
        calls = []
        monkeypatch.setattr(
            process.subprocess, "Popen",
            lambda argv, **kwargs: calls.append((argv, kwargs)),
        )
        return calls

    def test_macos_bundle_is_reopened_through_launch_services(self, monkeypatch):
        """打包版必须走 open -a：TCC 按 bundle 的代码签名认应用，exec 内层二进制会丢身份。"""
        calls = self._record_popen(monkeypatch)
        monkeypatch.setattr(
            process, "_macos_app_bundle", lambda: "/Applications/Jietuba.app"
        )

        assert process.relaunch_application() is True

        argv, kwargs = calls[0]
        assert argv[:2] == ["/bin/sh", "-c"]
        helper = argv[2]
        assert f"kill -0 {os.getpid()}" in helper
        assert "open -a" in helper
        assert "/Applications/Jietuba.app" in helper
        # 父进程退出后这个 shell 还要活着，否则等不到 pid 消失
        assert kwargs.get("start_new_session") or kwargs.get("creationflags")

    def test_source_run_reopens_the_same_interpreter(self, monkeypatch):
        calls = self._record_popen(monkeypatch)
        monkeypatch.setattr(process, "_macos_app_bundle", lambda: None)

        assert process.relaunch_application() is True

        argv, kwargs = calls[0]
        assert argv == [sys.executable, *sys.argv]
        assert kwargs["cwd"] == os.getcwd()

    def test_reports_failure_instead_of_raising(self, monkeypatch):
        def boom(argv, **kwargs):
            raise OSError("no exec")

        monkeypatch.setattr(process.subprocess, "Popen", boom)
        monkeypatch.setattr(process, "_macos_app_bundle", lambda: None)

        assert process.relaunch_application() is False

    def test_bundle_is_only_detected_for_a_frozen_macos_app(self, monkeypatch):
        monkeypatch.setattr(
            process.sys, "executable",
            "/Applications/Jietuba.app/Contents/MacOS/Jietuba",
        )

        monkeypatch.setattr(process, "IS_MACOS", False)
        monkeypatch.setattr(process.sys, "frozen", True, raising=False)
        assert process._macos_app_bundle() is None

        monkeypatch.setattr(process, "IS_MACOS", True)
        assert process._macos_app_bundle() == "/Applications/Jietuba.app"

    def test_bundle_is_not_invented_for_a_frozen_build_outside_an_app(self, monkeypatch):
        monkeypatch.setattr(process, "IS_MACOS", True)
        monkeypatch.setattr(process.sys, "frozen", True, raising=False)
        monkeypatch.setattr(process.sys, "executable", "/opt/jietuba/jietuba")

        assert process._macos_app_bundle() is None
