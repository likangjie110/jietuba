# -*- coding: utf-8 -*-
"""平台层进程能力（core/platform/process）单元测试。

Windows 分支在本机（macOS）跑不到，所以用假的 ``ctypes.windll`` / 假 kernel32 驱动它，
断言真的读对了字段、该关的句柄关了、该回退的路径回退了。这半边是迁移前完全没人测的
部分：原 ``core/platform_utils.py`` 224 行 Win32 代码一个平台守卫都没有，只靠
``except Exception`` 把 ``ctypes.windll`` 不存在这件事吞掉。

另外半边钉住「非 Windows 早退」：返回值必须与迁移前一致（None / False / 无副作用），
但不再制造异常。
"""

import ctypes
from types import SimpleNamespace

import pytest

from core.platform import process


class _FakeFunction:
    """记录调用并在需要时模拟失败。"""

    def __init__(self, returns=1, on_call=None):
        self.returns = returns
        self.on_call = on_call
        self.calls = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.on_call is not None:
            return self.on_call(*args, **kwargs)
        return self.returns


class _FakeKernel32:
    def __init__(self, *, open_process=1, get_times=True, image_path=None):
        self.OpenProcess = _FakeFunction(open_process)
        self.CloseHandle = _FakeFunction(1)
        self.TerminateProcess = _FakeFunction(1)
        self._get_times = get_times
        self._image_path = image_path

        def _get_process_times(_handle, creation, *_rest):
            if not self._get_times:
                return 0
            creation._obj.dwHighDateTime = 0x0000_0001
            creation._obj.dwLowDateTime = 0x0000_0002
            return 1

        self.GetProcessTimes = _FakeFunction(1, on_call=_get_process_times)

        def _query_image(_handle, _flags, buffer, _size):
            if self._image_path is None:
                return 0
            buffer.value = self._image_path
            return 1

        self.QueryFullProcessImageNameW = _FakeFunction(1, on_call=_query_image)


@pytest.fixture
def windows(monkeypatch):
    """把模块切到 Windows 分支并换掉 kernel32。"""
    fake = _FakeKernel32()
    monkeypatch.setattr(process, "IS_WINDOWS", True)
    monkeypatch.setattr(process, "_kernel32_ref", None)
    monkeypatch.setattr(process, "_kernel32", lambda: fake)
    return fake


class TestUnsupportedPlatforms:
    """非 Windows 的行为必须与迁移前逐字一致：那时所有调用都走 except 分支。"""

    @pytest.fixture(autouse=True)
    def _off_windows(self, monkeypatch):
        monkeypatch.setattr(process, "IS_WINDOWS", False)

    def test_working_set_trim_is_a_noop(self):
        process.trim_working_set()  # 不抛异常

    def test_trim_request_is_a_noop(self):
        process.request_trim_working_set()  # 不建 QTimer、不抛异常

    def test_dpi_and_app_id_are_noops(self):
        process.set_dpi_awareness()
        process.set_app_user_model_id("test.app")

    def test_process_identity_is_none(self):
        assert process.get_process_identity(1234) is None

    def test_terminate_returns_false(self):
        assert process.terminate_process_by_pid(1234) is False

    def test_negative_pid_is_rejected_even_on_windows(self, windows):
        assert process.get_process_identity(0) is None
        assert windows.OpenProcess.calls == []


class TestProcessIdentity:
    def test_reads_creation_time_and_image_name(self, windows):
        windows._image_path = r"C:\Program Files\Jietuba\jietuba.exe"
        identity = process.get_process_identity(4242)
        assert identity == ((0x1 << 32) | 0x2, "jietuba.exe")

    def test_uses_ntpath_so_windows_paths_split_on_macos(self, windows):
        """Windows 路径要用 ntpath 拆，os.path 在 macOS 上按 '/' 拆不出文件名。"""
        windows._image_path = r"C:\Users\mac\AppData\jietuba_pp.exe"
        assert process.get_process_identity(1)[1] == "jietuba_pp.exe"

    def test_open_process_failure_means_gone(self, windows):
        windows.OpenProcess = _FakeFunction(0)
        assert process.get_process_identity(999) is None
        assert windows.CloseHandle.calls == []

    def test_get_process_times_failure_still_closes_the_handle(self, windows):
        windows._get_times = False
        assert process.get_process_identity(999) is None
        assert len(windows.CloseHandle.calls) == 1

    def test_unavailable_image_name_is_empty_not_an_error(self, windows):
        windows._image_path = None
        identity = process.get_process_identity(7)
        assert identity[1] == ""

    def test_handle_is_always_closed(self, windows):
        process.get_process_identity(7)
        assert len(windows.CloseHandle.calls) == 1

    def test_never_raises(self, windows):
        def _boom(*_a, **_k):
            raise OSError("boom")

        windows.OpenProcess = _FakeFunction(on_call=_boom)
        assert process.get_process_identity(7) is None


class TestTerminateProcess:
    def test_success_closes_the_handle(self, windows):
        assert process.terminate_process_by_pid(42) is True
        assert len(windows.CloseHandle.calls) == 1

    def test_open_failure_returns_false_without_closing(self, windows):
        windows.OpenProcess = _FakeFunction(0)
        assert process.terminate_process_by_pid(42) is False
        assert windows.CloseHandle.calls == []

    def test_terminate_failure_returns_false_but_still_closes(self, windows):
        windows.TerminateProcess = _FakeFunction(0)
        assert process.terminate_process_by_pid(42) is False
        assert len(windows.CloseHandle.calls) == 1

    def test_never_raises(self, windows):
        windows.TerminateProcess = _FakeFunction(on_call=lambda *a, **k: (_ for _ in ()).throw(OSError()))
        assert process.terminate_process_by_pid(42) is False


class TestWindowsNativeCalls:
    """工作集、DPI、任务栏标识这三处直接用 ctypes.windll，用假模块驱动。"""

    @pytest.fixture
    def windll(self, monkeypatch):
        """给 ctypes 挂一个假的 windll（真 ctypes 在 macOS/Linux 上没有这个属性）。"""
        fake = SimpleNamespace(
            kernel32=SimpleNamespace(
                GetCurrentProcess=_FakeFunction(123),
                SetProcessWorkingSetSize=_FakeFunction(1),
            ),
            shcore=SimpleNamespace(SetProcessDpiAwareness=_FakeFunction(1)),
            user32=SimpleNamespace(SetProcessDPIAware=_FakeFunction(1)),
            shell32=SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=_FakeFunction(1)),
        )
        monkeypatch.setattr(process, "IS_WINDOWS", True)
        monkeypatch.setattr(process.ctypes, "windll", fake, raising=False)
        return fake

    def test_working_set_trim_calls_the_api(self, windll):
        process.trim_working_set()
        assert windll.kernel32.SetProcessWorkingSetSize.calls
        # HANDLE 必须显式声明类型，否则 64 位下会被按 c_int 截断
        assert windll.kernel32.GetCurrentProcess.restype is not None
        assert windll.kernel32.SetProcessWorkingSetSize.argtypes is not None

    def test_working_set_trim_swallows_errors(self, windll, monkeypatch):
        monkeypatch.setattr(
            process.ctypes, "windll",
            SimpleNamespace(kernel32=SimpleNamespace(GetCurrentProcess=lambda: 1,
                                                     SetProcessWorkingSetSize=_FakeFunction(
                                                         on_call=lambda *a, **k: (_ for _ in ()).throw(OSError())))),
            raising=False,
        )
        process.trim_working_set()  # 不抛异常

    def test_dpi_awareness_prefers_per_monitor(self, windll):
        process.set_dpi_awareness()
        assert len(windll.shcore.SetProcessDpiAwareness.calls) == 1
        assert windll.shcore.SetProcessDpiAwareness.calls[0][0] == (2,)
        assert windll.user32.SetProcessDPIAware.calls == []

    def test_dpi_awareness_falls_back_to_the_legacy_api(self, monkeypatch, windll):
        """ShCore 在 Windows 8.1 之前不存在，回退必须还在。"""
        def _boom(*_a, **_k):
            raise OSError("no shcore")

        monkeypatch.setattr(
            process.ctypes, "windll",
            SimpleNamespace(
                shcore=SimpleNamespace(SetProcessDpiAwareness=_FakeFunction(on_call=_boom)),
                user32=windll.user32,
            ),
            raising=False,
        )
        process.set_dpi_awareness()
        assert len(windll.user32.SetProcessDPIAware.calls) == 1

    def test_app_user_model_id_is_forwarded(self, windll):
        process.set_app_user_model_id("jietuba.app")
        assert windll.shell32.SetCurrentProcessExplicitAppUserModelID.calls[0][0] == ("jietuba.app",)

    def test_trim_working_set_is_debounced_through_one_timer(self, monkeypatch):
        """多次请求只留最后一次：每次新建定时器会在密集调用时堆一批唤醒。"""
        started = []

        class _FakeTimer:
            def __init__(self):
                self.timeout = SimpleNamespace(connect=lambda _fn: None)
                self.single_shot = None

            def setSingleShot(self, value):  # noqa: N802
                self.single_shot = value

            def start(self, delay):
                started.append(delay)

        monkeypatch.setattr(process, "IS_WINDOWS", True)
        monkeypatch.setattr(process, "_trim_timer", None)

        import PySide6.QtCore as qtcore

        monkeypatch.setattr(qtcore, "QTimer", _FakeTimer)
        process.request_trim_working_set(10)
        process.request_trim_working_set(20)
        assert started == [10, 20]
        assert isinstance(process._trim_timer, _FakeTimer)
        assert process._trim_timer.single_shot is True


class TestCtypesAvailability:
    def test_wintypes_is_importable_on_every_platform(self):
        """迁移前担心过 ``from ctypes import wintypes`` 在 macOS 上会炸；
        实测它可导入（windll 才不存在），所以模块级导入是安全的。"""
        from ctypes import wintypes

        assert wintypes.HANDLE is ctypes.c_void_p


class TestPlatformVersionPatch:
    """启动期补丁：让 platform.version() 不再启动 cmd.exe。

    PyInstaller onefile 打包后，「从 %TEMP% 解压再启动 cmd.exe」会被杀毒软件误报，
    所以用 sys.getwindowsversion()（纯 Win32 API）替代，结果一致且不创建子进程。
    """

    def test_noop_off_windows(self, monkeypatch):
        monkeypatch.setattr(process, "IS_WINDOWS", False)
        assert process.patch_platform_version_lookup() is False

    def test_replaces_syscmd_ver_on_windows(self, monkeypatch):
        import platform as platform_module

        called = []
        monkeypatch.setattr(process, "IS_WINDOWS", True)
        monkeypatch.setattr(platform_module, "_syscmd_ver", lambda *a, **k: called.append(a),
                            raising=False)
        monkeypatch.setattr(
            process.sys, "getwindowsversion",
            lambda: SimpleNamespace(major=10, minor=0, build=22631), raising=False,
        )

        assert process.patch_platform_version_lookup() is True
        result = platform_module._syscmd_ver()
        assert result == ("Microsoft Windows", "10", "10.0.22631")
        assert called == [], "替换后不该再走标准库那条会创建子进程的实现"

    def test_missing_internal_hook_does_not_break_startup(self, monkeypatch):
        """标准库改了内部实现时补丁失效，但不能让启动失败。"""
        import platform as platform_module

        monkeypatch.setattr(process, "IS_WINDOWS", True)
        monkeypatch.delattr(platform_module, "_syscmd_ver", raising=False)
        assert process.patch_platform_version_lookup() is False
