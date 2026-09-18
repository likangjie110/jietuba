# -*- coding: utf-8 -*-
"""权限清单与设置里的权限页。

清单来自平台层（``core/platform/permissions``）：那边声明要哪些权限、怎么查、怎么要；
这里只负责渲染和三个动作（去授权 / 打开系统设置面板 / 重启本程序）。

``PermissionList`` 是复用点：设置对话框的权限页与欢迎向导的权限步骤都嵌它——同一份渲染
和同一份状态轮询，两边不会各写一套然后慢慢漂移。没有系统权限门槛的平台清单为空，
``PermissionList`` 就是空的，两个宿主都按清单为空不显示（见 ``SettingsDialog`` 与 ``WelcomeWizard``）。

列表可见时每秒重查一次状态——用户是在系统设置里勾选完切回来的，状态不过期才算「快速授权」。
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from core import safe_event
from core.i18n import make_tr
from core.platform import permissions
from ui.dialogs import show_confirm_dialog
from ui.fluent_lite import PrimaryPushButton, PushButton

from .components import SettingCardGroup, WhiteCard, apply_theme_text_style, theme_color

# 文案上下文与设置对话框其余页面一致：界面字符串仍走 app_*.xml，不在这里硬编码译文。
# 向导里嵌的也是这份组件，所以两类文字的上下文都落在 SettingsDialog。
_tr = make_tr("SettingsDialog")

# 每条权限的文案（标题 + 为什么需要它）。key 由平台层给，文案留在界面这一侧。
_TEXTS = {
    permissions.ACCESSIBILITY: (
        "Accessibility Permission",
        "Global hotkeys only work with this permission, and smart selection uses it to "
        "detect the element under the cursor.",
    ),
    permissions.SCREEN_RECORDING: (
        "Screen Recording Permission",
        "Screenshots contain window contents only with this permission; without it they "
        "show the desktop wallpaper only.",
    ),
}

# 状态色：已授权/未授权分别一冷一暖，深浅主题各一份
_GRANTED_COLOR = ("#2E7D5B", "#7ED0A5")
_MISSING_COLOR = ("#B4632A", "#E0A063")

# 用户是在系统设置里勾的，1 秒足够跟手；一次 preflight 几乎不花钱
_REFRESH_INTERVAL_MS = 1000


@dataclass
class _Row:
    """一条权限对应的控件，刷新时只需要这几个。"""

    permission: permissions.Permission
    status: QLabel
    hint: QLabel
    authorize: PushButton
    restart: PushButton
    # 上一次画出来的 (已授权, 状态色)：状态或主题没变就不必重设样式表（每秒一次会反复 polish）
    painted: tuple[bool, str] | None = None


def _status_text(granted: bool) -> str:
    return _tr("Granted") if granted else _tr("Not Granted")


def _restart_application(parent) -> None:
    """确认后重开本程序——这条权限要重启才生效，让用户自己去找菜单退出太费事。"""
    if not show_confirm_dialog(
        parent,
        _tr("Restart jietuba"),
        _tr(
            "jietuba will close and reopen so the newly granted permission takes effect."
        ),
    ):
        return

    # 延迟导入：重开要用到 main_app 的实例，模块级导入会绕成环
    from ui.permission_actions import restart_application

    restart_application()


def _build_row(group, permission_list, permission) -> _Row:
    title_source, description_source = _TEXTS.get(permission.key, (permission.key, ""))

    card = WhiteCard(group)
    card.setObjectName(f"PermissionRow_{permission.key}")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 14, 20, 14)
    layout.setSpacing(6)

    head = QHBoxLayout()
    head.setSpacing(10)
    title = QLabel(_tr(title_source), card)
    apply_theme_text_style(title, 14, bold=True)
    head.addWidget(title, 1)

    status = QLabel("", card)
    status.setObjectName(f"PermissionStatus_{permission.key}")
    head.addWidget(status, 0, Qt.AlignmentFlag.AlignRight)
    layout.addLayout(head)

    description = QLabel(_tr(description_source), card)
    description.setWordWrap(True)
    apply_theme_text_style(description, 12, caption=True)
    layout.addWidget(description)

    hint = QLabel(_tr("Granted. Restart jietuba for it to take effect."), card)
    hint.setObjectName(f"PermissionRestart_{permission.key}")
    hint.setWordWrap(True)
    apply_theme_text_style(
        hint, 12, caption=True, extra=f"color: {theme_color(*_MISSING_COLOR)};"
    )
    hint.setVisible(False)
    layout.addWidget(hint)

    buttons = QHBoxLayout()
    buttons.setSpacing(8)
    buttons.addStretch(1)

    # 系统对话框只在本程序还没登记进列表时弹；已经拒绝过就只能把人送到面板前
    open_settings = PushButton(_tr("Open System Settings"), card)
    open_settings.setObjectName(f"PermissionSettings_{permission.key}")
    open_settings.setFixedHeight(32)
    open_settings.clicked.connect(
        lambda _checked=False, item=permission: permissions.open_settings(item)
    )
    buttons.addWidget(open_settings)

    restart = PushButton(_tr("Restart jietuba"), card)
    restart.setObjectName(f"PermissionRestartButton_{permission.key}")
    restart.setFixedHeight(32)
    restart.setVisible(False)
    restart.clicked.connect(lambda _checked=False, host=card: _restart_application(host))
    buttons.addWidget(restart)

    authorize = PrimaryPushButton(_tr("Authorize"), card)
    authorize.setObjectName(f"PermissionGrant_{permission.key}")
    authorize.setFixedHeight(32)
    authorize.clicked.connect(
        lambda _checked=False, item=permission: _authorize(permission_list, item)
    )
    buttons.addWidget(authorize)
    layout.addLayout(buttons)

    group.addSettingCard(card)
    return _Row(
        permission=permission, status=status, hint=hint,
        authorize=authorize, restart=restart,
    )


def _authorize(permission_list, permission) -> None:
    """弹系统授权对话框。它会阻塞到用户应答，所以回来立刻重查一次状态。"""
    permissions.request(permission)
    permission_list.refresh()


class PermissionList(QWidget):
    """权限清单：每条权限一行，可见时轮询状态。设置页与欢迎向导共用这一份。

    ``group_title`` 传 None 用默认的「系统权限」；传空串则不显示分组标题（向导页自己的
    标题已经是「权限」，再叠一层分组标题就重复了）。
    """

    def __init__(self, parent=None, *, group_title: str | None = None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        group = SettingCardGroup(_tr("System Permissions"), self)
        if group_title is not None:
            group.titleLabel.setText(group_title)
        if not group.titleLabel.text():
            group.titleLabel.hide()
        layout.addWidget(group)

        self._permission_rows = [
            _build_row(group, self, permission)
            for permission in permissions.requirements()
        ]

        self._permission_timer = QTimer(self)
        self._permission_timer.setInterval(_REFRESH_INTERVAL_MS)
        self._permission_timer.timeout.connect(self.refresh)

        self.refresh()

    def refresh(self) -> None:
        """重查每条权限的状态并更新行。"""
        for row in self._permission_rows:
            granted = permissions.trusted(row.permission)
            color = theme_color(*(_GRANTED_COLOR if granted else _MISSING_COLOR))
            if row.painted == (granted, color):
                continue
            row.painted = (granted, color)

            apply_theme_text_style(row.status, 13, bold=True, extra=f"color: {color};")
            row.status.setText(_status_text(granted))
            # 已授权就没什么可要的了：按钮留着只会让人以为还能再授权一次
            row.authorize.setEnabled(not granted)

            # 屏幕录制权限是进程启动时读一次的：本次运行里才拿到的，说清楚要重启
            pending = granted and permissions.pending_restart(row.permission)
            row.hint.setVisible(pending)
            row.restart.setVisible(pending)

    @safe_event
    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._permission_timer.start()

    @safe_event
    def hideEvent(self, event):
        self._permission_timer.stop()
        super().hideEvent(event)


class PermissionPage(QScrollArea):
    """设置对话框里的权限页：一句说明 + 权限清单。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        view = QWidget()
        view.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(20)

        intro = QLabel(
            _tr(
                "jietuba needs these system permissions. Grant them here; the status "
                "refreshes by itself once granted."
            ),
            view,
        )
        intro.setWordWrap(True)
        apply_theme_text_style(intro, 13, caption=True)
        layout.addWidget(intro)

        self.permission_list = PermissionList(view)
        layout.addWidget(self.permission_list)
        layout.addStretch()
        self.setWidget(view)


def create_permission_page(dialog) -> QWidget:
    """创建权限设置页。只在平台声明了权限需求时被调用。

    ``dialog`` 只用来与其他页面工厂保持同一签名——本页不读任何配置。
    """
    return PermissionPage()
