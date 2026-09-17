# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

截图吧（jietuba）：Windows x86_64 / ARM64 的截图 + 剪切板管理桌面应用。PySide6 做界面，Rust 扩展（`rust_libs/`）承担图像拼接、GIF 编码、剪贴板底层和 OCR。

- **发行版只有 Windows**，但目标结构是天然跨平台（Windows / macOS / Linux）：
  - **平台差异一律走 `main/core/platform/`**，业务模块不直接 `import win32gui` /
    `ctypes.windll` / 判断 `sys.platform`。新增平台相关代码时不要再写
    `if sys.platform == "win32": ...`，而是往平台层补能力或后端；这条由
    `main/tests/test_platform_structure.py` 强制（带一份尚未迁移的账本）。
  - **macOS 上可以开发调试**（`setup_macos.sh` 准备环境）：截图、标注、钉图、OCR、
    翻译、全局热键、剪贴板历史、窗口智能选区、GIF 录制都能跑（Windows 走 Rust 的
    GDI 抓屏，其它平台由 Python 用 mss 抓帧后喂进同一个 Rust FrameStore）。
  - 窗口智能选区在 macOS 上走 Quartz（`window_finder_macos.py`）：
    `CGWindowListCopyWindowInfo` 天然按 Z 序返回、天然不含投影，命中逻辑与 Windows
    共用。读窗口标题需要屏幕录制权限，没授权时退化为应用名。
  - 全局热键在非 Windows 上走 pynput 键盘监听，需要 macOS 的「辅助功能」权限，没授权
    时监听器活着但收不到事件——启动时会检测并写日志。
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
  - `test_handle_overlay.py` 的失败数在 6~14 之间随机浮动。
  - `test_welcome_hotkey_page.py` 有 2 个焦点相关用例长期失败（`show()` 后焦点没落到
    第一格）。
  - 用 `python main/scripts/run_tests_per_file.py out.json` 逐文件跑（会对有失败的文件
    重复采样，区分「稳定红」与「随机红」），用 `--diff old.json new.json` 比较两次运行
    的**最好值**判断有没有回归。

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
- `gif/`：状态机（选区 → 录制 → 回放 → 导出）+ 三层窗口，编码走 `gifrecorder`。
- `stitch/` 长截图调 Rust `longstitch`；`ocr/` 用 `ppocr_rust`（单例、异步线程，模型在 `models/`）；`barcode/` 用 zxing-cpp。
- `translation/` 是可插拔 provider 架构（`provider.py` 契约 + `registry.py` 注册 + `service.py` 编排，DeepL/Google/Azure/Amazon 四个实现）。
- `ui/fluent_lite/` 是自研的 Fluent 风格组件库，`ui/settings_ui/` 是设置对话框的各页面；通用样式和配色走 `ui/fluent_lite/theme.py` 与 `core/theme.py`。

### 平台层（`main/core/platform/`）

应用与「平台差异」之间的唯一接口。业务模块问能力，不问「是不是 Windows」。

| 模块 | 负责 |
|---|---|
| `detection.py` | 全项目唯一的平台探测点（`IS_WINDOWS`/`IS_MACOS`/`IS_LINUX`、Qt 平台插件名） |
| `capabilities.py` | 能力支持矩阵：`support()` / `available()` / `backend_name()`，三态（完整/降级/无实现） |
| `paths.py` | 应用数据目录、日志目录、默认截图与桌面目录 |
| `fonts.py` | QSS 字体栈与 QFont 字体族（按语言） |
| `shell.py` | 用系统默认程序打开文件/文件夹、在文件管理器定位、桌面快捷方式 |
| `startup.py` | 开机自启（注册表 HKCU\Run） |
| `process.py` | 工作集回收、进程身份/终止、DPI 感知、任务栏 AppUserModelID |
| `pointer.py` | 光标位置、鼠标键状态、全局滚轮监听、横向滚动与复制快捷键注入 |
| `window_ops.py` | 置顶、鼠标穿透、从截图排除、任务栏图标 |

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

尚未迁移（见 `main/tests/test_platform_structure.py` 的账本）：窗口枚举
（`capture/window_finder*.py`）、全局热键（`core/shortcut_manager.py`）、剪贴板写图与
粘贴注入（`core/clipboard_utils.py`、`clipboard/controllers/clipboard_controller.py`）、
启动预热里的 pywin32 导入（`core/bootstrap.py`）。

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
