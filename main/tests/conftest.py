# -*- coding: utf-8 -*-
"""
测试共享 fixtures

提供 QApplication 实例等公共 fixture。
"""
import pytest
import sys
import os

# Qt tests create and show real widgets. Keep them off the desktop when the
# suite is launched locally, while preserving an explicitly selected platform.
#
# macOS 例外：qframelesswindow 的 macOS 后端要真实 NSWindow，offscreen 下构造无边框
# 窗口会直接把进程打崩（段错误，不是断言失败）。开发机上跑测试时窗口闪一下可以接受，
# CI 在 Windows 上跑，不受影响。
if sys.platform != "darwin":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 确保 main/ 在 sys.path（从 tests/ 向上两级）
main_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if main_dir not in sys.path:
    sys.path.insert(0, main_dir)

# 项目根目录也加入（用于 Rust 库等）
project_root = os.path.dirname(main_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# tests/ 自身也加入：测试之间共享的助手（platform_guard 等）需要可导入。
# pytest 不会自动加这个目录——它只把 rootdir 插入 sys.path，而本仓库的 rootdir 是
# tests/ 上一层的配置位置，实测拿不到。
tests_dir = os.path.dirname(os.path.abspath(__file__))
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)


@pytest.fixture(scope="session")
def qapp():
    """提供一个全局 QApplication 实例（整个测试会话复用）"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _live_graphics_scenes() -> list:
    """当前存活的 QGraphicsScene（数量很少，扫描堆的开销可接受）。"""
    import gc

    from PySide6.QtWidgets import QGraphicsScene

    return [o for o in gc.get_objects() if isinstance(o, QGraphicsScene)]


def _is_cross_test_state(obj) -> bool:
    """对象是否被测试模块或 pytest 的 fixture 缓存持有。

    这种是跨用例共享的状态（例如某个模块级 fixture 建好的场景），销毁它会让后续用例
    拿到一个已失效的 C++ 对象；属于用例自己的对象则应该在这里被销毁。
    """
    import gc

    for ref in gc.get_referrers(obj):
        if isinstance(ref, type(os)):
            return True
        if type(ref).__module__.startswith("_pytest"):
            return True
    return False


@pytest.fixture(autouse=True)
def collect_qt_garbage_after_each_test():
    """每个用例结束后，先按 Qt 的顺序销毁它创建的图形对象，再回收一次。

    这是 macOS 上「pytest 退出前随机崩在某个测试文件」的根因对策。症状是 exit -11，
    且崩在哪个文件会漂移（同一份代码换个目录跑，崩溃率能从 100% 掉到 10%；甚至多定义
    三个测试函数就能把 0/5 变成 4/5），因此极容易被误判成某次改动引入的回归。系统崩溃
    报告给出的原生栈是：

        QUndoStack::clear() / ~QUndoStack()      ← KERN_INVALID_ADDRESS
        QObjectPrivate::deleteChildren() → ~QObject() → ~QGraphicsScene()
        SbkDeallocWrapperCommon → subtype_dealloc
        SbkObject_tp_clear → gc_collect_main     ← 由 Python 的循环 GC 触发

    机制：每个用例创建的 QGraphicsScene 及其子对象（QUndoStack、命令、图元）与视图、
    控制器之间构成引用环，只能由循环 GC 回收。GC 拆环时会先 ``tp_clear`` 掉环里
    Shiboken 包装对象的 Python 状态，而这些对象的释放又级联到 C++ 侧的父子析构
    （场景析构 → 删除子对象 QUndoStack），于是一个已经被释放/清空的对象被再次析构，
    访问到已回收的内存直接段错误。让 GC 去拆这种环本身就是不可靠的。

    这里改成在 Python 状态完整的时候用 ``shiboken6.delete`` 让 Qt 按自己的父子顺序
    销毁 C++ 对象，再回收纯 Python 的垃圾。两个细节是必须的：

    - ``shiboken6.isValid`` 判断：对已经被 Shiboken 释放过的对象再 delete 一次会直接崩
      （第一版就栽在这里，修法本身成了崩溃源）。
    - ``_is_cross_test_state``：不动被模块/fixture 持有的场景，那是跨用例共享的状态。

    只在 macOS 上启用：Windows CI 一直是绿的，没必要付这份开销、也不该改变那边的行为；
    这个隐患本身不是 macOS 专有的，将来若在别的平台看到同样的段错误，把平台判断去掉即可。
    """

    yield
    if sys.platform != "darwin":
        return

    import shiboken6
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        # 先处理挂起的 DeferredDelete，免得下面拿到已经排队的对象
        app.processEvents()

    candidates = [
        scene for scene in _live_graphics_scenes()
        if shiboken6.isValid(scene) and not _is_cross_test_state(scene)
    ]
    if candidates:
        for scene in candidates:
            scene.deleteLater()
        # deleteLater 要跑一轮事件循环才会真正析构
        if app is not None:
            app.processEvents()


@pytest.fixture(scope="session", autouse=True)
def isolated_tool_settings(tmp_path_factory):
    """让全局工具设置单例落在临时文件上，而不是开发机真实的配置。

    工具设置里有些键会改变行为本身，而不只是外观——马赛克的 draw_mode 就决定了
    同一串鼠标事件画出的是自由涂抹还是框选。只要测试读的是真实配置，用过一次
    "框选"的机器上整个马赛克测试文件就会挂，而 CI 的干净环境却全绿：这种失败
    最贵，因为它把"环境不同"伪装成"代码坏了"。

    这里用的是 get_tool_settings_manager 已有的注入口子（qsettings 参数只在首次
    创建单例时生效），所以必须抢在任何测试触碰单例之前把它建出来。
    """
    from PySide6.QtCore import QSettings
    from settings import tool_settings

    settings_file = str(tmp_path_factory.mktemp("settings") / "tool_settings.ini")
    previous = tool_settings._tool_settings_manager
    tool_settings._tool_settings_manager = None
    tool_settings.get_tool_settings_manager(
        QSettings(settings_file, QSettings.Format.IniFormat)
    )
    yield
    tool_settings._tool_settings_manager = previous


@pytest.fixture
def tmp_settings(tmp_path):
    """提供一个临时的 QSettings，避免污染真正的配置"""
    from PySide6.QtCore import QSettings
    settings_file = str(tmp_path / "test_settings.ini")
    return QSettings(settings_file, QSettings.Format.IniFormat)
 
