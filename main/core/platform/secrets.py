# -*- coding: utf-8 -*-
"""凭据存储：把 API key 交给系统密钥库保管，不落进 QSettings。

翻译 provider（DeepL / Google / Azure / Amazon）、外部公式服务与视觉模型都要用户填
API key。这些 key 原先和普通设置一样写进 QSettings——macOS 上就是
``~/Library/Preferences/*.plist``、Windows 上就是注册表 HKCU\\Software，两边都是**明文**，
任何读得到用户配置目录的进程、备份与同步服务都能直接拿到。这里改成向系统要一个安全容器：

- macOS：Keychain 的 generic password（Security.framework，service = ``SERVICE_NAME``）；
- Windows：凭据管理器（advapi32 的 ``CredReadW``/``CredWriteW``/``CredDeleteW``，
  generic credential，persist = local machine）；
- Linux：没有内置等价物（要 libsecret 依赖），能力登记 NONE，调用方按可用性回退。

调用方的约定和平台层其它门面一致：**失败只记日志并返回安全值，绝不抛异常**——这里的
调用点都是「读一个可能还不存在的 key」，不该把异常抛进设置页或翻译请求路径。

``is_available()`` 是回退的唯一开关：为 False 时调用方必须按「这台机器没有密钥库」处理
（旧实现是把 key 明文写进 QSettings），而不是当作「key 是空的」。
"""

import ctypes

from core.platform.detection import IS_MACOS, IS_WINDOWS

#: macOS Keychain 里承载这些条目的 service 名（应用标识）。
SERVICE_NAME = "com.jietuba.app"

#: Windows 凭据管理器里的 target 前缀，避免和别的应用撞名。
WINDOWS_TARGET_PREFIX = "jietuba/"

#: Security.framework 的 errSecItemNotFound（「这个凭据还没存过」）。
ERR_SEC_ITEM_NOT_FOUND = -25300

#: CredReadW 在「没有这个凭据」时的 GetLastError 值。
ERROR_NOT_FOUND = 1168

#: 凭据管理器里 generic credential 的类型与持久化范围。
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


def is_available() -> bool:
    """当前平台有没有可用的系统密钥库。"""
    from core.platform.capabilities import Capability, available

    return available(Capability.SECRET_STORE)


def backend_name() -> str:
    """后端名字（日志与设置页说明用）；没有后端时返回 "native"。"""
    from core.platform.capabilities import Capability, backend_name as _backend_name

    return _backend_name(Capability.SECRET_STORE)


def get_secret(name: str) -> str:
    """读一个凭据；不存在或读失败都返回空串（两种情况调用方处理方式相同：没填 key）。"""
    if not name:
        return ""
    try:
        if IS_MACOS:
            return _macos_get(name)
        if IS_WINDOWS:
            return _windows_get(name)
    except Exception as e:
        from core.logger import T, log_exception

        log_exception(e, T("从系统密钥库读取 {name}", name=name))
    return ""


def set_secret(name: str, value: str) -> bool:
    """写入一个凭据，成功返回 True。

    空值等价于删除：设置页把输入框清空、点保存，用户的意图是「不要再存这个 key」。
    """
    if not name:
        return False
    if not value:
        return delete_secret(name)
    try:
        if IS_MACOS:
            return _macos_set(name, value)
        if IS_WINDOWS:
            return _windows_set(name, value)
    except Exception as e:
        from core.logger import T, log_exception

        log_exception(e, T("写入系统密钥库 {name}", name=name))
    return False


def delete_secret(name: str) -> bool:
    """删除一个凭据；本来就不存在也算成功（幂等）。"""
    if not name:
        return False
    try:
        if IS_MACOS:
            return _macos_delete(name)
        if IS_WINDOWS:
            return _windows_delete(name)
    except Exception as e:
        from core.logger import T, log_exception

        log_exception(e, T("删除系统密钥库条目 {name}", name=name))
    return False


def _decode(raw) -> str:
    """Keychain / 凭据管理器返回的是字节，统一按 UTF-8 解回来。"""
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""
    return bytes(raw).decode("utf-8", errors="replace")


# ── macOS：Keychain ──────────────────────────────────────────
# pyobjc 的 Security 绑定要求 service / account / password 都是 byte-buffer，
# 长度单独传，所以每个参数都要一份 bytes 和它的长度。


def _macos_get(name: str) -> str:
    import Security

    service = SERVICE_NAME.encode("utf-8")
    account = name.encode("utf-8")
    status, _length, data, _item = Security.SecKeychainFindGenericPassword(
        None, len(service), service, len(account), account, None, None, None
    )
    if status == ERR_SEC_ITEM_NOT_FOUND:
        return ""
    if status != 0:
        from core.logger import T, log_warning

        log_warning(T("读取系统密钥库失败（{status}）: {name}", status=status, name=name), "Secrets")
        return ""
    return _decode(data)


def _macos_set(name: str, value: str) -> bool:
    import Security

    service = SERVICE_NAME.encode("utf-8")
    account = name.encode("utf-8")
    payload = value.encode("utf-8")
    # 先删再加，而不是「找到就改」：pyobjc 的这个版本没有导出
    # SecKeychainItemModifyContent，重复 add 会返回 errSecDuplicateItem（-25299）。
    # 删掉不存在的条目是 no-op，所以这条路径对「首次写入」同样成立。
    _macos_delete(name)
    status, _item = Security.SecKeychainAddGenericPassword(
        None, len(service), service, len(account), account, len(payload), payload, None
    )
    if status != 0:
        from core.logger import T, log_warning

        log_warning(T("写入系统密钥库失败（{status}）: {name}", status=status, name=name), "Secrets")
        return False
    return True


def _macos_delete(name: str) -> bool:
    import Security

    service = SERVICE_NAME.encode("utf-8")
    account = name.encode("utf-8")
    status, _length, _data, item = Security.SecKeychainFindGenericPassword(
        None, len(service), service, len(account), account, None, None, None
    )
    if status == ERR_SEC_ITEM_NOT_FOUND:
        return True
    if status != 0:
        from core.logger import T, log_warning

        log_warning(T("删除系统密钥库条目失败（{status}）: {name}", status=status, name=name), "Secrets")
        return False
    if item is None:
        return True
    return Security.SecKeychainItemDelete(item) == 0


# ── Windows：凭据管理器 ──────────────────────────────────────
# 本机是 macOS，这一支只做过「结构正确性」检查（假 DLL 断言调用参数），
# 没有在 Windows 实机上验证过。


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong),
                ("dwHighDateTime", ctypes.c_ulong)]


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", _FileTime),
        ("CredentialBlobSize", ctypes.c_ulong),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", ctypes.c_ulong),
        ("AttributeCount", ctypes.c_ulong),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


def _windows_api():
    """加载 advapi32 并声明原型（use_last_error 让 GetLastError 拿得到真错误码）。

    测试里替换这个函数就能用假 DLL 驱动上面三个函数——原型声明在真实 DLL 上是必须的，
    而假对象没有函数对象可挂 ``argtypes``。
    """
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _declare_windows_prototypes(advapi32)
    return advapi32


def _declare_windows_prototypes(advapi32) -> None:
    advapi32.CredReadW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.POINTER(ctypes.POINTER(_CredentialW)),
    ]
    advapi32.CredReadW.restype = ctypes.c_bool
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CredentialW), ctypes.c_ulong]
    advapi32.CredWriteW.restype = ctypes.c_bool
    advapi32.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
    advapi32.CredDeleteW.restype = ctypes.c_bool
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    return advapi32


def _windows_target(name: str) -> str:
    return f"{WINDOWS_TARGET_PREFIX}{name}"


def _last_error() -> int:
    """最近一次 Win32 调用的错误码。

    ``ctypes.get_last_error`` 只在 Windows 上存在，所以这里得问一句再用——否则非 Windows
    上跑 Windows 分支（假后端驱动的测试）会抛 AttributeError，把「不该失败的分支」变成
    静默返回空值。
    """
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


def _windows_get(name: str) -> str:
    advapi32 = _windows_api()
    pointer = ctypes.POINTER(_CredentialW)()
    ok = advapi32.CredReadW(_windows_target(name), CRED_TYPE_GENERIC, 0, ctypes.byref(pointer))
    if not ok:
        error = _last_error()
        if error == ERROR_NOT_FOUND:
            return ""
        from core.logger import T, log_warning

        log_warning(T("读取系统密钥库失败（{status}）: {name}", status=error, name=name), "Secrets")
        return ""
    try:
        credential = pointer.contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return _decode(blob)
    finally:
        advapi32.CredFree(pointer)


def _windows_set(name: str, value: str) -> bool:
    advapi32 = _windows_api()
    payload = value.encode("utf-8")
    # CredWriteW 只拿指针，不回拷内容：buffer 必须活到调用结束（下面这行是刻意的）
    buffer = ctypes.create_string_buffer(payload, len(payload))
    credential = _CredentialW()
    credential.Flags = 0
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = _windows_target(name)
    credential.Comment = None
    credential.CredentialBlobSize = len(payload)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.AttributeCount = 0
    credential.Attributes = None
    credential.TargetAlias = None
    credential.UserName = "jietuba"
    ok = advapi32.CredWriteW(ctypes.byref(credential), 0)
    if not ok:
        from core.logger import T, log_warning

        log_warning(
            T("写入系统密钥库失败（{status}）: {name}",
              status=_last_error(), name=name),
            "Secrets",
        )
        return False
    return True


def _windows_delete(name: str) -> bool:
    advapi32 = _windows_api()
    ok = advapi32.CredDeleteW(_windows_target(name), CRED_TYPE_GENERIC, 0)
    if not ok:
        error = _last_error()
        if error == ERROR_NOT_FOUND:
            return True
        from core.logger import T, log_warning

        log_warning(T("删除系统密钥库条目失败（{status}）: {name}", status=error, name=name), "Secrets")
        return False
    return True
