# -*- coding: utf-8 -*-
"""标注样式模板：把当前工具的一套参数存下来，之后一键套到新元素或选中元素上。

存的是什么：**工具设置里那套键**（颜色/粗细/线型/箭头外观/水印参数/滤镜参数…）。所以
模板与工具设置共用同一份键名，套用时既写设置（影响之后画的）也可以写选中元素（影响
当前这一条）——不需要为模板另立一套参数模型。

存在 ``app/annotation_templates``：``{名字: {tool_id: {键: 值}}}`` 的 JSON 字符串。
值一律是标量（颜色存 ``#RRGGBB``），方便直接落进 QSettings。
"""

from __future__ import annotations

import json

from PySide6.QtGui import QColor

from core.logger import T, log_debug, log_exception, log_warning

#: 配置键（app/ 前缀）
SETTING_KEY = "annotation_templates"

#: 模板名字长度上限（界面上一行放得下）
MAX_NAME_LENGTH = 24
#: 模板数量上限：模板是给人记的，几十个就够，太多反而找不到
MAX_TEMPLATES = 32


def normalize_name(name: str) -> str:
    """模板名：去首尾空白并截断；空名返回空串（调用方拒绝保存）。"""
    return str(name or "").strip()[:MAX_NAME_LENGTH]


def load_templates(config_manager) -> dict:
    """读回模板表；坏数据回退成空表（一条坏 JSON 不该让整个功能不可用）。"""
    if config_manager is None:
        return {}
    try:
        raw = config_manager.get_app_setting(SETTING_KEY, "")
    except Exception as e:
        log_exception(e, T("读取标注样式模板"))
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        log_warning(T("标注样式模板格式不对，按空表处理"), "AnnotationTemplate")
        return {}
    if not isinstance(data, dict):
        return {}
    templates = {}
    for name, tools in data.items():
        if not isinstance(tools, dict):
            continue
        cleaned = {}
        for tool_id, values in tools.items():
            if isinstance(values, dict):
                cleaned[str(tool_id)] = dict(values)
        templates[normalize_name(name)] = cleaned
    return templates


def save_templates(config_manager, templates: dict) -> bool:
    """写回模板表（只保留最近的 ``MAX_TEMPLATES`` 个）。"""
    if config_manager is None:
        return False
    trimmed = dict(list(templates.items())[-MAX_TEMPLATES:])
    try:
        config_manager.set_app_setting(
            SETTING_KEY, json.dumps(trimmed, ensure_ascii=False, sort_keys=True))
        return True
    except Exception as e:
        log_exception(e, T("保存标注样式模板"))
        return False


def capture_from_manager(config_manager, tool_id: str, keys=None) -> dict:
    """从工具设置里取一套参数作为模板内容（``keys`` 给定时只取这些键）。"""
    if config_manager is None:
        return {}
    try:
        settings = config_manager.get_tool_settings(tool_id)
        values = settings.to_dict() if hasattr(settings, "to_dict") else dict(settings or {})
    except Exception as e:
        log_exception(e, T("读取工具设置"))
        return {}
    if keys is None:
        return _serializable(values)
    return _serializable({key: values[key] for key in keys if key in values})


def _serializable(values: dict) -> dict:
    """把值收敛成能进 JSON 的标量：颜色存 #RRGGBB，其余原样（非标量丢掉）。"""
    out = {}
    for key, value in values.items():
        if isinstance(value, QColor):
            out[key] = value.name()
        elif isinstance(value, (int, float, str, bool)) or value is None:
            out[key] = value
    return out


def apply_to_manager(config_manager, tool_id: str, values: dict) -> int:
    """把模板里的参数写回工具设置（影响之后画出来的元素），返回写入的键数。"""
    if config_manager is None or not values:
        return 0
    try:
        config_manager.update_settings(tool_id, **values)
        return len(values)
    except Exception as e:
        log_exception(e, T("套用标注样式模板"))
        return 0


def apply_to_item(item, tool_id: str, values: dict) -> int:
    """把模板里的参数套到已有元素上（能认的键才写），返回改动的键数。

    只处理元素自己暴露的 setter：''set_stroke_width''/''set_visual_opacity''/''arrow_state''/
    水印的 ``apply_state``。元素没这个能力就跳过——模板不该因为元素类型不同而报错。
    """
    if item is None or not values:
        return 0
    changed = 0
    strokes = {"stroke_width": "set_stroke_width", "opacity": "set_visual_opacity"}
    for key, setter in strokes.items():
        if key in values:
            method = getattr(item, setter, None)
            if callable(method):
                try:
                    method(float(values[key]))
                    changed += 1
                except Exception as e:
                    log_exception(e, T("套用模板参数"))
    if "color" in values:
        method = getattr(item, "set_color", None)
        if callable(method):
            try:
                method(QColor(values["color"]))
                changed += 1
            except Exception as e:
                log_exception(e, T("套用模板颜色"))
    if tool_id == "arrow" and hasattr(item, "apply_arrow_state"):
        state = {key: values[key] for key in ("path_style", "head_start", "head_end",
                                              "arrow_style") if key in values}
        if state:
            try:
                item.apply_arrow_state(state)
                changed += len(state)
            except Exception as e:
                log_exception(e, T("套用模板箭头样式"))
    if tool_id == "watermark" and hasattr(item, "apply_state"):
        state = {key: values[key] for key in ("text", "font_size", "angle", "gap",
                                              "opacity", "color") if key in values}
        if state:
            try:
                item.apply_state(state)
                changed += len(state)
            except Exception as e:
                log_exception(e, T("套用水印模板"))
    return changed


def save_template(config_manager, name: str, tool_id: str, values: dict) -> dict:
    """存一个模板（同名覆盖），返回更新后的模板表。"""
    template_name = normalize_name(name)
    if not template_name or not values:
        return load_templates(config_manager)
    templates = load_templates(config_manager)
    templates[template_name] = {tool_id: _serializable(values)}
    save_templates(config_manager, templates)
    log_debug(T("已保存标注样式模板: {name}", name=template_name), "AnnotationTemplate")
    return templates


def delete_template(config_manager, name: str) -> dict:
    """删一个模板，返回更新后的模板表。"""
    templates = load_templates(config_manager)
    templates.pop(normalize_name(name), None)
    save_templates(config_manager, templates)
    return templates
