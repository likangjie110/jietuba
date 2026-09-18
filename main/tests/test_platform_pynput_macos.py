# -*- coding: utf-8 -*-
"""core/platform/pynput_macos 单元测试。

这个补丁防的是「进程被系统的队列断言打死」，单测里没法真的崩一次（崩了整进程就没了），
所以这里用假 pynput 模块钉住契约：

1. 主线程调用照旧走 pynput 的原实现，并把结果缓存下来；
2. 非主线程（pynput 的监听线程）只读缓存，**绝不碰原实现**——那一步正是崩进程的调用；
3. 缓存还没建立时给空值：不崩，也不静默（记一条日志）；
4. 两个模块名都要换：``pynput.keyboard._darwin`` 是按名字导入的，只改
   ``pynput._util.darwin`` 的话监听线程拿到的还是旧函数——崩溃就发生在那里。

原始实现是「装过一次就记下来」的，所以下面只重置「装没装过」与缓存，不清它。
"""

import contextlib
import sys
import threading
import types
from types import SimpleNamespace

import pytest

from core.platform import IS_MACOS, pynput_macos


class _Calls:
    """记录原实现被调用时的线程（True = 主线程）。"""

    def __init__(self):
        self.context = []
        self.unicode_map = []


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    monkeypatch.setattr(pynput_macos, "_installed", False)
    monkeypatch.setattr(pynput_macos, "_cached_context", None)
    monkeypatch.setattr(pynput_macos, "_cached_unicode_map", None)
    monkeypatch.setattr(pynput_macos, "_warned_off_main_thread", False)


@pytest.fixture
def as_macos(monkeypatch):
    monkeypatch.setattr(pynput_macos, "IS_MACOS", True)


@pytest.fixture
def fake_pynput(monkeypatch):
    """把假的 pynput（两个模块、同名函数）塞进 sys.modules。"""
    calls = _Calls()

    util_darwin = types.ModuleType("pynput._util.darwin")

    @contextlib.contextmanager
    def keycode_context():
        calls.context.append(threading.current_thread() is threading.main_thread())
        yield ("keyboard-type", b"layout-data")

    def get_unicode_to_keycode_map():
        calls.unicode_map.append(threading.current_thread() is threading.main_thread())
        # 与 pynput 的实现一致：从模块全局取 keycode_context，补丁换的就是这个名字
        with util_darwin.keycode_context() as context:
            return {context[0]: 1}

    util_darwin.keycode_context = keycode_context
    util_darwin.get_unicode_to_keycode_map = get_unicode_to_keycode_map

    util_module = types.ModuleType("pynput._util")
    util_module.darwin = util_darwin
    keyboard_module = types.ModuleType("pynput.keyboard")
    keyboard_darwin = types.ModuleType("pynput.keyboard._darwin")
    keyboard_module._darwin = keyboard_darwin
    pynput_module = types.ModuleType("pynput")
    pynput_module._util = util_module
    pynput_module.keyboard = keyboard_module

    for name, module in (
        ("pynput", pynput_module),
        ("pynput._util", util_module),
        ("pynput._util.darwin", util_darwin),
        ("pynput.keyboard", keyboard_module),
        ("pynput.keyboard._darwin", keyboard_darwin),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    return SimpleNamespace(calls=calls, util_darwin=util_darwin,
                           keyboard_darwin=keyboard_darwin)


def _run_in_worker_thread(func):
    """在一个真正的子线程里跑 func（pynput 的监听线程就是普通线程）。"""
    results = []
    thread = threading.Thread(target=lambda: results.append(func()))
    thread.start()
    thread.join()
    return results


def _enter_keycode_context(module):
    def run():
        with module.keycode_context() as context:
            return context

    return run


class TestInstall:
    def test_off_macos_is_a_noop(self, monkeypatch, fake_pynput):
        monkeypatch.setattr(pynput_macos, "IS_MACOS", False)
        assert pynput_macos.install() is False
        assert fake_pynput.util_darwin.keycode_context is not pynput_macos._main_thread_keycode_context

    def test_missing_pynput_is_reported_not_raised(self, as_macos, monkeypatch):
        monkeypatch.setitem(sys.modules, "pynput._util", None)
        assert pynput_macos.install() is False

    def test_both_module_names_are_rebound(self, as_macos, fake_pynput):
        """只换一个模块名是不够的：pynput.keyboard._darwin 拿的是自己那份名字。"""
        assert pynput_macos.install() is True
        for module in (fake_pynput.util_darwin, fake_pynput.keyboard_darwin):
            assert module.keycode_context is pynput_macos._main_thread_keycode_context
            assert module.get_unicode_to_keycode_map is pynput_macos._main_thread_unicode_map

    def test_install_is_idempotent(self, as_macos, fake_pynput):
        assert pynput_macos.install() is True
        patched = fake_pynput.util_darwin.keycode_context
        assert pynput_macos.install() is True
        assert fake_pynput.util_darwin.keycode_context is patched

    def test_reinstall_does_not_wrap_the_patch_itself(self, as_macos, fake_pynput):
        """状态被重置后再装一次，不能把补丁当原实现套一层（那会无限递归）。"""
        pynput_macos.install()
        original = pynput_macos._original_keycode_context

        pynput_macos._installed = False
        assert pynput_macos.install() is True

        assert pynput_macos._original_keycode_context is original


class TestMainThreadCalls:
    def test_uses_the_original_and_caches_it(self, as_macos, fake_pynput):
        pynput_macos.install()

        with fake_pynput.keyboard_darwin.keycode_context() as context:
            assert context == ("keyboard-type", b"layout-data")
        assert fake_pynput.calls.context == [True]

    def test_map_is_built_from_the_original(self, as_macos, fake_pynput):
        pynput_macos.install()

        assert fake_pynput.keyboard_darwin.get_unicode_to_keycode_map() == {"keyboard-type": 1}
        assert fake_pynput.calls.unicode_map == [True]
        # 建映射要先取上下文，也是主线程这一次
        assert fake_pynput.calls.context == [True]


class TestListenerThread:
    """pynput 的监听线程一进来就 ``with keycode_context()``（Listener._run）。"""

    def test_never_reaches_the_original_without_a_cache(self, as_macos, fake_pynput):
        pynput_macos.install()

        assert _run_in_worker_thread(
            _enter_keycode_context(fake_pynput.keyboard_darwin)) == [(None, None)]
        assert fake_pynput.calls.context == []

    def test_reuses_the_cached_context(self, as_macos, fake_pynput):
        pynput_macos.install()
        with fake_pynput.keyboard_darwin.keycode_context() as main_context:
            assert main_context is not None

        assert _run_in_worker_thread(
            _enter_keycode_context(fake_pynput.keyboard_darwin)) == [main_context]
        # 只有主线程那一次真的问了 Carbon
        assert fake_pynput.calls.context == [True]

    def test_map_is_empty_without_a_cache(self, as_macos, fake_pynput):
        pynput_macos.install()

        assert _run_in_worker_thread(
            fake_pynput.keyboard_darwin.get_unicode_to_keycode_map) == [{}]
        assert fake_pynput.calls.unicode_map == []

    def test_map_reuses_the_cached_one(self, as_macos, fake_pynput):
        pynput_macos.install()
        expected = fake_pynput.keyboard_darwin.get_unicode_to_keycode_map()

        assert _run_in_worker_thread(
            fake_pynput.keyboard_darwin.get_unicode_to_keycode_map) == [expected]
        assert fake_pynput.calls.unicode_map == [True]

    def test_no_cache_is_reported_once(self, as_macos, fake_pynput):
        pynput_macos.install()

        assert pynput_macos._warned_off_main_thread is False
        _run_in_worker_thread(_enter_keycode_context(fake_pynput.keyboard_darwin))
        assert pynput_macos._warned_off_main_thread is True


class TestRealPynputBinding:
    """真 pynput 的两个模块名确实换得动——假模块只验证了契约。"""

    @pytest.mark.skipif(not IS_MACOS, reason="pynput 的 Darwin 后端只在 macOS 上存在")
    def test_real_darwin_modules_are_rebound(self, monkeypatch):
        from pynput._util import darwin as util_darwin
        from pynput.keyboard import _darwin as keyboard_darwin

        # 装完由 monkeypatch 原样还原，别把补丁状态留给别的用例
        for module in (util_darwin, keyboard_darwin):
            for name in ("keycode_context", "get_unicode_to_keycode_map"):
                monkeypatch.setattr(module, name, getattr(module, name), raising=False)

        assert pynput_macos.install() is True
        assert util_darwin.keycode_context is pynput_macos._main_thread_keycode_context
        assert keyboard_darwin.keycode_context is pynput_macos._main_thread_keycode_context
        assert keyboard_darwin.get_unicode_to_keycode_map is pynput_macos._main_thread_unicode_map
