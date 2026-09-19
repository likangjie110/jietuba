# -*- coding: utf-8 -*-
"""截图历史的存储：一份索引 + 每条一个图片文件。

存的是**用户确认过的截图结果**（导出后的图，含标注），不是屏幕快照。索引是
``<root>/index.json``，图片是 ``<root>/<id>.png``——分成两份是为了「列表要快、图要能单独删」：
读列表只读索引，缩略/预览才去碰图片。

保留策略的**决策**是纯函数（``plan_eviction``）：什么时候淘汰哪几条与文件系统无关，
所以能直接穷举测。真正删文件在这里（``apply_retention``），删之前先删索引再删文件，
中途失败也只是留下一个孤立文件，不会留下「索引里有、文件没了」的坏条目。
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtGui import QImage

from core.logger import T, log_debug, log_exception, log_warning
from core.platform.paths import app_data_dir

#: 历史目录名（应用数据目录下）
HISTORY_DIR_NAME = "history"

#: 来源取值：窗口 / 窗口里的元素 / 整块显示器 / 手拉选区
SOURCES = ("window", "element", "monitor", "region")

#: 图片文件名后缀（PNG：无损，截图历史要能再导出）
IMAGE_SUFFIX = ".png"


@dataclass(frozen=True)
class HistoryEntry:
    """一条截图历史。``created_at`` 是 epoch 秒。"""

    id: str
    created_at: float
    path: str
    width: int
    height: int
    size_bytes: int
    source: str = "region"
    label: str = ""

    def exists(self) -> bool:
        return bool(self.path) and os.path.exists(self.path)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "HistoryEntry | None":
        """从索引里的一条记录还原；缺字段或值坏掉的记录丢掉（不让一条坏数据毁掉整份索引）。"""
        try:
            return cls(
                id=str(raw["id"]),
                created_at=float(raw["created_at"]),
                path=str(raw["path"]),
                width=int(raw.get("width", 0)),
                height=int(raw.get("height", 0)),
                size_bytes=int(raw.get("size_bytes", 0)),
                source=str(raw.get("source", "region") or "region"),
                label=str(raw.get("label", "") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class RetentionPolicy:
    """保留策略：三个上限都是「0 = 不限制」，可以同时生效。"""

    retention_days: int = 0
    max_entries: int = 0
    max_disk_bytes: int = 0
    #: 单条明细（哪条为什么被淘汰），plan_eviction 填充，供日志与测试用
    reasons: dict = field(default_factory=dict)


def plan_eviction(entries, *, now: float, retention_days: int = 0, max_entries: int = 0,
                  max_disk_bytes: int = 0) -> tuple:
    """算出该删哪些条目，返回 ``(要删的 id 元组, 每个 id 的原因)``。

    规则按「先过期、再超额、最后按磁盘上限从最旧的删」的顺序，同一个 id 只记第一次
    触发的原因。``entries`` 顺序无所谓：这里按时间从新到旧自己排。
    """
    ordered = sorted(entries, key=lambda item: item.created_at, reverse=True)
    doomed: dict = {}

    if retention_days and retention_days > 0:
        cutoff = now - retention_days * 86400
        for entry in ordered:
            if entry.created_at < cutoff:
                doomed[entry.id] = "expired"

    if max_entries and max_entries > 0:
        for entry in ordered[max_entries:]:
            doomed.setdefault(entry.id, "over_count")

    if max_disk_bytes and max_disk_bytes > 0:
        kept_bytes = sum(e.size_bytes for e in ordered if e.id not in doomed)
        # 从最旧的开始丢，直到剩下的低于上限
        for entry in reversed(ordered):
            if kept_bytes <= max_disk_bytes:
                break
            if entry.id in doomed:
                continue
            doomed[entry.id] = "over_disk"
            kept_bytes -= entry.size_bytes

    return tuple(doomed), doomed


class HistoryStore:
    """截图历史的读写。所有方法都在主线程用；写索引是整体重写（条目数是千级，够用）。"""

    def __init__(self, root: str | os.PathLike | None = None):
        self._root = Path(root) if root is not None else app_data_dir() / HISTORY_DIR_NAME
        self._index_path = self._root / "index.json"
        self._entries: list[HistoryEntry] = []
        self.load()

    # ── 路径与索引 ──

    @property
    def root(self) -> Path:
        return self._root

    def load(self) -> int:
        """从磁盘读回索引；文件缺失/坏掉时当空历史（记一条日志，不抛）。"""
        self._entries = []
        if not self._index_path.exists():
            return 0
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        except Exception as e:
            log_exception(e, T("读取截图历史索引"))
            return 0
        if not isinstance(raw, list):
            log_warning(T("截图历史索引格式不对，按空历史处理"), "History")
            return 0
        for item in raw:
            entry = HistoryEntry.from_dict(item) if isinstance(item, dict) else None
            if entry is not None:
                self._entries.append(entry)
        self._entries.sort(key=lambda item: item.created_at, reverse=True)
        return len(self._entries)

    def _save(self) -> bool:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            tmp = self._index_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps([e.to_dict() for e in self._entries], ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            os.replace(tmp, self._index_path)
            return True
        except Exception as e:
            log_exception(e, T("写入截图历史索引"))
            return False

    # ── 查询 ──

    def entries(self) -> list:
        """按时间从新到旧返回全部条目。"""
        return list(self._entries)

    def get(self, entry_id: str) -> HistoryEntry | None:
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        return None

    def filtered(self, *, source: str = "", start: float = 0.0, end: float = 0.0) -> list:
        """按来源与时间范围筛选；``start``/``end`` 是 epoch 秒，0 表示这一端不限。"""
        result = []
        for entry in self._entries:
            if source and entry.source != source:
                continue
            if start and entry.created_at < start:
                continue
            if end and entry.created_at > end:
                continue
            result.append(entry)
        return result

    def total_bytes(self) -> int:
        return sum(entry.size_bytes for entry in self._entries)

    def image(self, entry_id: str) -> QImage | None:
        """读回某条目的图片；文件没了返回 None（调用方按「已失效」处理）。"""
        entry = self.get(entry_id)
        if entry is None or not entry.exists():
            return None
        image = QImage(entry.path)
        return None if image.isNull() else image

    # ── 写入 ──

    def add(self, image: QImage, *, source: str = "region", label: str = "",
            created_at: float | None = None) -> HistoryEntry | None:
        """把一张图存进历史；写不进去时返回 None 并记日志。"""
        if image is None or image.isNull():
            return None
        source = source if source in SOURCES else "region"
        stamp = float(created_at if created_at is not None else time.time())
        entry_id = f"{time.strftime('%Y%m%d_%H%M%S', time.localtime(stamp))}_{uuid.uuid4().hex[:8]}"
        path = self._root / f"{entry_id}{IMAGE_SUFFIX}"
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            if not image.save(str(path), "PNG"):
                log_warning(T("截图历史写入图片失败: {path}", path=str(path)), "History")
                return None
            size_bytes = os.path.getsize(path)
        except Exception as e:
            log_exception(e, T("截图历史写入图片"))
            return None

        entry = HistoryEntry(
            id=entry_id, created_at=stamp, path=str(path),
            width=image.width(), height=image.height(), size_bytes=size_bytes,
            source=source, label=str(label or ""),
        )
        self._entries.insert(0, entry)
        self._save()
        log_debug(T("截图历史新增: {entry_id} ({source}, {width}x{height}, {size} 字节)",
                    entry_id=entry_id, source=source, width=entry.width, height=entry.height,
                    size=size_bytes), "History")
        return entry

    # ── 删除与保留 ──

    def remove(self, entry_id: str, *, delete_file: bool = True) -> bool:
        """删一条：先摘索引，再删文件（顺序反过来的话，删文件成功但索引写失败就留下坏条目）。"""
        entry = self.get(entry_id)
        if entry is None:
            return False
        self._entries = [e for e in self._entries if e.id != entry_id]
        self._save()
        if delete_file:
            try:
                os.remove(entry.path)
            except FileNotFoundError:
                pass
            except Exception as e:
                log_exception(e, T("删除截图历史文件"))
        return True

    def apply_retention(self, *, retention_days: int = 0, max_entries: int = 0,
                        max_disk_bytes: int = 0, now: float | None = None) -> int:
        """按策略淘汰旧条目，返回删掉的条数（磁盘占用随之下降）。"""
        doomed, reasons = plan_eviction(
            self._entries, now=float(now if now is not None else time.time()),
            retention_days=retention_days, max_entries=max_entries,
            max_disk_bytes=max_disk_bytes,
        )
        if not doomed:
            return 0
        for entry_id in doomed:
            self.remove(entry_id)
        log_debug(T("截图历史保留策略: 淘汰 {count} 条 ({reasons})",
                    count=len(doomed),
                    reasons=", ".join(sorted({reasons[i] for i in doomed}))), "History")
        return len(doomed)

    def clear(self) -> int:
        """清空历史（含文件）；返回清掉的条数。"""
        count = len(self._entries)
        try:
            self._entries = []
            self._save()
            shutil.rmtree(self._root, ignore_errors=True)
        except Exception as e:
            log_exception(e, T("清空截图历史"))
        return count
