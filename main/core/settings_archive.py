# -*- coding: utf-8 -*-
"""设置导出/导入：把整份 QSettings 打包成 zip，导入失败时**回滚到导入前**。

打包内容是 `settings.json`（键值全量）+ `manifest.json`（版本、时间、键数、来源）。
导入的语义是「整体替换」而不是「合并」：用户的期望是「把我备份的那份还回来」，合并会
留下备份里没有、现状有的键，事后没人说得清哪一项是从哪来的。

回滚是这份实现的关键：先快照现状 → 清空并写入新值 → 任何一步抛错就把快照写回去。
不这么做的话，一个写了一半的坏包会把用户配置变成半新半旧的混合体。
"""

from __future__ import annotations

import json
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from core.logger import T, log_debug, log_exception, log_warning

#: 包内文件名
SETTINGS_NAME = "settings.json"
MANIFEST_NAME = "manifest.json"

#: 当前的包格式版本（将来改结构时用它判断能不能读）
ARCHIVE_SCHEMA = 1

#: 包里最多接受多少个键（防御损坏/恶意包）
MAX_KEYS = 20000


@dataclass
class ArchiveResult:
    """导出/导入的结果：成功与否 + 一条能直接显示给用户的说明。"""

    ok: bool
    message: str = ""
    keys: int = 0
    error: str = ""
    details: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


def snapshot_settings(config_manager) -> dict:
    """把当前所有键值拷一份出来（回滚用）。"""
    settings = getattr(config_manager, "qsettings", None)
    if settings is None:
        return {}
    return {key: settings.value(key) for key in settings.allKeys()}


def restore_settings(config_manager, snapshot: dict) -> bool:
    """用快照整体替换当前配置（先清空再写，避免残留快照里没有的键）。"""
    settings = getattr(config_manager, "qsettings", None)
    if settings is None:
        return False
    try:
        settings.clear()
        for key, value in (snapshot or {}).items():
            settings.setValue(key, value)
        settings.sync()
        return True
    except Exception as e:
        log_exception(e, T("恢复设置快照"))
        return False


def export_settings(config_manager, target_path: str) -> ArchiveResult:
    """把当前设置导出成 zip。"""
    settings = getattr(config_manager, "qsettings", None)
    if settings is None:
        return ArchiveResult(False, error=T("没有可导出的配置").render())

    values = {}
    for key in settings.allKeys():
        value = settings.value(key)
        values[key] = _jsonable(value)

    manifest = {
        "schema": ARCHIVE_SCHEMA,
        "exported_at": time.time(),
        "exported_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
        "keys": len(values),
    }
    try:
        path = Path(target_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(SETTINGS_NAME,
                             json.dumps(values, ensure_ascii=False, indent=1))
            archive.writestr(MANIFEST_NAME,
                             json.dumps(manifest, ensure_ascii=False, indent=1))
    except Exception as e:
        log_exception(e, T("导出设置"))
        return ArchiveResult(False, error=T("导出设置失败: {e}", e=e).render())

    log_debug(T("设置已导出: {path} ({count} 个键)", path=str(path), count=len(values)),
              "SettingsArchive")
    return ArchiveResult(True, keys=len(values), details=manifest,
                         message=T("已导出 {count} 个设置项", count=len(values)).render())


def read_archive(path: str) -> ArchiveResult:
    """只读包内容（不改配置）：给导入做校验用。"""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = set(archive.namelist())
            if SETTINGS_NAME not in names:
                return ArchiveResult(False, error=T("压缩包里没有 {name}",
                                                    name=SETTINGS_NAME).render())
            raw = json.loads(archive.read(SETTINGS_NAME).decode("utf-8"))
            manifest = {}
            if MANIFEST_NAME in names:
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
    except zipfile.BadZipFile:
        return ArchiveResult(False, error=T("这不是一个有效的设置压缩包").render())
    except Exception as e:
        log_exception(e, T("读取设置压缩包"))
        return ArchiveResult(False, error=T("读取设置压缩包失败: {e}", e=e).render())

    if not isinstance(raw, dict):
        return ArchiveResult(False, error=T("设置内容格式不对").render())
    if len(raw) > MAX_KEYS:
        return ArchiveResult(False, error=T("设置项过多，已拒绝导入").render())
    schema = int(manifest.get("schema", ARCHIVE_SCHEMA) or ARCHIVE_SCHEMA)
    if schema > ARCHIVE_SCHEMA:
        return ArchiveResult(False, error=T("压缩包来自更新的版本，无法导入").render())
    return ArchiveResult(True, keys=len(raw), details={"manifest": manifest, "values": raw})


def import_settings(config_manager, path: str) -> ArchiveResult:
    """导入设置；任一步失败都恢复到导入前的状态。"""
    read = read_archive(path)
    if not read:
        return read

    values = read.details.get("values") or {}
    before = snapshot_settings(config_manager)
    try:
        settings = config_manager.qsettings
        settings.clear()
        for key, value in values.items():
            settings.setValue(key, value)
        settings.sync()
    except Exception as e:
        log_exception(e, T("导入设置"))
        restored = restore_settings(config_manager, before)
        if not restored:
            log_warning(T("导入失败且回滚失败，请手动检查配置"), "SettingsArchive")
        return ArchiveResult(
            False,
            error=T("导入失败，已回滚到导入前: {e}", e=e).render() if restored
            else T("导入失败且回滚失败，请手动检查配置").render())

    # 工具设置被读进内存后不会自己回看存储，必须显式重载（否则「导入成功但值没变」）
    reload_hook = getattr(config_manager, "reload_from_storage", None)
    if callable(reload_hook):
        try:
            reload_hook()
        except Exception as e:
            log_exception(e, T("重新加载工具设置"))

    log_debug(T("设置已导入: {path} ({count} 个键)", path=str(path), count=len(values)),
              "SettingsArchive")
    return ArchiveResult(True, keys=len(values),
                         message=T("已导入 {count} 个设置项", count=len(values)).render())


def _jsonable(value):
    """把 QSettings 取出来的值转成能进 JSON 的类型（QByteArray 之类原样存字符串）。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    try:
        return str(value)
    except Exception:
        return ""
