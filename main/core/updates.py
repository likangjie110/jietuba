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


def run_update_check(notify=None, *, opener=None, local_version: str = "") -> "UpdateCheck":
    """跑一次更新检查并把结果交给 ``notify``（设置页/托盘用它展示）。

    ``opener`` 与 ``local_version`` 供测试注入。返回值是完整结果，调用方据此决定
    要不要弹「有新版」——**不是**「检查有没有成功」。
    """
    from core.logger import T, log_warning

    if not local_version:
        local_version = _local_version()
    result = check_remote(local_version, opener)
    if notify is not None:
        try:
            notify(result.message)
        except Exception as e:
            log_warning(T("展示更新检查结果失败: {e}", e=e), "Update")
    return result


def check_for_updates(notify=None, *, opener=None, local_version: str = "") -> bool:
    """检查更新：有更新版本返回 True；已是最新或读不到远端都返回 False。

    读不到远端与「已是最新」都返回 False 是有意的：这个布尔值的语义是「要不要提示
    用户去下载」，不是「检查有没有成功」。要区分两者用 ``run_update_check()``。
    """
    return bool(run_update_check(notify, opener=opener, local_version=local_version))


def _local_version() -> str:
    """本程序版本号（main_app.APP_VERSION）。"""
    try:
        from main_app import APP_VERSION

        return str(APP_VERSION)
    except Exception:
        return ""


# ======================================================================
# 版本比较与远端检查
# ======================================================================

#: 单次检查的超时（秒）
CHECK_TIMEOUT = 10

#: 仓库的 Releases API（GitHub 官方接口，公开仓库免鉴权）
RELEASES_API = "https://api.github.com/repos/1003129155/jietuba/releases/latest"


class UpdateCheck:
    """一次检查的结果。

    ``state`` 只有四种：``newer``（有新版）/ ``current``（已是最新）/
    ``unknown``（远端读不到）/ ``dev``（版本号不可比，例如开发中）。**没有第五种
    「大概有新版」**：猜出来的「有新版本」比不报更糟。
    """

    def __init__(self, state: str, *, local: str = "", remote: str = "",
                 message: str = "", url: str = ""):
        self.state = state
        self.local = local
        self.remote = remote
        self.message = message
        self.url = url

    def __bool__(self) -> bool:
        return self.state == "newer"

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"UpdateCheck(state={self.state!r}, local={self.local!r}, remote={self.remote!r})"


def parse_version(text: str) -> tuple:
    """把版本号解析成可比较的元组；带字母/构建号的尾巴忽略。

    ``1.9`` → ``(1, 9)``、``v1.10.2-beta`` → ``(1, 10, 2)``、``dev`` → ``()``。
    解析不出数字时返回空元组，调用方据此报「不可比」而不是猜大小。
    """
    cleaned = str(text or "").strip().lstrip("vV")
    numbers = []
    for part in cleaned.replace("-", ".").replace("_", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        numbers.append(int(digits))
    return tuple(numbers)


def compare_versions(local: str, remote: str) -> str:
    """比较两个版本号：``newer`` / ``current`` / ``unknown``。"""
    left, right = parse_version(local), parse_version(remote)
    if not left or not right:
        return "unknown"
    if right > left:
        return "newer"
    return "current"


def fetch_latest_release(opener=None, *, api_url: str = RELEASES_API):
    """读远端最新发布；返回 ``(tag_name, html_url, error)``。

    ``opener`` 可注入（测试用本地 stub），生产走 urllib。网络失败**不**吞掉：调用方
    要如实告诉用户「读不到远端」。
    """
    import json
    import urllib.request

    request = urllib.request.Request(
        api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "jietuba"})
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=CHECK_TIMEOUT) as response:
            status = getattr(response, "status", 200)
            raw = response.read()
        if status != 200:
            return "", "", f"HTTP {status}"
        payload = json.loads(raw.decode("utf-8", "replace"))
    except Exception as e:
        return "", "", str(e)
    return (str(payload.get("tag_name") or ""),
            str(payload.get("html_url") or ""),
            "")


def check_remote(local_version: str, opener=None, *, api_url: str = RELEASES_API) -> UpdateCheck:
    """检查远端版本并与本地比较；每一种结果都带一句能直接显示的话。"""
    from core.logger import T, log_debug

    tag, url, error = fetch_latest_release(opener, api_url=api_url)
    if error or not tag:
        log_debug(T("读取远端版本失败: {error}", error=error or "空响应"), "Update")
        return UpdateCheck(
            "unknown", local=local_version, url=update_source_url(),
            message=T("读不到远端版本信息: {error}", error=error or "空响应").render())

    state = compare_versions(local_version, tag)
    if state == "newer":
        message = T("有新版本 {remote}（当前 {local}）", remote=tag,
                    local=local_version).render()
    elif state == "current":
        message = T("已是最新版本（{local}）", local=local_version).render()
    else:
        message = T("无法比较版本号（远端 {remote}，当前 {local}）", remote=tag,
                    local=local_version).render()
    log_debug(T("更新检查: {state}（本地 {local}，远端 {remote}）", state=state,
                local=local_version, remote=tag), "Update")
    return UpdateCheck(state, local=local_version, remote=tag, message=message,
                       url=url or update_source_url())
