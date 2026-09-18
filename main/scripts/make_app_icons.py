# -*- coding: utf-8 -*-
"""从品牌 SVG 生成打包用的图标文件（.icns / .ico / .png）。

图标只有 SVG 一份源（``svg/品牌.svg``），二进制产物在构建时生成、不进仓库——
这样改图标只要改那一个 SVG，也不会让仓库里躺着几个几百 KB 的二进制。

用法（在仓库根目录）::

    python main/scripts/make_app_icons.py

产物落在 ``build/icons/``：``jietuba.icns``（macOS）、``jietuba.ico``（Windows）、
``jietuba_<size>.png``（Linux / 窗口图标备用）。
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SVG_PATH = REPO_ROOT / "svg" / "品牌.svg"
OUT_DIR = REPO_ROOT / "build" / "icons"

#: macOS .icns 需要的 .iconset 尺寸（@2x 由 iconutil 认文件名后缀）
ICONSET_SIZES = (16, 32, 128, 256, 512)
#: 尺寸齐全的 PNG（窗口图标、Linux 打包备用）
PNG_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def _render(svg_path: Path, size: int, target: Path) -> None:
    """把 SVG 渲染成指定边长的 PNG。"""
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise RuntimeError(f"SVG 无法解析：{svg_path}")

    image = QImage(QSize(size, size), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    if not image.save(str(target), "PNG"):
        raise RuntimeError(f"PNG 写出失败：{target}")


def build_icns(png_paths: dict, target: Path) -> bool:
    """用 macOS 自带的 iconutil 生成 .icns；非 macOS 直接跳过。"""
    if sys.platform != "darwin" or shutil.which("iconutil") is None:
        return False

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "jietuba.iconset"
        iconset.mkdir()
        for size in ICONSET_SIZES:
            shutil.copyfile(png_paths[size], iconset / f"icon_{size}x{size}.png")
            shutil.copyfile(png_paths[size], iconset / f"icon_{size}x{size}@2x.png")
        result = subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
            capture_output=True, text=True, check=False,
        )
    if result.returncode != 0:
        print(f"iconutil 失败：{result.stderr.strip()}")
        return False
    return True


def build_ico(png_paths: dict, target: Path) -> bool:
    """用 Pillow 打包多尺寸 .ico（Windows）。"""
    try:
        from PIL import Image
    except ImportError:
        print("没有 Pillow，跳过 .ico")
        return False

    sizes = [16, 32, 64, 128, 256]
    base = Image.open(png_paths[256]).convert("RGBA")
    base.save(target, format="ICO", sizes=[(s, s) for s in sizes])
    return True


def main() -> int:
    if not SVG_PATH.exists():
        print(f"找不到品牌 SVG：{SVG_PATH}")
        return 1

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    png_paths = {}
    for size in PNG_SIZES:
        target = OUT_DIR / f"jietuba_{size}.png"
        _render(SVG_PATH, size, target)
        png_paths[size] = target
    print(f"PNG：{len(png_paths)} 个尺寸 → {OUT_DIR}")

    if build_icns(png_paths, OUT_DIR / "jietuba.icns"):
        print("icns：build/icons/jietuba.icns")
    if build_ico(png_paths, OUT_DIR / "jietuba.ico"):
        print("ico：build/icons/jietuba.ico")

    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
