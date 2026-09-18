# -*- coding: utf-8 -*-
"""欢迎向导：系统权限页。

首次启动就把权限要齐——用户第一次按热键、第一次截图时撞上「没反应」和「只截到桌面」，
是这一页要提前消掉的两件事。

清单渲染与状态轮询复用设置页那份 ``PermissionList``（同一份渲染逻辑 + 同一份每秒重查），
这里只补向导需要的标题与说明。平台层没声明权限需求（Windows/Linux）时，向导不会创建这
一页，步骤数也少一步。
"""

from PySide6.QtWidgets import QLabel, QVBoxLayout

from core.i18n import make_tr

if __package__:
    from .base_page import BasePage, IllustrationArea, set_welcome_label_style
else:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from base_page import BasePage, IllustrationArea, set_welcome_label_style


_tr = make_tr("WelcomeWizard")


class PermissionGuidePage(BasePage):
    """向导里的权限步骤。"""

    def __init__(self, config_manager, parent=None):
        self._config = config_manager
        super().__init__(
            title=_tr("权限"),
            subtitle=_tr("这两项是系统级的：没有它们，热键会没反应、截图里只有桌面壁纸。"),
            parent=parent,
        )

    def _create_illustration(self):
        # 这一页没有可预览的交互内容：收起插画区，清单才能拿到整幅宽度
        # （向导正文栏本来就窄，留着 300px 的插画会把权限行挤到标题和按钮都被截断）
        area = IllustrationArea(self)
        area.hide()
        return area

    def _build_controls(self, layout: QVBoxLayout):
        from ui.settings_ui.page_permission import PermissionList

        self._hint = QLabel(_tr("权限只对本机生效，随时可以在设置里复查。"), self)
        self._hint.setWordWrap(True)
        set_welcome_label_style(self._hint, role="muted", font_size=12)
        layout.addWidget(self._hint)

        # 分组标题留空：这一页自己的标题就是「权限」
        self.permission_list = PermissionList(self, group_title="")
        layout.addWidget(self.permission_list)
        layout.addStretch()

    def retranslate(self):
        if self.title_label is not None:
            self.title_label.setText(_tr("权限"))
        if self.subtitle_label is not None:
            self.subtitle_label.setText(
                _tr("这两项是系统级的：没有它们，热键会没反应、截图里只有桌面壁纸。")
            )
        self._hint.setText(_tr("权限只对本机生效，随时可以在设置里复查。"))

    def save(self):
        """向导会对每一页调 ``save()``；这一页没有配置项，权限本身由系统持有。"""
        return
