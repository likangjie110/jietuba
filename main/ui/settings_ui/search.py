# -*- coding: utf-8 -*-
"""设置搜索：按关键字找到设置项，并指出它在哪一页。

索引的原料是**设置卡片的标题与说明**，加上页面名本身——不是手抄一张「可搜项清单」：
手抄的清单会随着页面改动而腐烂，而卡片的文案本来就要写清楚「这一项是干什么的」，
拿它当索引既有信息量又不需要维护。

搜索规则（纯函数，可穷举）：
- 关键字按空白切成词，**每个词都要命中**（AND）：多打一个词只会收窄结果，不会翻车；
- 大小写不敏感；命中标题或说明任一处即可；
- 空关键字返回空表（不是全量）——界面上的空搜索框不该瞬间弹出一整屏结果。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.logger import T, log_debug

#: 单次最多返回多少条（再多也没人往下翻）
MAX_RESULTS = 30


@dataclass(frozen=True)
class SearchEntry:
    """一个可搜索的设置项：页面索引、页面名、卡片标题、说明。"""

    page_index: int
    page_name: str
    title: str
    description: str = ""

    def haystack(self) -> str:
        return f"{self.page_name} {self.title} {self.description}".casefold()


@dataclass(frozen=True)
class SearchHit:
    entry: SearchEntry
    score: int


def build_entries(page_names, cards) -> list:
    """把「每页的卡片」拼成索引条目。

    ``page_names`` 是 ``{页下标: 页名}``，``cards`` 是 ``[(页下标, 标题, 说明), ...]``。
    """
    entries = []
    for page_index, title, description in cards:
        entries.append(SearchEntry(
            page_index=int(page_index),
            page_name=str(page_names.get(int(page_index), "") or ""),
            title=str(title or ""),
            description=str(description or ""),
        ))
    return entries


def search(entries, query: str, *, limit: int = MAX_RESULTS) -> list:
    """按关键字搜索；返回按相关度排序的命中（无命中就是空表）。"""
    terms = [term for term in str(query or "").casefold().split() if term]
    if not terms:
        return []

    hits = []
    for entry in entries:
        haystack = entry.haystack()
        if not all(term in haystack for term in terms):
            continue
        title = entry.title.casefold()
        # 标题命中比说明命中更相关；整词前缀再高一点，用来把「语言」排在「界面语言」前
        score = 0
        for term in terms:
            if term in title:
                score += 3
                if title.startswith(term):
                    score += 2
            if term in entry.page_name.casefold():
                score += 1
        hits.append(SearchHit(entry, score))

    hits.sort(key=lambda hit: (-hit.score, hit.entry.page_index, hit.entry.title))
    limited = hits[:max(1, int(limit))]
    log_debug(T("设置搜索: {query} → {count} 条", query=query, count=len(limited)),
              "SettingsSearch")
    return limited


def index_from_widgets(page_names, pages) -> list:
    """从真实的设置页部件里抽出索引（找 ``SettingCard`` 的标题与说明）。

    按类型名判断而不是 import 具体类：这层只关心「有标题和说明的卡片」，
    组件库将来换个类名不该让搜索失效。
    """
    cards = []
    for page_index, page in enumerate(pages):
        for widget in _iter_widgets(page):
            title = _card_text(widget, "titleLabel")
            if not title:
                continue
            cards.append((page_index, title, _card_text(widget, "contentLabel")))
    return build_entries(page_names, cards)


def _iter_widgets(root):
    stack = [root]
    while stack:
        widget = stack.pop()
        if widget is None:
            continue
        yield widget
        try:
            stack.extend(widget.children())
        except Exception:
            continue


def _card_text(widget, attribute: str) -> str:
    label = getattr(widget, attribute, None)
    if label is None:
        return ""
    for getter in ("text",):
        method = getattr(label, getter, None)
        if callable(method):
            try:
                return str(method() or "").strip()
            except Exception:
                return ""
    return ""
