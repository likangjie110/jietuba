# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

截图吧（jietuba）：Windows x86_64 / ARM64 的截图 + 剪切板管理桌面应用。PySide6 做界面，Rust 扩展（`rust_libs/`）承担图像拼接、GIF 编码、剪贴板底层和 OCR。

- **发行版只有 Windows**，但目标结构是天然跨平台（Windows / macOS / Linux）：
  - **平台差异一律走 `main/core/platform/`**，业务模块不直接 `import win32gui` /
    `ctypes.windll` / 判断 `sys.platform`，也不拿 `IS_WINDOWS` 这类常量做分派。新增平台
    相关代码时不要写 `if sys.platform == "win32": ...`，而是往平台层补能力或后端；这两条
    由 `main/tests/test_platform_structure.py` 强制（第一道扫描带一份账本，现在只剩
    OCR 的 Windows 构建变体引擎一条）。
  - **macOS 上可以开发调试**（`setup_macos.sh` 准备环境，`setup_macos_signing.sh` 一次性
    准备打包用的签名身份）：截图、标注、钉图、OCR、
    翻译、全局热键、剪贴板历史、窗口智能选区、GIF 录制都能跑（Windows 走 Rust 的
    GDI 抓屏，其它平台由 Python 用 mss 抓帧后喂进同一个 Rust FrameStore）。
  - 窗口智能选区在 macOS 上走 Quartz（`core/platform/window_macos.py`）：
    `CGWindowListCopyWindowInfo` 天然按 Z 序返回、天然不含投影，命中逻辑与 Windows
    共用。读窗口标题需要屏幕录制权限，没授权时退化为应用名。
  - 截图时的「UI 检测」（设置项 `ui_detection`）分三档：`none` 不检测 / `window` 仅窗口 /
    `element` 检测元素（默认）。元素档目前**只有 macOS**（`AXUIElementCopyElementAtPosition`，
    需辅助功能权限；命中自身进程的元素时当作没元素、回退窗口档，否则框的是截图遮罩自己）；
    Windows 的 UIA 后端还没写，能力表 `UI_ELEMENT_DETECTION` 里登记为 NONE，默认档在
    Windows 上按窗口级工作并记一条日志。
  - 全局热键在非 Windows 上走 pynput 键盘监听，需要 macOS 的「辅助功能」权限：没授权时
    pynput 连事件 tap 都建不出来，监听器「启动成功」但一个事件都收不到——快捷键表现为
    完全没反应（`core/platform/hotkey.py` 负责查/请求权限，`shortcut_manager` 检测到缺失
    就弹系统对话框、记日志，并每 3 秒轮询，用户勾上后自动重建监听器，不用重启程序）。
  - 设置对话框里有「权限」页（`ui/settings_ui/page_permission.py`，清单来自
    `core/platform/permissions.py`）：页面可见时每秒重查一次状态，用户从系统设置勾完切回来
    就能看到「已授权」；每行有「去授权」（弹系统对话框）与「打开系统设置」（深链直达对应
    面板，系统对话框只在首次登记时弹一次）。屏幕录制那条若是**本次运行期间**才授权的，
    会显示「已授权，重启本程序后生效」（`pending_restart`）并多出一个「重启 jietuba」按钮
    ——这条权限是进程启动时读一次的，不把中间态说出来，用户只会觉得授权没用。
  - 权限清单渲染收在 `PermissionList`（同上文件），欢迎向导第 2 步
    （`ui/welcome/page_permission.py`）与设置页共用它；向导的步骤数按平台声明算，
    Windows/Linux 上没有这一步（仍是 6 步）。
  - 缺权限时不再只有日志：抓屏在 `MainApp._on_capture_ready`（主线程）、热键在
    `ShortcutManager` 的权限轮询里经 `HotkeySystem.permission_missing` 信号，各自叫一次
    `ui/permission_prompt.py`；每条权限每进程只提示一次（抓屏是高频动作），弹窗上
    「打开权限设置」经 `ui/permission_actions.py` 打开设置并跳到权限页。
  - 「重启 jietuba」不能用 `exec` 内层二进制：TCC 按 bundle 的代码签名认应用，那样起来的
    副本对不上辅助功能/录屏里的授权条目。`relaunch_application()` 让一个脱离父进程的 shell
    等本进程 pid 消失后 `open -a "<bundle>"`（等 pid 是因为新实例带单实例检查，本进程还
    活着时它一启动就会自己退出）。源码运行则重开同一个解释器和 argv。
  - macOS 的 TCC 是按**代码签名**认应用的。`dist/Jietuba.app` 现在由
    `build_macos_app.py` 用本机自签名身份签（身份由 `./setup_macos_signing.sh` 一次性创建，
    存在 `~/Library/Keychains/jietuba-signing.keychain-db`）：designated requirement 是
    `identifier + certificate leaf`，只要证书不变，重新打包不会让屏幕录制/辅助功能的授权
    失效。**别再退回 ad-hoc 签名**——ad-hoc 的 requirement 是 cdhash，重打包即失效，
    旧条目在系统设置里看着是勾上的、实际不生效；打包脚本会校验 requirement 并直接报错拦住。
  - 签名这件事上几个实测过的坑：`codesign --keychain <路径>` 在 macOS 26 上找不到身份
    （必须把该 keychain 放进用户 keychain 搜索列表且排在第一位）；身份放登录 keychain 会
    弹系统授权框（codesign 实际由 `com.apple.CodeSigningHelper` 代签，ACL 里的 codesign
    条目拦不住它，而 partition list 又必须先知道登录 keychain 密码才能改）；自签名证书不加
    `add-trusted-cert -p codeSign` 就是 `no identity found`，而这一步改的是系统信任设置，
    必然弹一次用户授权（脚本里只有这里是交互的，且只在首次创建时走）。keychain 空闲 5 分钟
    /睡眠后会自锁，所以打包脚本每次签名前先 `security unlock-keychain -p ""`（空密码）。
  - OCR 模型由 `--add-data` 交给 PyInstaller 收集，落在 `Contents/Resources/models/`（数据区），
    打包后 `_MEIPASS/models`（= `Contents/Frameworks/models` 符号链接）正好命中代码里那条
    回退路径，所以**不要**改回打包后再拷 `Contents/MacOS/models`：那是代码区，codesign 要求
    目录里每个文件自带签名，`.onnx` 只能靠扩展属性承载 generic 签名，扩展属性一丢整包签名就
    失效，刚授的权也跟着作废。
  - pynput 的键盘监听线程一启动就用 ctypes 读一次当前键盘布局，那组 Carbon 输入源接口
    在本进程是前台应用时要求在主线程调用；在监听线程上调用会被系统断言打死（崩溃报告是
    `EXC_BREAKPOINT`/`SIGTRAP`，线程停在 ctypes 里，不是可捕获的异常）。因此所有键盘监听
    与按键注入入口都先经 `core/platform/pynput_macos.install()` 把这一步收口到主线程。
  - 剪贴板历史的 Rust 实现（`pyclipboard`）**在 macOS 上可用**：底层 `clipboard-rs`
    跨平台，crate 里的 `cfg(not(windows))` 分支只少了 Windows 专有优化和「来源应用」
    名称（UI 里那一行会空着）。
- Python 版本被 `pyproject.toml` 锁死在 `==3.11.*`。
- 在 macOS 上跑测试**不要设 `QT_QPA_PLATFORM=offscreen`**：`qframelesswindow` 的 macOS
  后端要真实 NSWindow，离屏平台下构造无边框窗口会直接段错误（整进程崩，不是断言失败）。
  conftest 已按平台处理。
- **macOS 上测试有已知的不稳定，别把它当成回归**：
  - pytest 会话拆除时会在强制 GC 里段错误（`_pytest/unraisableexception.py:gc_collect_harder`
    → Qt/ObjC 析构顺序），**崩在哪个文件会漂移**：同一份代码换一个目录跑，某个文件的
    崩溃率能从 100% 掉到 10%。实测过与代码无关（把同一份代码复制到干净目录对照即可），
    因此不要靠"某文件崩了"判断改动好坏。
  - `main/tests/conftest.py` 现在每个用例结束后按 Qt 的父子顺序销毁用例自己创建的
    `QGraphicsScene`（`shiboken6.isValid` 校验后 `deleteLater` + `processEvents`，被
    模块/fixture 持有的场景跳过），针对的正是上面那条「循环 GC 拆环时级联析构」的根因。
    段错误本来就是随机的，一次跑通不能证明它已根治；要判断有没有退化仍按下面的采样法。
  - 2026-09-17 复测（`venv311` 与全新安装的 venv 各跑一次整套，各约 21 分钟）：**没再出现
    拆除期段错误**，覆盖率 56%（棘轮 37% 通过）；但整套会话里**不是全绿**——
    `test_welcome_hotkey_page.py` 的两个焦点用例（`TestKeyboardHighlight`）两次都失败，
    单独跑这个文件是 13 passed；另一次还额外挂了 2 个 `test_handle_overlay.py` 的用例
    （历史账上就随机红）。那两条的症状是按键根本没到控件（断言时 `keyboard._pressed` 是
    空集）——`show()` 之后焦点没落到第一格，与逐文件跑的结果不同，属会话/环境相关，
    不是代码回归。
  - 所以回归判据仍看逐文件：`python main/scripts/run_tests_per_file.py out.json`（会对有
    失败的文件重复采样，区分「稳定红」与「随机红」），用 `--diff old.json new.json` 比较
    两次运行的**最好值**。今天逐文件 104 个文件全绿、无波动文件。
  - 那个逐用例销毁 Qt 对象的 fixture 不是万能药：把不带它的 conftest 放进干净副本跑
    `test_handle_overlay.py` 同样是 40/40，可见那几条红与目录/用例组合相关。

## 常用命令

```bash
# 环境（Windows）
py -3.11 -m venv venv311 && call venv311\Scripts\activate.bat
python -m pip install -r requirements-dev.txt      # 含 requirements.txt

# 运行（必须 cd 进 main/）
cd main && python main_app.py

# 测试（在仓库根目录跑，.coveragerc 的相对路径依赖这一点）
python -m pytest main/tests -c main/tests/pytest.ini
python -m pytest main/tests/test_undo_stack.py::TestCommandUndoStack -c main/tests/pytest.ini   # 单个测试

# 覆盖率：CI 用总量棘轮（只升不降）+ PR 增量门槛（改动行 80%）
pytest main/tests -c main/tests/pytest.ini --cov --cov-config=.coveragerc --cov-fail-under=37

ruff check main                                     # 规则集 E9/F/B，当前零告警

# Rust 扩展
cargo test --locked --workspace --release --manifest-path rust_libs/Cargo.toml
maturin build --locked --release -m rust_libs/<crate>/Cargo.toml

python build_with_ocr_onefile.py                    # → dist/jietuba_pp.exe + dist/models/
python main/compile_translations.py                 # app_*.xml → app_*.qm（需 lrelease）
```

## 架构

### 导入约定（最容易踩的一条）

`main/` **不是包**（没有 `__init__.py`），它是 sys.path 的根。模块之间一律扁平绝对导入：`from core.logger import log_debug`、`from settings import get_tool_settings_manager`、`from capture.capture_service import CaptureService`。因此运行入口是 `cd main && python main_app.py`；测试从仓库根跑，靠 `main/tests/conftest.py` 把 `main/` 和仓库根塞进 `sys.path`。

`canvas` ↔ `tools` 存在循环依赖（`tools` 导入时会反向导入 `canvas.items`），所以 `tools` 只能在 `CanvasScene.__init__` 内部延迟导入——不要把它提到 `main/canvas/scene.py` 的模块顶层。

### 启动与生命周期

`main/main_app.py` 只管托盘图标、全局热键（`core/shortcut_manager.HotkeySystem`）和窗口生命周期，`__main__` 里交给 `core.bootstrap.run`。真正的启动在 `core/bootstrap.py` 的两阶段：

1. **Pre-Qt**：环境变量、DPI 感知、崩溃钩子、单实例检查、模块路径——必须在导入 PySide6 之前完成。
2. **Post-Qt**：`PreloadManager` 链式预加载 mss / canvas / tools / win32gui / OCR / 设置窗口 / 剪贴板，首次截图和首次打开各窗口的热路径因此不用等 import。

应用常驻托盘，窗口尽量复用而不是重建（`ScreenshotWindow` 等都有会话生命周期管理）。

### 模块与跨模块流

产品核心是「截图 ⇄ 剪切板 ⇄ 钉图」三向互通：截完可入剪切板 / 生成钉图，剪切板历史可反手生成钉图，钉图可再编辑、重 OCR、翻译。改这三者中任何一个的入口或数据模型，都要顺着另外两边看一眼调用方。

- `capture/` 抓屏与窗口识别 → `canvas/` + `tools/` 标注编辑 → `core/save.py`、`core/export.py` 导出。
- `canvas/` 是编辑核心：QGraphicsScene + `CommandUndoStack`（QUndoStack 封装）+ `SelectionModel`；每个绘图工具是 `tools/base.Tool` 的子类，注册进 `ToolController` 后由它分发鼠标事件，依赖通过 `ToolContext` 注入。
- `pin/`：`PinManager` 单例管理所有置顶钉图窗口。
- `clipboard/`：四层结构 `controllers/`（交互）→ `core/`（`ClipboardManager` 存储与监听，底层是 Rust `pyclipboard`）→ `services/`（文件 payload、分组、导入导出）→ `ui/`。
- `gif/`：状态机（选区 → 录制 → 回放 → 导出）+ 三层窗口，编码走 `gifrecorder`，抓帧后端由 `core/platform/capture.py` 提供。
- `stitch/` 长截图调 Rust `longstitch`；`ocr/` 用 `ppocr_rust`（单例、异步线程，模型在 `models/`）；`barcode/` 用 zxing-cpp。
- `core/actions.py` 是「应用里能做哪些事」的唯一出处（动作 id / 名称 / 静默截图 / 编辑器模式 / 托盘默认值）：全局鼠标动作、快捷键/动作页、托盘菜单都读它。某个动作的全局热键与「是否显示在托盘」存在 `app/action_hotkeys`、`app/action_tray`（旧键 `app/hotkey`、`clipboard/hotkey`、`app/translation_hotkey` 等六个只在首次读表时迁移，之后 `get_hotkey()` 一类读写器都只是这张表的视图）。
- `translation/` 是可插拔 provider 架构（`provider.py` 契约 + `registry.py` 注册 + `service.py` 编排，DeepL/Google/Azure/Amazon 四个实现）。
- `ui/fluent_lite/` 是自研的 Fluent 风格组件库，`ui/settings_ui/` 是设置对话框的各页面；通用样式和配色走 `ui/fluent_lite/theme.py` 与 `core/theme.py`。

### 平台层（`main/core/platform/`）

应用与「平台差异」之间的唯一接口。业务模块问能力，不问「是不是 Windows」。

| 模块 | 负责 |
|---|---|
| `detection.py` | 全项目唯一的平台探测点（`IS_WINDOWS`/`IS_MACOS`/`IS_LINUX`、Qt 平台插件名） |
| `capabilities.py` | 能力支持矩阵：`support()` / `available()` / `backend_name()`，三态（完整/降级/无实现） |
| `contracts.py` | 跨后端共用的数据模型（`WindowInfo`），单独放以免后端与门面互相导入成环 |
| `paths.py` | 应用数据目录、日志目录、默认截图与桌面目录 |
| `fonts.py` | QSS 字体栈与 QFont 字体族（按语言） |
| `shell.py` | 用系统默认程序打开文件/文件夹/URL（URL 用于跳系统设置面板）、在文件管理器定位、桌面快捷方式 |
| `startup.py` | 开机自启（注册表 HKCU\Run） |
| `process.py` | 工作集回收、进程身份/终止、DPI 感知、任务栏 AppUserModelID、重开本程序（macOS 打包版走 `open -a` 让 LaunchServices 重开 .app，见下） |
| `pointer.py` | 光标位置、鼠标键状态、全局滚轮监听、横向滚动与复制快捷键注入 |
| `window_ops.py` | 置顶、鼠标穿透、从截图排除、任务栏图标 |
| `window.py`（+ `window_win32.py` / `window_macos.py`） | 窗口枚举与 Z 序命中：Windows 走 `EnumWindows` + DWM 去阴影，macOS 走 Quartz；另有元素级命中（macOS 的 AX） |
| `hotkey.py`（+ `hotkey_win32.py`） | 全局热键：Windows 用 `RegisterHotKey` + `WM_HOTKEY` 过滤器与侧键钩子，其它平台用 pynput 监听；热键字符串解析三平台共用 |
| `permissions.py` | 系统权限清单：当前平台要哪些授权、怎么查/怎么要、跳到系统设置哪个面板（macOS 两条：辅助功能、屏幕录制）。清单为空即「这个平台没有系统门槛」，界面按空表不显示 |
| `clipboard.py` | 剪贴板写图（Windows CF_DIBV5 + 注册 "PNG" 格式，其它平台退 Qt ）、预热 |
| `focus.py` | 前台窗口/应用的记录与切回（「粘贴回原程序」用），以及前台程序名（`foreground_app_name`，全局鼠标动作的「忽略程序列表」用；Linux 上无实现） |
| `capture.py`（+ `capture_mss.py`） | GIF 录制的抓帧后端：Windows 是 Rust 的 GDI BitBlt，其它平台是 mss 抓帧线程 |

约定：

- 包放在 `core/` 下而不是顶层 `platform/`：`main/` 会被插到 `sys.path` 最前，顶层建
  `platform` 包会把标准库同名模块整个遮掉。
- Windows 取值逐字保留。`fonts.py` 的三份字体栈、`window_ops.set_click_through` 的
  `layered`/`force_frame_change` 两个参数都是在保留历史差异——合并它们会改变 Windows
  上的实际渲染/窗口样式，而本机是 macOS、无法验证，要改先上 Windows 实机。
- 平台层在**函数内**延迟导入 `core.logger`：它被 `core.constants` 导入，而 constants 在
  logger 的依赖链上，模块级导入会成环（`test_platform_structure.py` 会拦）。
- 能力缺失要显式表达：返回 False/None 并记日志，不要靠 `except Exception` 吞掉
  「这个平台没有这个 API」。
- 允许在平台层内部按平台分派，业务模块里不允许。

迁移状态：窗口枚举、全局热键、剪贴板写图与粘贴注入、启动预热里的 pywin32 导入都已收进
平台层，`core/platform_utils.py`、`gif/click_through.py`、`capture/window_finder*.py`
已删除。`main/tests/test_platform_structure.py` 的账本因此只剩一条：`ocr/engines.py` 里
`windows_media_ocr` 的导入（构建变体专有，按 `is_available()` 能力登记，不是按平台分派）。

### Rust 扩展

`rust_libs/` 是 cargo workspace，四个 crate。**PyPI 发行名和 import 名不一样**：`j-gif`→`gifrecorder`、`j-stitch`→`longstitch`、`j-clipboard`→`pyclipboard`、`j-ppocr`→`ppocr_rust`（绑定均为 `abi3-py311`）。调用点统一用模块级 `try: import X` + `X_AVAILABLE` 标志做降级（`PYCLIPBOARD_AVAILABLE`、`_gifrecorder_available` 等），OCR 还有 `is_ocr_available()` 门禁——新增调用请沿用这个模式，别让缺失的扩展直接炸在 import 上。

## 约定

- **国际化**：界面字符串优先 `self.tr("文本")`（类名即翻译上下文），需要显式上下文时用 `core.i18n.make_tr("Ctx")`。翻译源在 `main/translations/app_{zh,en,ja,ko}.xml`（Qt .ts 格式），改完必须跑 `compile_translations.py` 重新生成 `.qm`。
- **日志**：用 `core.logger` 的 `log_debug/log_info/log_warning/log_exception`，不要 `except: pass`，异常走 `log_exception(e, T("操作名"))`。日志正文写中文，多语言靠 `core/logger.T("中文 {name}", name=...)`，对应英文模板加到 `core/log_translations/<包名>.py`（key 是中文模板原文，按源码目录分文件，别都堆到一个大字典里）。日志落在 `%LOCALAPPDATA%/Jietuba/Logs`。
- **配置**：`settings/tool_settings.py` 是配置中心（`DEFAULT_SETTINGS` 工具默认值 + `APP_DEFAULT_SETTINGS` 应用级设置），单例且支持注入 `QSettings` 做测试隔离。新增配置项加在这两个字典里，不要各处散读。
- **资源路径**：一律走 `core/resource_manager.ResourceManager`（它处理 PyInstaller `_MEIPASS` 和图标缓存），不要拼相对路径。
- 注释和文档写中文；日志、注释解释「为什么」而不是「做了什么」。
- 版本号在 `main/main_app.py` 的 `APP_VERSION`。
- 提交信息用 Conventional Commits 中文格式：`feat: 欢迎页、扫码、聚光灯等`、`fix(clipboard): release pin image memory promptly`、`release: prepare 1.9`。

## CI 与发布

- 主分支 `master2`（`master` 一并构建）；`ci.yml` 在 Windows x64 + ARM64 上跑 ruff、Rust 单测与 wheel 构建、完整测试套装，ARM64 额外跑真实的剪贴板监听基准。
- 打 `release-*` tag 触发 `build.yml`，产出 x64 / arm64 压缩包并发布到 Release。
- `publish-pypi.yml` 手动触发，把 `rust_libs/` 的四个包发到 PyPI / TestPyPI（细节见 `rust_libs/PUBLISHING.md`）。
