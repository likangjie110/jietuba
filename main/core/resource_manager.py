"""
资源管理器 - 统一管理应用资源路径和图标缓存
"""
import os
import sys

from core.logger import log_debug, log_exception, log_warning, T


# 立即光栅化时覆盖的目标框尺寸。
#
# 为什么必须立即光栅化：QIcon(路径) 是惰性的，它只记住文件名，真正读文件推迟到
# 每次需要位图的时候，渲染结果丢进全局 QPixmapCache（默认上限只有 10 MB）。
# onefile 打包会把资源解压到 %TEMP%\_MEIxxxxxx，这个目录很容易被 Windows 存储感知、
# 磁盘清理或第三方清理工具删掉。文件没了以后图标不会立刻出问题——直到 QPixmapCache
# 里的条目被后续绘制挤掉、或者界面请求了一个没渲染过的新尺寸，
# 此时所有图标会同时变成空白且永不恢复，表现就是"运行一段时间后工具栏按钮突然全没了"。
#
# 一次性渲染成位图存进 QIcon 之后，运行期就不再依赖源文件，顺带也省掉了每次绘制的文件 IO。
_RASTER_BOX_SIZES = (16, 24, 32, 48, 64, 128)


class ResourceManager:
    # 应用级 QIcon 缓存：SVG 路径 → QIcon 对象
    # QIcon 内部持有光栅化后的位图，跨 ScreenshotWindow 生命周期复用
    _icon_cache: dict = {}

    @staticmethod
    def get_resource_path(relative_path):
        """
        获取资源文件的绝对路径
        支持 PyInstaller 打包后的路径处理
        """
        if hasattr(sys, '_MEIPASS'):
            # PyInstaller 打包后：临时文件夹路径
            base_path = sys._MEIPASS
        else:
            # 开发环境：从 core/ 向上两级到项目根目录
            current_dir = os.path.dirname(os.path.abspath(__file__))
            base_path = os.path.dirname(os.path.dirname(current_dir))

        return os.path.join(base_path, relative_path)

    @staticmethod
    def get_icon_path(icon_name):
        """获取图标路径 - 从根目录的 svg 文件夹"""
        return ResourceManager.get_resource_path(os.path.join("svg", icon_name))

    @staticmethod
    def _rasterize(svg_path: str, size: int = 0):
        """把 SVG 立即渲染成位图 QIcon，之后不再依赖源文件。

        Args:
            svg_path: SVG 文件的绝对路径
            size:     > 0 时渲染成 size×size 的正方形（铺满画布，与旧行为一致，
                      GIF 工具栏依赖这一点来保证各图标视觉大小统一）；
                      为 0 时按原始宽高比渲染多个尺寸。

        Returns:
            QIcon；渲染失败返回 None，由调用方决定如何回退。
        """
        from PySide6.QtCore import Qt, QSize
        from PySide6.QtGui import QIcon, QPixmap, QPainter
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(svg_path)
        if not renderer.isValid():
            return None

        def render_to(target: QSize):
            pm = QPixmap(target)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            renderer.render(painter)
            painter.end()
            return pm

        icon = QIcon()

        if size > 0:
            icon.addPixmap(render_to(QSize(size, size)))
            return icon

        default = renderer.defaultSize()
        if default.isEmpty():
            default = QSize(64, 64)

        seen = set()
        for box in _RASTER_BOX_SIZES:
            # 保持宽高比：arrow_*.svg 是 128×32、step_*.svg 是 10×6，
            # 铺满方形画布会把它们拉变形。Qt 自带的 SVG 图标引擎也是按比例缩放的，
            # 这里与之保持一致。
            target = default.scaled(box, box, Qt.AspectRatioMode.KeepAspectRatio)
            if target.isEmpty():
                continue
            key = (target.width(), target.height())
            if key in seen:
                continue
            seen.add(key)
            icon.addPixmap(render_to(target))

        return icon if not icon.isNull() else None

    #: 品牌图标：应用图标（窗口/任务栏/打包）用它；托盘那一枚是另一回事
    BRAND_ICON_NAME = "品牌.svg"
    FALLBACK_BRAND_ICON_NAME = "托盘.svg"
    #: 配置里自定义 logo 的键（app/custom_logo_path）
    CUSTOM_LOGO_SETTING_KEY = "custom_logo_path"

    @staticmethod
    def custom_logo_path() -> str:
        """配置里指定的自定义 logo 图片路径。

        没配、路径不存在、配置读不出来都返回空串——调用方据此回退内置图标，
        不需要自己判断这些情况。
        """
        try:
            from settings import get_tool_settings_manager

            path = get_tool_settings_manager().get_app_setting(
                ResourceManager.CUSTOM_LOGO_SETTING_KEY, ""
            )
        except Exception as e:
            log_exception(e, T("读取自定义 logo 路径"))
            return ""

        if not isinstance(path, str) or not path.strip():
            return ""
        path = path.strip()
        if not os.path.exists(path):
            log_warning(T("自定义 logo 文件不存在: {path}", path=path), "Resource")
            return ""
        return path

    @staticmethod
    def custom_logo_icon():
        """自定义 logo 的 QIcon；没配或读不出图返回 None（调用方回退内置图标）。

        用户挑的可能是 SVG、PNG、JPG 或根本不是图片：统一走 QIcon 加载 + 判空，
        读不出就当作没设，绝不返回一个「什么都画不出来」的空图标。
        """
        from PySide6.QtGui import QIcon

        path = ResourceManager.custom_logo_path()
        if not path:
            return None

        key = f"__custom_logo__:{path}"
        cached = ResourceManager._icon_cache.get(key)
        if cached is not None:
            return cached or None

        icon = QIcon(path)
        if icon.isNull():
            log_warning(T("自定义 logo 无法解析为图片: {path}", path=path), "Resource")
            ResourceManager._icon_cache[key] = QIcon()
            return None

        ResourceManager._icon_cache[key] = icon
        return icon

    @staticmethod
    def refresh_app_icons() -> bool:
        """配置里的 logo 变了之后重新解析，并套到应用与已存在的顶层窗口上。

        返回当前是否在用自定义 logo。托盘那一枚由 main_app 自己重设（它持有
        QSystemTrayIcon），这里管窗口图标与 ``QApplication`` 的默认图标。
        """
        from PySide6.QtGui import QGuiApplication

        # 只清应用图标那一份缓存；按路径缓存的 logo 复用，避免每保存一次都重新解码
        ResourceManager._icon_cache.pop("__app_icon__", None)
        icon = ResourceManager.get_app_icon()

        app = QGuiApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)
            for widget in app.topLevelWidgets():
                try:
                    if widget.isWindow():
                        widget.setWindowIcon(icon)
                except Exception:
                    continue

        using_custom = bool(ResourceManager.custom_logo_path())
        log_debug(
            T("应用图标已刷新: {state}", state="自定义 logo" if using_custom else "内置品牌图标"),
            "Resource",
        )
        return using_custom

    @staticmethod
    def get_app_icon():
        """应用（品牌）图标的 QIcon：自定义 logo 优先，其次内置品牌图标。

        调用方拿到的可能仍是空 QIcon（资源全丢时），所以别在这里抛异常——图标坏了
        不该让窗口建不出来。
        """
        from PySide6.QtGui import QIcon

        cached = ResourceManager._icon_cache.get("__app_icon__")
        if cached is not None:
            return cached

        custom = ResourceManager.custom_logo_icon()
        if custom is not None:
            ResourceManager._icon_cache["__app_icon__"] = custom
            return custom

        icon = QIcon()
        for name in (ResourceManager.BRAND_ICON_NAME,
                     ResourceManager.FALLBACK_BRAND_ICON_NAME):
            path = ResourceManager.get_icon_path(name)
            if os.path.exists(path):
                candidate = QIcon(path)
                if not candidate.isNull():
                    icon = candidate
                    break
        ResourceManager._icon_cache["__app_icon__"] = icon
        return icon

    @staticmethod
    def get_icon(svg_path: str, size: int = 0):
        """获取 QIcon（带缓存，内容已光栅化，不再依赖源文件）。

        Args:
            svg_path: SVG 文件的绝对路径
            size: 若 > 0，则渲染到 size×size 的正方形，确保不同 SVG 图标大小一致。
                  若为 0（默认），按原始宽高比渲染一组常用尺寸。

        Returns:
            QIcon 对象
        """
        cache_key = (svg_path, size)
        icon = ResourceManager._icon_cache.get(cache_key)
        if icon is not None:
            return icon

        from PySide6.QtGui import QIcon

        try:
            icon = ResourceManager._rasterize(svg_path, size)
        except Exception as e:
            log_exception(e, T("SVG 渲染图标"))
            icon = None

        if icon is None:
            # 渲染失败不写缓存：否则一次偶发失败会被永久固化，
            # 资源恢复之后仍然只能拿到空图标。
            log_warning(T("图标渲染失败，本次返回空图标: {svg_path}", svg_path=svg_path), "Resource")
            return QIcon()

        ResourceManager._icon_cache[cache_key] = icon
        return icon

    @staticmethod
    def get_icon_by_name(icon_name: str, size: int = 0):
        """按 svg/ 下的文件名取图标，等价于 get_icon(get_icon_path(name), size)。"""
        return ResourceManager.get_icon(
            ResourceManager.get_icon_path(icon_name), size
        )
