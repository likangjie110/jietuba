# -*- coding: utf-8 -*-
"""识别质量的判断：本地 OCR 置信度偏低时，如实告诉用户可以改用视觉模型。

引擎的约定是每条结果带一个 0~1 的 ``score``（给不出置信度的引擎填 1.0，见
``ocr/engine.py``），所以"这次识别靠不靠谱"是算得出来的。这里只做两件纯事：

- ``average_confidence``：按字符数加权的平均置信度。按字符而不是按行加权，是因为
  一行 3 个字的误识别和一行 40 个字的误识别对结果的影响差着一个量级。
- ``low_confidence_hint``：低于阈值时给一句人能照做的话。

**这里绝不发网络请求**：提示只是提示，是否改用视觉模型由用户自己决定（视觉模型要花用户
自己的额度，静默上传截图是绝对不能做的事）。所以 ``vision_ready`` 只用来决定提示里是
"可以改用视觉模型"还是"可以先在设置里配一个视觉模型"。
"""

from typing import Any

from core.logger import T

#: 默认阈值：0.6 是「多数行识别得不踏实」的量级，再高会把正常结果也判成低置信度。
DEFAULT_THRESHOLD = 0.6


def average_confidence(result: Any) -> float:
    """结果的加权平均置信度；没有可用数据时返回 1.0（不误报低置信度）。"""
    rows = _rows(result)
    if not rows:
        return 1.0
    total_weight = 0
    total = 0.0
    for row in rows:
        score = _score(row)
        if score is None:
            continue
        weight = max(1, len(str(row.get("text", "") or "")))
        total += score * weight
        total_weight += weight
    if not total_weight:
        return 1.0
    return total / total_weight


def low_confidence_hint(result: Any, *, threshold: float = DEFAULT_THRESHOLD,
                        vision_ready: bool = False) -> str:
    """低于阈值时返回提示文本，否则返回空串。

    ``vision_ready`` 是「已经配好可用的视觉模型」——配置读取失败时按 False 处理，
    提示里就会让用户先去设置里配，而不是指一条走不通的路。
    """
    score = average_confidence(result)
    if score >= _threshold(threshold):
        return ""
    percent = int(round(score * 100))
    if vision_ready:
        return T("识别置信度偏低（约 {percent}%），可改用已配置的视觉模型再读一次",
                 percent=percent).render()
    return T("识别置信度偏低（约 {percent}%），可在设置里配置视觉模型后改用它再读一次",
             percent=percent).render()


def _threshold(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    if number <= 0 or number > 1:
        return DEFAULT_THRESHOLD
    return number


def _rows(result: Any) -> list:
    """兼容三种结果形态：``{"data": [...]}``、扁平 list、None。"""
    if isinstance(result, dict):
        data = result.get("data")
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    return []


def _score(row: dict) -> float | None:
    raw = row.get("score")
    if raw is None:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return min(1.0, number)
