# -*- coding: utf-8 -*-
"""独立图片查看器：看图、缩放平移、旋转翻转、同目录前后切换、另存/复制。

定位是「不想为了看一眼图而开截图窗口或剪贴板窗口」：从托盘、截图历史、剪贴板都能把图
丢进来（``open_image_viewer(image=...)``），或者给一个文件路径，它就在同目录里前后翻。

渲染用一个 QGraphicsView + QGraphicsPixmapItem：缩放/平移/旋转交给场景变换，像素不会被
重采样两次（自己画的话每次缩放都要重算目标矩形，容易与「100% 到底是几个像素」这类
判断打架）。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPixmap, QTransform
from PySide6.QtWidgets import (
    QFileDialog, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QVBoxLayout, QWidget,
)

from core.i18n import make_tr
from core.image_formats import save_dialog_filter
from core.logger import T, log_debug, log_exception, log_warning
from core.save import SaveService
from core.size_format import human_size
from ui.fluent_lite import PushButton

_tr = make_tr("ImageViewer")

#: 一次缩放的步进倍率
ZOOM_STEP = 1.15
#: 缩放上下限
ZOOM_LIMITS = (0.05, 20.0)

#: 能前后翻的图片扩展名
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".jfif")


class ImageViewer(QWidget):
    """一个图片 + 同目录序列的查看器。"""

    #: 当前图片变化（路径，可能为空=来自内存）
    image_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = QImage()
        self._path = ""
        self._folder_images: list = []
        self._index = -1
        self._zoom = 1.0
        self._rotation = 0          # 0/90/180/270
        self._flip_h = False
        self._flip_v = False
        self.setWindowTitle(_tr("Image Viewer"))
        self.setMinimumSize(720, 520)
        self._build_ui()

    # ── 界面 ──

    def _build_ui(self):
        from core.resource_manager import ResourceManager

        icon = ResourceManager.get_app_icon()
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        specs = (
            (_tr("Previous"), self.show_previous),
            (_tr("Next"), self.show_next),
            (_tr("Zoom In"), lambda: self.zoom_by(ZOOM_STEP)),
            (_tr("Zoom Out"), lambda: self.zoom_by(1.0 / ZOOM_STEP)),
            (_tr("Fit Window"), self.fit_to_window),
            (_tr("Actual Size"), self.actual_size),
            (_tr("Rotate Right"), lambda: self.rotate(90)),
            (_tr("Rotate Left"), lambda: self.rotate(-90)),
            (_tr("Flip Horizontal"), self.flip_horizontal),
            (_tr("Flip Vertical"), self.flip_vertical),
            (_tr("Copy"), self.copy_image),
            (_tr("Save as"), self.save_as),
            (_tr("Close"), self.close),
        )
        self.buttons = []
        for label, slot in specs:
            button = PushButton(label, self)
            button.clicked.connect(slot)
            bar.addWidget(button)
            self.buttons.append(button)
        bar.addStretch(1)
        outer.addLayout(bar)

        self.scene = QGraphicsScene(self)
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)
        self.view = QGraphicsView(self.scene, self)
        self.view.setRenderHint(self.view.renderHints())
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.setBackgroundBrush(Qt.GlobalColor.darkGray)
        self.view.wheelEvent = self._on_wheel  # 滚轮缩放（默认是滚动）
        outer.addWidget(self.view, 1)

        self.info_label = QLabel("", self)
        self.info_label.setWordWrap(True)
        outer.addWidget(self.info_label)

    # ── 载入 ──

    def load_file(self, path: str) -> bool:
        """载入一个文件，并把同目录的图片做成前后序列。"""
        image = QImage(str(path))
        if image.isNull():
            log_warning(T("读取图片失败: {path}", path=path), "ImageViewer")
            return False
        self._path = str(path)
        self._folder_images = _sibling_images(path)
        try:
            self._index = self._folder_images.index(Path(path).name)
        except ValueError:
            self._index = -1
        self._set_image(image)
        log_debug(T("查看器载入文件: {path} ({w}x{h})", path=path, w=image.width(),
                    h=image.height()), "ImageViewer")
        return True

    def load_image(self, image: QImage, *, name: str = "") -> bool:
        """直接载入一张内存里的图（来自剪贴板 / 截图历史）。"""
        if image is None or image.isNull():
            return False
        self._path = ""
        self._folder_images = []
        self._index = -1
        self._set_image(QImage(image))
        if name:
            self.info_label.setText(name + "  ·  " + self.info_label.text())
        return True

    def _set_image(self, image: QImage) -> None:
        self._image = image
        self._zoom = 1.0
        self._rotation = 0
        self._flip_h = False
        self._flip_v = False
        self._apply_transform()
        self.fit_to_window()
        self._refresh_info()
        self.image_changed.emit(self._path)

    # ── 变换 ──

    @property
    def image(self) -> QImage:
        return self._image

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def rotation(self) -> int:
        return self._rotation

    def is_flipped(self) -> tuple:
        return self._flip_h, self._flip_v

    def displayed_pixmap(self) -> QPixmap:
        """当前真正显示出来的位图（含旋转/翻转），导出与断言都看它。"""
        return self.pixmap_item.pixmap()

    def _apply_transform(self) -> None:
        if self._image.isNull():
            self.pixmap_item.setPixmap(QPixmap())
            return
        transform = QTransform()
        transform.rotate(self._rotation)
        transform.scale(-1.0 if self._flip_h else 1.0, -1.0 if self._flip_v else 1.0)
        pixmap = QPixmap.fromImage(self._image).transformed(
            transform, Qt.TransformationMode.SmoothTransformation)
        self.pixmap_item.setPixmap(pixmap)
        self.scene.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        self.view.setTransform(QTransform().scale(self._zoom, self._zoom))
        self._refresh_info()

    def zoom_by(self, factor: float) -> bool:
        """按倍率缩放（带上限）；越界时不改。"""
        target = self._zoom * float(factor)
        target = max(ZOOM_LIMITS[0], min(ZOOM_LIMITS[1], target))
        if abs(target - self._zoom) < 1e-6:
            return False
        self._zoom = target
        self.view.setTransform(QTransform().scale(self._zoom, self._zoom))
        self._refresh_info()
        return True

    def fit_to_window(self) -> bool:
        """缩放到刚好放得下（保持比例）。"""
        if self._image.isNull():
            return False
        pixmap = self.pixmap_item.pixmap()
        viewport = self.view.viewport().size()
        if pixmap.isNull() or viewport.width() <= 2 or viewport.height() <= 2:
            return False
        self._zoom = min(viewport.width() / pixmap.width(),
                         viewport.height() / pixmap.height())
        self.view.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self.view.transform().m11()
        self._refresh_info()
        return True

    def actual_size(self) -> bool:
        """回到 100%。"""
        if self._image.isNull():
            return False
        self._zoom = 1.0
        self.view.setTransform(QTransform())
        self._refresh_info()
        return True

    def rotate(self, degrees: int) -> bool:
        """旋转（顺时针为正），每次 90°。"""
        if self._image.isNull():
            return False
        self._rotation = (self._rotation + int(degrees)) % 360
        self._apply_transform()
        self.fit_to_window()
        return True

    def flip_horizontal(self) -> bool:
        if self._image.isNull():
            return False
        self._flip_h = not self._flip_h
        self._apply_transform()
        return True

    def flip_vertical(self) -> bool:
        if self._image.isNull():
            return False
        self._flip_v = not self._flip_v
        self._apply_transform()
        return True

    def _on_wheel(self, event):
        """滚轮缩放（Ctrl 不需要：这个窗口里滚轮没有别的用途）。"""
        self.zoom_by(ZOOM_STEP if event.angleDelta().y() > 0 else 1.0 / ZOOM_STEP)
        event.accept()

    # ── 序列 ──

    def has_sequence(self) -> bool:
        return len(self._folder_images) > 1 and self._index >= 0

    def show_next(self) -> bool:
        return self._step(1)

    def show_previous(self) -> bool:
        return self._step(-1)

    def _step(self, delta: int) -> bool:
        if not self.has_sequence():
            return False
        index = (self._index + delta) % len(self._folder_images)
        folder = os.path.dirname(self._path)
        if not self.load_file(os.path.join(folder, self._folder_images[index])):
            return False
        return True

    def sequence_position(self) -> tuple:
        """当前在序列里的位置 ``(序号, 总数)``；不在序列里时 ``(0, 0)``。"""
        if not self.has_sequence():
            return 0, 0
        return self._index + 1, len(self._folder_images)

    # ── 导出 ──

    def copy_image(self) -> bool:
        """把当前显示的画面复制到剪贴板（含旋转/翻转后的样子）。"""
        pixmap = self.displayed_pixmap()
        if pixmap.isNull():
            return False
        from core.platform import clipboard as platform_clipboard

        ok = bool(platform_clipboard.copy_image(pixmap.toImage()))
        log_debug(T("查看器复制图片: {ok}", ok=ok), "ImageViewer")
        return ok

    def save_as(self) -> bool:
        """另存为（格式清单与其它保存入口共用派生清单）。"""
        pixmap = self.displayed_pixmap()
        if pixmap.isNull():
            return False
        default_name = Path(self._path).name if self._path else "image.png"
        path, selected_filter = QFileDialog.getSaveFileName(
            self, _tr("Save as"), default_name, save_dialog_filter())
        if not path:
            return False

        from core.image_formats import normalize

        image_format = _format_from_filter(path, selected_filter)
        if not os.path.splitext(path)[1]:
            path = f"{path}.{image_format.lower()}"
        service = SaveService()
        saved = service.save_qimage_to_path(pixmap.toImage(), path,
                                            image_format=normalize(image_format))
        if saved:
            log_debug(T("查看器另存: {path}", path=path), "ImageViewer")
        return bool(saved)

    def _refresh_info(self) -> None:
        if self._image.isNull():
            self.info_label.setText(_tr("No image loaded."))
            return
        pixmap = self.pixmap_item.pixmap()
        parts = []
        if self._path:
            parts.append(Path(self._path).name)
        parts.append(f"{pixmap.width()}×{pixmap.height()}")
        parts.append(f"{int(self._zoom * 100)}%")
        if self._rotation:
            parts.append(f"{self._rotation}°")
        if self._flip_h or self._flip_v:
            parts.append(_tr("flipped"))
        if self._path and os.path.exists(self._path):
            parts.append(human_size(os.path.getsize(self._path)))
        position, total = self.sequence_position()
        if total:
            parts.append(f"{position}/{total}")
        self.info_label.setText("  ·  ".join(part for part in parts if part))


def _sibling_images(path: str) -> list:
    """同目录里的图片文件名（按名字排序，供前后翻页）。"""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = [name for name in os.listdir(folder)
                 if name.lower().endswith(IMAGE_SUFFIXES)]
    except OSError as e:
        log_exception(e, T("读取图片目录"))
        return []
    names.sort(key=str.lower)
    return names


def _format_from_filter(path: str, selected_filter: str) -> str:
    """按扩展名定格式；扩展名缺失时退回过滤器里写的那个。"""
    extension = os.path.splitext(path)[1].lstrip(".").upper()
    if extension:
        return "JPG" if extension == "JPEG" else extension
    match = selected_filter.split("(")[-1].split(")")[0].strip().lstrip("*.")
    return (match or "png").upper()


def open_image_viewer(*, path: str = "", image: QImage | None = None,
                      config_manager=None) -> ImageViewer | None:
    """打开（或复用）查看器；给路径就翻同目录，给图就显示内存里的那张。"""
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    viewer = getattr(application, "_image_viewer", None)
    if viewer is None:
        viewer = ImageViewer()
        if application is not None:
            application._image_viewer = viewer
            viewer.destroyed.connect(lambda: setattr(application, "_image_viewer", None))

    if path:
        viewer.load_file(path)
    elif image is not None:
        viewer.load_image(image)
    elif viewer.image.isNull():
        log_warning(T("查看器没有可显示的图片"), "ImageViewer")
        return None

    viewer.show()
    viewer.raise_()
    viewer.activateWindow()
    return viewer
