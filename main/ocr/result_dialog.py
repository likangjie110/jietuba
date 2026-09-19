# -*- coding: utf-8 -*-
"""识别结果对话框：按设置决定「什么时候」把识别到的文字弹出来。

设置项 `app/ocr_dialog_triggers` 是三个时机的子集（取值见
`settings.tool_settings.ToolSettingsManager.OCR_DIALOG_TRIGGERS`）：

- ``capture``：截图/钉图后的自动识别完成时
- ``copy_selection``：复制**选定**的文本时（钉图文字层里框选后复制）
- ``copy_all``：复制**所有**文本时（截图并复制文本、复制表格为 Markdown）

默认空表 = 沿用老行为：只复制到剪贴板，什么都不弹。弹窗只是「看一眼再走」的补充，
关闭它不影响复制本身。
"""
from typing import List

from core.logger import T, log_debug, log_exception


def dialog_triggers() -> List[str]:
    """当前配置里允许弹窗的时机；配置读不出来时按「不弹」处理。"""
    try:
        from settings import get_tool_settings_manager

        return list(get_tool_settings_manager().get_ocr_dialog_triggers())
    except Exception as e:
        log_exception(e, T("读取识别结果对话框时机"))
        return []


def should_show(trigger: str) -> bool:
    """这个时机该不该弹识别结果窗。"""
    return trigger in dialog_triggers()


def maybe_show_ocr_result(text: str, trigger: str, hint: str = "") -> bool:
    """命中设置的时机就弹出识别结果（可选中、可复制）；返回是否真的弹了。

    文本为空、或该时机没启用时什么都不做——调用方（复制路径）已经在做复制，
    这里只是展示，不能因为它失败而影响复制结果。

    ``hint`` 是识别质量之类的补充提醒（例如置信度偏低可以用视觉模型再读一次）：它**只加在
    窗口里**，不进剪贴板——用户复制的应当是识别结果本身，不该被我们的说明污染。
    """
    if not text or not should_show(trigger):
        return False
    try:
        from core.i18n import make_tr
        from ui.dialogs import show_text_dialog

        log_debug(T("按设置显示识别结果对话框: {trigger}", trigger=trigger), "OCR")
        body = f"{text}\n\n{hint}" if hint else text
        show_text_dialog(None, make_tr("OcrResultDialog")("Recognition Result"), body)
        return True
    except Exception as e:
        log_exception(e, T("显示识别结果对话框"))
        return False
