# -*- coding: utf-8 -*-
"""平台层凭据存储（core/platform/secrets）单元测试。

三条分支的验证强度不一样，测试里如实体现：

- **macOS**：跑真实 Keychain（service 名换成测试专用的，用例结束删干净）。这是唯一
  「真的能用」的分支，所以它是本文件的主要部分。
- **Windows**：假 advapi32 断言调用参数与解码逻辑（本机是 macOS，凭据管理器没有实机验证）。
- **Linux / 无后端**：只确认「如实返回空值、不抛异常」，不假装实现过。
"""

import ctypes
import os
import sys

import pytest

from core.platform import secrets


@pytest.fixture(autouse=True)
def _real_platform_api(monkeypatch, real_secret_api):
    """本文件验的就是平台层的真实现，所以要把 conftest 的会话级隔离临时撤掉。

    其它测试文件跑内存实现（不碰开发机钥匙串），这里取回 `core/platform/secrets` 的公开
    函数原件，Windows 分支再用假 DLL 驱动。
    """
    for name, original in real_secret_api.items():
        monkeypatch.setattr(secrets, name, original)


def _patch_backend(monkeypatch, platform: str) -> None:
    monkeypatch.setattr(secrets, "IS_MACOS", platform == "macos")
    monkeypatch.setattr(secrets, "IS_WINDOWS", platform == "windows")


class TestAvailability:
    def test_is_available_follows_the_capability(self, monkeypatch):
        monkeypatch.setattr("core.platform.capabilities.available", lambda *a, **k: False)
        assert secrets.is_available() is False
        monkeypatch.setattr("core.platform.capabilities.available", lambda *a, **k: True)
        assert secrets.is_available() is True

    def test_backend_names_are_registered_per_platform(self):
        from core.platform.capabilities import Capability, Support, backend_name, support

        assert backend_name(Capability.SECRET_STORE, "macos") == "keychain"
        assert backend_name(Capability.SECRET_STORE, "windows") == "credential-manager"
        assert support(Capability.SECRET_STORE, "linux") is Support.NONE
        assert backend_name(Capability.SECRET_STORE, "linux") == ""


class TestMacOSKeychain:
    """真实 Keychain 往返。

    用测试专用的 service 名 + 带 pid 的条目名：跑测试不会碰到用户自己存的 key，
    也不会和并行跑的另一次测试互相覆盖。
    """

    @pytest.fixture(autouse=True)
    def _backend(self, monkeypatch):
        if sys.platform != "darwin":
            pytest.skip("真实 Keychain 只在 macOS 上跑")
        monkeypatch.setattr(secrets, "SERVICE_NAME", "com.jietuba.app.tests")
        _patch_backend(monkeypatch, "macos")
        self._names = []
        yield
        for name in self._names:
            secrets._macos_delete(name)

    def _name(self, tag: str) -> str:
        name = f"pytest-{os.getpid()}-{tag}"
        self._names.append(name)
        return name

    def test_round_trip_keeps_non_ascii(self):
        name = self._name("round-trip")
        assert secrets.set_secret(name, "sk-密钥-123") is True
        assert secrets.get_secret(name) == "sk-密钥-123"

    def test_missing_entry_reads_as_empty(self):
        assert secrets.get_secret(self._name("missing")) == ""

    def test_second_write_overwrites_the_first(self):
        name = self._name("overwrite")
        secrets.set_secret(name, "first")
        secrets.set_secret(name, "second")
        assert secrets.get_secret(name) == "second"

    def test_empty_value_deletes_the_entry(self):
        name = self._name("empty")
        secrets.set_secret(name, "value")
        assert secrets.set_secret(name, "") is True
        assert secrets.get_secret(name) == ""

    def test_delete_is_idempotent(self):
        name = self._name("delete-twice")
        secrets.set_secret(name, "value")
        assert secrets.delete_secret(name) is True
        assert secrets.delete_secret(name) is True

    def test_entries_do_not_collide_across_names(self):
        first, second = self._name("a"), self._name("b")
        secrets.set_secret(first, "one")
        secrets.set_secret(second, "two")
        assert secrets.get_secret(first) == "one"
        assert secrets.get_secret(second) == "two"

    def test_empty_name_is_refused(self):
        assert secrets.get_secret("") == ""
        assert secrets.set_secret("", "value") is False
        assert secrets.delete_secret("") is False


class _FakeAdvapi32:
    """假的 advapi32：记录调用，按预设返回。error 是「这次调用失败时 GetLastError 报什么」。"""

    def __init__(self, blob: bytes = b"", read_ok: bool = True, error: int = 0,
                 write_ok: bool = True, delete_ok: bool = True):
        self.calls = []
        self.freed = []
        self._blob = blob
        self._read_ok = read_ok
        self._error = error
        self._write_ok = write_ok
        self._delete_ok = delete_ok
        self._credential = secrets._CredentialW()

    def CredReadW(self, target, cred_type, flags, out):  # noqa: N802
        self.calls.append(("CredReadW", target, cred_type, flags))
        if not self._read_ok:
            return False
        credential = secrets._CredentialW()
        credential.CredentialBlobSize = len(self._blob)
        buffer = ctypes.create_string_buffer(self._blob, max(1, len(self._blob)))
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
        self._credential = credential
        self._buffer = buffer          # 保持存活，否则 out 里的指针会悬空
        # 真 DLL 会往调用方的 out 参数里写；ctypes.byref(x) 暴露成 _obj，就是那个 x
        out._obj.contents = credential
        return True

    def CredWriteW(self, credential, flags):  # noqa: N802
        cred = getattr(credential, "_obj", None) or getattr(credential, "contents", credential)
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        self.calls.append(("CredWriteW", cred.TargetName, cred.Type, cred.Persist, blob,
                           cred.UserName, flags))
        return self._write_ok

    def CredDeleteW(self, target, cred_type, flags):  # noqa: N802
        self.calls.append(("CredDeleteW", target, cred_type, flags))
        return self._delete_ok

    def CredFree(self, pointer):  # noqa: N802
        self.freed.append(pointer)


class TestWindowsCredentialManager:
    """凭据管理器分支：本机是 macOS，用假 DLL 断言「发出了什么调用」。"""

    @pytest.fixture(autouse=True)
    def _backend(self, monkeypatch):
        _patch_backend(monkeypatch, "windows")

    def _use(self, monkeypatch, fake):
        monkeypatch.setattr(secrets, "_windows_api", lambda: fake)
        # ctypes 的 last-error 接口只有 Windows 有，测试里直接把错误码喂进去
        monkeypatch.setattr(secrets, "_last_error", lambda: fake._error)
        return fake

    def test_write_uses_generic_credential_target_and_utf8_blob(self, monkeypatch):
        fake = self._use(monkeypatch, _FakeAdvapi32())
        assert secrets.set_secret("deepl_api_key", "sk-密钥") is True
        kind, target, cred_type, persist, blob, user, _flags = fake.calls[0]
        assert kind == "CredWriteW"
        assert target == f"{secrets.WINDOWS_TARGET_PREFIX}deepl_api_key"
        assert cred_type == secrets.CRED_TYPE_GENERIC
        assert persist == secrets.CRED_PERSIST_LOCAL_MACHINE
        assert blob == "sk-密钥".encode("utf-8")
        assert user == "jietuba"

    def test_read_decodes_the_blob(self, monkeypatch):
        fake = self._use(monkeypatch, _FakeAdvapi32(blob="值-1".encode("utf-8")))
        assert secrets.get_secret("deepl_api_key") == "值-1"
        assert fake.calls[0][0] == "CredReadW"
        assert fake.freed, "读到的凭据必须 CredFree 掉，否则每次读都漏一块内存"

    def test_missing_entry_reads_as_empty(self, monkeypatch):
        self._use(monkeypatch, _FakeAdvapi32(read_ok=False, error=secrets.ERROR_NOT_FOUND))
        assert secrets.get_secret("nope") == ""

    def test_unexpected_read_error_is_reported(self, monkeypatch):
        logged = []
        monkeypatch.setattr("core.logger.log_warning", lambda *a, **k: logged.append(a))
        self._use(monkeypatch, _FakeAdvapi32(read_ok=False, error=5))
        assert secrets.get_secret("boom") == ""
        assert logged, "非「找不到」的错误必须留日志，不能和「没填过」混为一谈"

    def test_write_failure_returns_false(self, monkeypatch):
        self._use(monkeypatch, _FakeAdvapi32(write_ok=False, error=5))
        assert secrets.set_secret("key", "value") is False

    def test_delete_treats_missing_as_success(self, monkeypatch):
        self._use(monkeypatch, _FakeAdvapi32(delete_ok=False, error=secrets.ERROR_NOT_FOUND))
        assert secrets.delete_secret("gone") is True

    def test_delete_failure_is_reported(self, monkeypatch):
        self._use(monkeypatch, _FakeAdvapi32(delete_ok=False, error=5))
        assert secrets.delete_secret("key") is False


class TestFailurePaths:
    """平台层契约：失败只记日志，绝不把异常抛给调用方。"""

    def test_macos_exception_is_swallowed(self, monkeypatch):
        _patch_backend(monkeypatch, "macos")

        def _boom(*args, **kwargs):
            raise RuntimeError("keychain 炸了")

        monkeypatch.setattr(secrets, "_macos_get", _boom)
        monkeypatch.setattr(secrets, "_macos_set", _boom)
        monkeypatch.setattr(secrets, "_macos_delete", _boom)
        assert secrets.get_secret("key") == ""
        assert secrets.set_secret("key", "value") is False
        assert secrets.delete_secret("key") is False

    def test_platform_without_backend_reads_empty(self, monkeypatch):
        _patch_backend(monkeypatch, "linux")
        assert secrets.get_secret("key") == ""
        assert secrets.set_secret("key", "value") is False
        assert secrets.delete_secret("key") is False
