"""
截图吧 - macOS 本地 .app 打包脚本

与 Windows 的 build_with_ocr_onefile.py 分开写，因为差异不止一个参数：
  - 那份 spec 里有一层 PySide6 二进制白名单，只认 qt6core.dll / *.pyd；macOS 的 Qt 是
    .framework/.dylib、绑定是 *.abi3.so，套用那份白名单会把整个 Qt 过滤光
  - 它的隐藏导入含 win32api / mss.windows，macOS 上没有这些模块
  - macOS 要 BUNDLE 才是可双击的 .app，Windows 的 onefile EXE 没这一步

用法：
  ./setup_macos_signing.sh          # 只需跑一次：建本机签名身份
  venv311/bin/python build_macos_app.py

输出：
  dist/Jietuba.app                            （拖进 /Applications 即可）
  dist/Jietuba.app/Contents/Resources/models/ （OCR 模型，由 PyInstaller 收集进包）

签名：用 `./setup_macos_signing.sh` 建的本机自签名身份签（见 SIGN_IDENTITY）。这不是
可选项：ad-hoc 签名的 designated requirement 是 cdhash，每次重打包都变，macOS 的屏幕
录制/辅助功能授权就会失效（「每次更新都要重新授权」）；自签名后 requirement 是
identifier + certificate leaf，重新打包不影响已有授权。

注意：这是本机自用的开发包，不是发行版（自签名，没过公证，拷到别的 Mac 上会被 Gatekeeper 拦）。
"""

import os
import subprocess
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

#: 代码签名身份，由 ./setup_macos_signing.sh 创建。它决定了 designated requirement 是
#: 「按证书识别」还是「按内容哈希」，也就是重新打包后系统授权会不会失效。
SIGN_IDENTITY = os.environ.get("JIETUBA_CODESIGN_IDENTITY", "Jietuba Local Signing")
#: 身份所在的 keychain：`security list-keychains -d user` 里必须能找到它（setup 脚本负责），
#: 签名前要解锁（它默认空闲后自动上锁）。
SIGNING_KEYCHAIN = Path(os.environ.get(
    "JIETUBA_SIGNING_KEYCHAIN",
    str(Path.home() / "Library" / "Keychains" / "jietuba-signing.keychain-db")))

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


def require_signing_identity() -> None:
    """打包前先确认签名身份可用，免得白等几分钟打包才在签名那步失败。"""
    if not SIGNING_KEYCHAIN.exists():
        raise RuntimeError(
            f"找不到签名 keychain: {SIGNING_KEYCHAIN}\n先跑一次 ./setup_macos_signing.sh")
    unlock = subprocess.run(["/usr/bin/security", "unlock-keychain", "-p", "", str(SIGNING_KEYCHAIN)],
                            capture_output=True, text=True)
    if unlock.returncode != 0:
        raise RuntimeError(f"解锁签名 keychain 失败: {SIGNING_KEYCHAIN}\n{unlock.stderr.strip()}")
    found = subprocess.run(["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
                           capture_output=True, text=True)
    if f'"{SIGN_IDENTITY}"' not in found.stdout:
        raise RuntimeError(
            f"没有可用的签名身份「{SIGN_IDENTITY}」。先跑一次 ./setup_macos_signing.sh"
            "（若系统里存在同名但不受信的证书，也要重新跑它补信任设置）")


def model_data_args() -> list:
    """让 PyInstaller 收集 models/（--add-data），而不是打包完再往包里拷。

    这些文件会落在 Contents/Resources/models/（数据区），而 sys._MEIPASS 是
    Contents/Frameworks —— PyInstaller 在那里建了同名符号链接，所以正好命中代码里
    `_MEIPASS/models` 这条回退路径（ocr/engines.py、ocr/model_tiers.py、
    ocr/formula_engines.py 三处共用同一套候选目录规则）。

    以前是打包后拷到 Contents/MacOS/models：那是**代码区**，codesign 要求目录里每个
    文件都有自己的签名，.onnx 只能用扩展属性承载 generic 签名；扩展属性在拷贝中一丢，
    整包签名就失效，TCC 里刚授的权也跟着作废。放进数据区则由普通资源封印覆盖，
    交给 PyInstaller 一次签完。
    """
    args = []
    for fn in MODEL_FILES:
        src = REPO_DIR / "models" / fn
        if src.exists():
            args += ["--add-data", f"{src}:models"]
        else:
            print(f"警告: 缺少模型 {src}")

    formula_src = REPO_DIR / "models" / FORMULA_MODEL_DIR
    if formula_src.is_dir():
        args += ["--add-data", f"{formula_src}:models/{FORMULA_MODEL_DIR}"]
        files = [p for p in formula_src.iterdir() if p.is_file()]
        size_mb = sum(p.stat().st_size for p in files) / 1e6
        print(f"公式模型随包收集: {FORMULA_MODEL_DIR}/（{len(files)} 个文件，{size_mb:.1f} MB）")
    else:
        print(f"提示: 没有 models/{FORMULA_MODEL_DIR}/，包内本地公式识别不可用（仍可选外部服务）")
    return args


#: Mach-O 魔数（含 universal/fat）。0xcafebabe 与 Java .class 重名，靠后面的架构个数区分。
_MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf",
}


def _is_mach_o(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    if len(head) < 4 or head[:4] not in _MACHO_MAGICS:
        return False
    if head[:4] in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        return len(head) == 8 and 1 <= int.from_bytes(head[4:8], "big") <= 20
    return True


def _codesign(target: Path) -> None:
    # --timestamp=none：自签名证书拿不到 Apple 时间戳，带上它 codesign 会去连时间戳
    # 服务并长时间卡住（实测）
    result = subprocess.run(
        ["/usr/bin/codesign", "--force", "--timestamp=none", "--sign", SIGN_IDENTITY, str(target)],
        capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"签名失败 {target}:\n{result.stdout}{result.stderr}")


def sign_app(app_path: Path) -> None:
    """由内到外签名：先包内每个 Mach-O，再 bundle 本身。

    不用 `codesign --deep`：Apple 已不拿它做签名用途，逐个签的可控性更好，出错能指名
    是哪个文件。PyInstaller 留下的 ad-hoc 签名不必先移除，--force 会原地替换。
    """
    signed = 0
    for path in sorted(app_path.rglob("*")):
        if path.is_symlink() or not path.is_file() or not _is_mach_o(path):
            continue
        _codesign(path)
        signed += 1
    _codesign(app_path)
    print(f"已用「{SIGN_IDENTITY}」签名: {signed} 个包内二进制 + bundle 本身")


def verify_signature(app_path: Path) -> None:
    """校验签名有效，且 requirement 是按证书识别的（不是 cdhash）。

    这条断言是这个脚本存在的理由：一旦退回 ad-hoc 签名，本机授权就会在每次重打包后
    失效，而失败现象要等到用户下次截图才暴露，所以在这里就拦住。
    """
    verify = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app_path)],
                            capture_output=True, text=True, timeout=600)
    if verify.returncode != 0:
        raise RuntimeError(f"签名校验失败:\n{verify.stdout}{verify.stderr}")

    shown = subprocess.run(["/usr/bin/codesign", "-d", "-r-", str(app_path)],
                           capture_output=True, text=True, timeout=120)
    # codesign -d 的输出分在两条流上：requirement 走 stdout，Executable= 走 stderr
    requirement = ""
    for line in (shown.stdout + shown.stderr).splitlines():
        if "=>" in line:
            requirement = line.split("=>", 1)[1].strip()
    if "certificate leaf" not in requirement:
        raise RuntimeError(
            f"签名不是按证书识别的（{requirement or '读不到 designated requirement'}），"
            "重新打包后系统授权仍会失效；先跑 ./setup_macos_signing.sh")
    print(f"签名校验通过: {requirement}")


def main() -> int:
    if sys.platform != "darwin":
        print("这个脚本只用于 macOS；Windows 发行包请用 build_with_ocr_onefile.py")
        return 1

    os.chdir(REPO_DIR)
    import PyInstaller.__main__

    try:
        require_signing_identity()
    except RuntimeError as e:
        print(e)
        return 1

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
        *model_data_args(),
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

    # 签名放在打包之后：PyInstaller 只用 ad-hoc，本机授权要靠这份自签名才稳得住
    try:
        sign_app(app_path)
        verify_signature(app_path)
    except RuntimeError as e:
        print(f"\n{e}")
        return 1

    print(f"""
打包完成：{app_path}

安装：
    cp -R "{app_path}" /Applications/          # 或直接拖进「应用程序」

首次运行要给的权限（系统设置 → 隐私与安全性）：
    - 屏幕录制：截图 / 窗口识别
    - 辅助功能：全局热键（pynput 键盘监听）

授权只需给一次：这个包是用本机自签名身份签的，重新打包后授权继续有效。
（若系统设置里还留着旧 ad-hoc 时代那条失效的 jietuba 条目，删掉它即可。）
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
