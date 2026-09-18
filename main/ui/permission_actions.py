# -*- coding: utf-8 -*-
"""权限相关的跨模块动作：打开权限设置页、重开本程序。

权限提示弹窗（``ui.permission_prompt``）、权限页上的按钮都要做这两件事，而它们各自需要
「应用实例」和「平台层」两样东西。收在这里一次，免得每个调用点各自去摸 ``QApplication``
和 ``main_app``。
"""


def open_permission_settings() -> bool:
    """打开设置窗口并跳到权限页；平台没有权限页或应用还没起来时返回 False。"""
    from main_app import main_app_instance

    app = main_app_instance()
    if app is None:
        return False

    app.open_settings()
    window = getattr(app, "settings_window", None)
    if window is None:
        return False
    return bool(window.show_permission_page())


def restart_application() -> bool:
    """重开本程序（当前实例会退出）；重开动作交不出去时返回 False。

    调用方（权限页上的按钮）负责先确认，这里只负责「重开 + 让当前实例退出」。
    """
    from PySide6.QtWidgets import QApplication

    from core.platform import process
    from main_app import main_app_instance

    if not process.relaunch_application():
        return False

    app = main_app_instance()
    if app is not None:
        app.quit_app()
        return True

    # 应用实例没登记（例如在测试或异常启动路径里）：退到 Qt 的退出，至少不留两个进程
    instance = QApplication.instance()
    if instance is not None:
        instance.quit()
    return True
