# -*- coding: utf-8 -*-
"""
plugins.py - 外部 OCR 引擎的发现与注册

插件目录：打包后是 exe 同级的 plugins/（和 models/ 一个规矩），开发时是仓库根的 plugins/。
一个插件 = 该目录下一个可导入的顶层模块或包，按下面的约定暴露 register(manager)：

    # plugins/my_ocr.py  或  plugins/my_ocr/__init__.py
    from ocr.engine import OcrEngine

    class MyEngine(OcrEngine):
        name = "my_ocr"          # 引擎 id，set_ocr_engine() 用它
        label = "My OCR"

        def is_available(self) -> bool: ...
        def recognize(self, pixmap, return_format="dict"): ...

    def register(manager):
        manager.register_engine(MyEngine())

宿主在 OCRManager 构造时扫描插件目录，逐个加载并调用 register()，插件引擎随即出现在
get_available_engines() 里，和内建引擎完全等价。两条约定：**放对目录**、**有 register(manager)**；
名字以下划线或点开头的项会被跳过（本地私有文件不放插件）。插件的 name 与内建引擎相同时
会覆盖内建的那个。

为什么是目录扫描而不是 entry points：本应用主要靠 PyInstaller 单文件 exe 分发，用户拿到的
是一堆解压好的文件、不会 pip install 任何东西——只有目录扫描对"把包丢进 plugins/"这个动作生效。

插件是第三方代码：加载失败、没有 register、register 里抛异常，都只记一条日志然后跳过。
一个坏插件不能拖垮 OCR，更不能拖垮截图主流程。
"""
import importlib.util
import sys
from pathlib import Path
from typing import Iterator, List, Tuple

from core.logger import T

from .engine import ocr_log

PLUGIN_DIR_NAME = "plugins"


def plugins_dir() -> Path:
    """插件目录：打包后用 exe 同级目录，开发时用仓库根目录。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parents[2]   # main/ocr/plugins.py → 仓库根
    return base / PLUGIN_DIR_NAME


def _iter_plugin_entries(directory: Path) -> Iterator[Tuple[str, Path, bool]]:
    """产出 (模块名, 入口文件, 是否为包)。"""
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir():
            init = entry / "__init__.py"
            if init.is_file():
                yield entry.name, init, True
        elif entry.suffix == ".py":
            yield entry.stem, entry, False


def _load_module(name: str, path: Path, is_package: bool):
    """按文件路径加载插件模块。

    刻意不走 sys.path：插件目录若插在 sys.path 前面，一个碰巧叫 av.py 或 json.py 的
    插件就会把真依赖顶掉，整个程序跟着出问题。按路径加载、只把自己的名字登记进
    sys.modules，重名的模块跳过而不是覆盖。
    """
    spec = importlib.util.spec_from_file_location(
        name, path,
        submodule_search_locations=[str(path.parent)] if is_package else None,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # 包内相对导入依赖 sys.modules 里有这个名字
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)     # 加载失败的模块不留半个在 sys.modules 里
        raise
    return module


def discover_plugins(manager) -> List[str]:
    """扫描插件目录并注册其中的引擎，返回成功加载的插件名。"""
    directory = plugins_dir()
    if not directory.is_dir():
        return []
    try:
        entries = list(_iter_plugin_entries(directory))
    except OSError as e:
        ocr_log(T("扫描插件目录失败: {e}", e=e), "WARN")
        return []

    loaded = []
    for name, path, is_package in entries:
        if name in sys.modules:
            ocr_log(T("插件与已加载模块重名，跳过: {name}", name=name), "WARN")
            continue
        try:
            module = _load_module(name, path, is_package)
            register = getattr(module, "register", None)
            if not callable(register):
                ocr_log(T("插件缺少 register(manager)，跳过: {name}", name=name), "WARN")
                continue
            register(manager)
            loaded.append(name)
        except Exception as e:
            ocr_log(T("加载 OCR 插件失败: {name}: {e}", name=name, e=e), "WARN")

    if loaded:
        ocr_log(T("已加载 OCR 插件: {names}", names=", ".join(loaded)))
    return loaded
