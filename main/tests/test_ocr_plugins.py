# -*- coding: utf-8 -*-
"""
外部 OCR 插件的发现与注册

plugins/ 目录里丢进去的第三方引擎要能被自动加载；同时一个坏插件不能连累别的插件，
更不能连累程序启动。这里用临时目录伪造插件目录，不碰真实的 plugins/。
"""
import sys

import pytest

from ocr import ocr_manager as om
from ocr import plugins as ocr_plugins


PLUGIN_TEMPLATE = """
from ocr.engine import OcrEngine, format_result


class DemoEngine(OcrEngine):
    name = "NAME"
    label = "Demo"

    def is_available(self):
        return True

    def recognize(self, pixmap, return_format="dict"):
        return format_result([[[[0, 0], [9, 0], [9, 5], [0, 5]], "hello", 0.9]], return_format, 0.0)


def register(manager):
    manager.register_engine(DemoEngine())
"""


def _plugin_source(name: str) -> str:
    return PLUGIN_TEMPLATE.replace("NAME", name)


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    """插件目录指向临时目录；结束后把测试加载进来的模块从 sys.modules 清掉。"""
    before = set(sys.modules)
    monkeypatch.setattr(ocr_plugins, "plugins_dir", lambda: tmp_path)
    yield tmp_path
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


@pytest.fixture
def manager(monkeypatch):
    """干净的 OCRManager 实例，注册表清空（内建引擎的可用性随环境变化）。"""
    monkeypatch.setattr(om.OCRManager, "_instance", None, raising=False)
    monkeypatch.setattr(om.OCRManager, "_initialized", False, raising=False)
    mgr = om.OCRManager()
    mgr._engines.clear()
    return mgr


class TestDiscovery:

    def test_single_file_plugin_is_registered(self, plugin_dir, manager):
        (plugin_dir / "demo_engine.py").write_text(_plugin_source("demo_engine"))

        assert ocr_plugins.discover_plugins(manager) == ["demo_engine"]
        assert manager.get_available_engines() == ["demo_engine"]
        assert manager.set_engine("demo_engine") is True

    def test_package_plugin_can_use_relative_imports(self, plugin_dir, manager):
        """包形式的插件要支持相对导入——插件自己拆成几个文件是很自然的事。"""
        pkg = plugin_dir / "demo_pkg"
        pkg.mkdir()
        (pkg / "engine_impl.py").write_text(_plugin_source("demo_pkg"))
        (pkg / "__init__.py").write_text("from .engine_impl import register\n")

        assert ocr_plugins.discover_plugins(manager) == ["demo_pkg"]
        assert "demo_pkg" in manager.get_available_engines()

    def test_missing_plugin_dir_is_not_an_error(self, tmp_path, manager, monkeypatch):
        monkeypatch.setattr(ocr_plugins, "plugins_dir", lambda: tmp_path / "nope")
        assert ocr_plugins.discover_plugins(manager) == []

    def test_private_and_non_python_entries_are_skipped(self, plugin_dir, manager):
        (plugin_dir / "README.md").write_text("说明文档不是插件")
        (plugin_dir / "_scratch.py").write_text("raise RuntimeError('不该被加载')")
        (plugin_dir / "demo_engine.py").write_text(_plugin_source("demo_engine"))

        assert ocr_plugins.discover_plugins(manager) == ["demo_engine"]

    def test_plugin_without_register_is_skipped(self, plugin_dir, manager):
        (plugin_dir / "no_register.py").write_text("x = 1\n")

        assert ocr_plugins.discover_plugins(manager) == []
        assert manager.get_available_engines() == []

    def test_broken_plugin_does_not_block_the_next_one(self, plugin_dir, manager):
        (plugin_dir / "a_broken.py").write_text("raise RuntimeError('炸了')")
        (plugin_dir / "b_ok.py").write_text(_plugin_source("b_ok"))

        assert ocr_plugins.discover_plugins(manager) == ["b_ok"]

    def test_failing_register_is_contained(self, plugin_dir, manager):
        (plugin_dir / "c_bad_register.py").write_text(
            "def register(manager):\n    raise RuntimeError('注册失败')\n")
        (plugin_dir / "d_ok.py").write_text(_plugin_source("d_ok"))

        assert ocr_plugins.discover_plugins(manager) == ["d_ok"]

    def test_name_colliding_with_loaded_module_is_skipped(self, plugin_dir, manager):
        """插件名撞上已加载的模块时跳过：插件目录不进 sys.path 就是为了防这个。"""
        (plugin_dir / "sys.py").write_text(_plugin_source("sys"))

        assert ocr_plugins.discover_plugins(manager) == []
        assert sys.modules["sys"].__name__ == "sys"   # 真 sys 没被顶掉

    def test_failed_import_leaves_no_module_behind(self, plugin_dir, manager):
        (plugin_dir / "e_broken.py").write_text("import 不存在的模块\n")

        ocr_plugins.discover_plugins(manager)
        assert "e_broken" not in sys.modules
