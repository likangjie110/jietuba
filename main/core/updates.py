# -*- coding: utf-8 -*-
"""更新源与「检查更新」入口。

这一版只做「展示 + 打开」：仓库还没有发布接口的调用约定（release 资产命名、版本
比较规则、通道划分都没定），凭猜测做一个「有新版本」的提示，错报比不报更糟。所以
托盘/设置里的「检查更新」把用户送到发布页——那里本来就是唯一的更新来源。

等发布约定定下来，在 `check_for_updates()` 里接 GitHub Releases API 即可，调用方
（托盘项、设置页卡片）不用改。
"""
from core.constants import PROJECT_GITHUB_URL

#: 默认更新源：GitHub 仓库的 releases 页
DEFAULT_UPDATE_SOURCE = f"{PROJECT_GITHUB_URL}/releases"


def update_source_url() -> str:
    """当前更新源地址：配置里填了就用它，否则用内置的 GitHub 发布页。

    配置读不出来（例如设置模块还没起来）也回退到内置地址——这里不能抛异常，
    它是「关于/更新」这类展示路径的最后一道。
    """
    try:
        from settings import get_tool_settings_manager

        configured = get_tool_settings_manager().get_app_setting("update_source_url", "")
    except Exception:
        configured = ""
    configured = str(configured or "").strip()
    return configured or DEFAULT_UPDATE_SOURCE


def open_update_source() -> bool:
    """用系统默认浏览器打开更新源；打不开时记日志并返回 False。"""
    from core.logger import T, log_warning
    from core.platform import shell

    url = update_source_url()
    if shell.open_url(url):
        return True
    log_warning(T("打开更新源失败: {url}", url=url), "Update")
    return False


def check_for_updates(notify=None) -> bool:
    """检查更新。

    现在没有可比较的版本信息（见模块文档），因此「检查」= 打开更新源。`notify`
    留着给将来接 API 时用：拿到结果后调 ``notify(message)`` 由界面展示。
    """
    return open_update_source()
