"""
截图吧 - macOS 本地 .app 打包脚本

与 Windows 的 build_with_ocr_onefile.py 分开写，因为差异不止一个参数：
  - 那份 spec 里有一层 PySide6 二进制白名单，只认 qt6core.dll / *.pyd；macOS 的 Qt 是
    .framework/.dylib、绑定是 *.abi3.so，套用那份白名单会把整个 Qt 过滤光
  - 它的隐藏导入含 win32api / mss.windows，macOS 上没有这些模块
  - macOS 要 BUNDLE 才是可双击的 .app，Windows 的 onefile EXE 没这一步

用法：
  venv311/bin/python build_macos_app.py

输出：
  dist/Jietuba.app                （拖进 /Applications 即可，无签名，只在本机跑）
  dist/Jietuba.app/Contents/MacOS/models/   （OCR 模型，包内自带）

注意：这是本机自用的开发包，不是发行版（没签名没公证，拷到别的 Mac 上会被 Gatekeeper 拦）。
"""

import os
import shutil
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
#: 打包用图标（make_app_icons.py 生成；缺了就退回 PyInstaller 默认图标）
ICON_PATH = REPO_DIR / "build" / "icons" / "jietuba.icns"
APP_NAME = "Jietuba"
BUNDLE_ID = "com.jietuba.app"
MAIN_APP = "main/main_app.py"
DIST_DIR = REPO_DIR / "dist"
BUILD_DIR = REPO_DIR / "build"

# PySide6 的 Python 绑定 + 用到了动态导入的包（mss.factory 在函数里按平台 import backend，
# pynput 同理；不列出来的话 PyInstaller 认不出那些分支）
HIDDEN_IMPORTS = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtXml",
    # 网络代理是进程级的（core/net.py 用 QNetworkProxy）：这个模块原来在下面 EXCLUDES 里，
    # 而 exclude 优先级高于 hidden-import，缺了它打包版会在 setup_tray 抛 ModuleNotFoundError
    # （静默异常），表现成「代理设置不生效」。要一起把它从 EXCLUDES 里删掉，别只加一处。
    "PySide6.QtNetwork",
    # 网络代理是进程级的（core/net.py 用 QNetworkProxy），PyInstaller 的 PySide6 hook
    # 不会自动带上它：缺了以后打包版会在 setup_tray 里抛 ModuleNotFoundError（静默异常），
    # 表现成「代理设置不生效」
    "pyclipboard",
    "longstitch",
    "gifrecorder",
    "ppocr_rust",
    "zxingcpp",
    "PIL",
    "PIL.Image",
    "mss",
    "mss.darwin",
    "mss.base",
    "mss.exception",
    "mss.factory",
    "mss.models",
    "mss.screenshot",
    "mss.tools",
    "pynput",
    "pynput.keyboard._darwin",
    "pynput.mouse._darwin",
    "objc",
    "Cocoa",
    "AppKit",
    "Quartz",
    "darkdetect",
]

# 排除的模块：和 Windows 那份保持一致，主要为了别把 QtWebEngine 之类的整套 Qt 拖进来
EXCLUDES = [
    "matplotlib",
    "scipy",
    "numpy",
    "pandas",
    "torch",
    "tensorflow",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "tkinter",
    "pytest",
    "IPython",
    "jupyter",
    "rapidocr",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "keyboard",
    "av",
    "windows_media_ocr",
    "pythoncom",
    "win32com",
    "win32com.client",
    "win32com.server",
    "win32com.gen_py",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickTest",
    "PySide6.QtSql",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPrintSupport",
    "PySide6.QtHelp",
    "PySide6.QtUiTools",
    "PySide6.QtDesigner",
    "PySide6.QtTest",
    "PySide6.QtConcurrent",
    "PySide6.QtDBus",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQuick3D",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
]

MODEL_FILES = ["PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"]
#: 公式识别模型目录（可选）：用户在 models/formula/ 放了 PP-FormulaNet 就一起打进去。
#: 没放也不影响打包 —— 程序会按「没有本地公式引擎」降级，用户仍可选外部服务。
FORMULA_MODEL_DIR = "formula"


def copy_models(app_path: Path) -> None:
    """把 models/ 放进 Contents/MacOS/ —— 打包后 _ppocr_model_paths() 找的正是
    dirname(sys.executable)/models，在 .app 里就是这一层。"""
    dst = app_path / "Contents" / "MacOS" / "models"
    dst.mkdir(parents=True, exist_ok=True)
    for fn in MODEL_FILES:
        src = REPO_DIR / "models" / fn
        if src.exists():
            shutil.copy2(src, dst / fn)
            print(f"已外置模型: {fn}")
        else:
            print(f"警告: 缺少模型 {src}")

    formula_src = REPO_DIR / "models" / FORMULA_MODEL_DIR
    if formula_src.is_dir():
        shutil.copytree(formula_src, dst / FORMULA_MODEL_DIR, dirs_exist_ok=True)
        files = [p for p in formula_src.iterdir() if p.is_file()]
        size_mb = sum(p.stat().st_size for p in files) / 1e6
        print(f"已外置公式模型: {FORMULA_MODEL_DIR}/（{len(files)} 个文件，{size_mb:.1f} MB）")
    else:
        print(f"提示: 没有 models/{FORMULA_MODEL_DIR}/，包内本地公式识别不可用（仍可选外部服务）")


def main() -> int:
    if sys.platform != "darwin":
        print("这个脚本只用于 macOS；Windows 发行包请用 build_with_ocr_onefile.py")
        return 1

    os.chdir(REPO_DIR)
    import PyInstaller.__main__

    # 不写 spec 文件：onedir + --windowed 在 macOS 上由 PyInstaller 自己加 BUNDLE 步骤，
    # 生成的 spec 丢进 build/ 就行，仓库根不用多一个要同步维护的文件。
    args = [
        MAIN_APP,
        "--name", APP_NAME,
        "--windowed",
        "--noconfirm",
        "--paths", "main",
        "--osx-bundle-identifier", BUNDLE_ID,
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(BUILD_DIR),
        # 必须绝对路径：spec 生成在 build/ 下，相对路径会跟着 spec 目录解析
        "--add-data", f"{REPO_DIR / 'svg'}:svg",
        # 品牌图标：由 main/scripts/make_app_icons.py 从 svg/品牌.svg 生成
        *(["--icon", str(ICON_PATH)] if ICON_PATH.exists() else []),
        "--add-data", f"{REPO_DIR / 'main' / 'translations'}:translations",
    ]
    for module in HIDDEN_IMPORTS:
        args += ["--hidden-import", module]
    for module in EXCLUDES:
        args += ["--exclude-module", module]

    print(f"开始打包 {APP_NAME}.app（onedir，macOS 本地自用）")
    PyInstaller.__main__.run(args)

    app_path = DIST_DIR / f"{APP_NAME}.app"
    if not app_path.exists():
        print(f"打包失败：没有生成 {app_path}")
        return 1
    copy_models(app_path)

    print(f"""
打包完成：{app_path}

安装：
    cp -R "{app_path}" /Applications/          # 或直接拖进「应用程序」

首次运行要给的权限（系统设置 → 隐私与安全性）：
    - 屏幕录制：截图 / 窗口识别
    - 辅助功能：全局热键（pynput 键盘监听）
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
