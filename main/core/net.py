# -*- coding: utf-8 -*-
"""网络代理：把设置里的代理配置套到 Qt 的网络层上。

翻译、公式识别的外部服务这类走网络的功能都用 Qt 的网络栈（``QNetworkProxy`` 是进程
级的，设一次全局生效；外部公式服务用标准库 urllib 时会读 ``HTTP_PROXY`` 环境变量，
所以两处都要照顾到）。本机直连能用的用户不受影响——默认是「无代理」。
"""
import os

from core.logger import T, log_debug, log_warning


def apply_proxy_from_settings() -> str:
    """按设置把代理套到进程上，返回实际生效的模式（``none`` / ``manual``）。

    manual 但主机/端口不完整时按直连处理（`get_proxy_config` 已经做过这层判断），
    并在日志里说清楚——否则用户会看到所有网络功能超时却不知道是代理没生效。
    """
    from PySide6.QtNetwork import QNetworkProxy

    try:
        from settings import get_tool_settings_manager

        config = get_tool_settings_manager().get_proxy_config()
    except Exception as e:
        from core.logger import log_exception

        log_exception(e, T("读取代理配置"))
        config = {"mode": "none", "host": "", "port": 0}

    if config["mode"] != "manual":
        QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        # 环境变量里残留的代理也要清掉，否则标准库那几条路径还会走代理
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ.pop(key, None)
        log_debug(T("网络代理: 直连"), "Net")
        return "none"

    proxy = QNetworkProxy(QNetworkProxy.ProxyType.HttpProxy, config["host"], config["port"])
    QNetworkProxy.setApplicationProxy(proxy)
    url = f"http://{config['host']}:{config['port']}"
    os.environ["HTTP_PROXY"] = url
    os.environ["HTTPS_PROXY"] = url
    log_debug(T("网络代理: {host}:{port}", host=config["host"], port=config["port"]), "Net")
    return "manual"


def verify_proxy(host: str = "", port: int = 0, timeout: float = 3.0) -> tuple:
    """试连一下代理服务器，返回 ``(是否连上, 失败原因)``。

    只验证「这台机器能不能连上这个代理」，不验证「代理能不能出网」——后者要发一次
    真实请求，会带上用户的凭据与流量，不该由一个「验证」按钮悄悄做掉。
    """
    import socket

    if not host or not port:
        try:
            from settings import get_tool_settings_manager

            config = get_tool_settings_manager().get_proxy_config()
            host, port = config["host"], config["port"]
        except Exception:
            host, port = host, port
    if not host or not port:
        return False, "代理地址或端口为空"

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, ""
    except OSError as e:
        log_warning(T("代理连接失败: {host}:{port} {e}", host=host, port=port, e=str(e)), "Net")
        return False, str(e)
