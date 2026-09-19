# -*- coding: utf-8 -*-
"""Agent 接口：``--json`` 命令行与本机 bridge。

命令行那几条是**真起进程**跑 `main_app.py --json ...`（不是直接调函数），断言标准输出能被
``json.loads`` 解析且字段内容正确——这是外部 Agent 唯一会看到的东西。bridge 用真的
QLocalServer/QLocalSocket 在同一进程里往返。

「不弹窗、不抢焦点」由真机探针断言：跑一次 CLI 前后比较**在屏幕上的窗口数**与**前台进程**，
两者都必须不变（这条在 macOS 上用 Quartz 读，取不到就跳过并说明）。
"""

import json
import os
import subprocess
import sys
import time

import pytest
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter
from PySide6.QtWidgets import QApplication

MAIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cli(*args, timeout=120):
    """真起一个进程跑 CLI，返回 (退出码, stdout, stderr)。"""
    process = subprocess.run(
        [sys.executable, "main_app.py", *args],
        cwd=MAIN_DIR, capture_output=True, text=True, timeout=timeout,
    )
    return process.returncode, process.stdout, process.stderr


def parse_stdout(stdout: str) -> dict:
    """标准输出必须是一行合法 JSON。"""
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, "命令行没有输出"
    return json.loads(lines[-1])


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _dark_pixels(image: QImage) -> int:
    return sum(1 for y in range(image.height()) for x in range(image.width())
               if image.pixelColor(x, y).lightness() < 128)


@pytest.fixture
def text_image(tmp_path):
    """一张**确认画上了字**的图，给 ocr 命令当输入。

    这一步带自检：字体族在本机解析不到时（换机器、字体缓存刚被清），画出来的会是一张
    白图，OCR 于是合法地返回 0 行——那样红的是测试的前提而不是产品，而且现象很难查。
    所以这里逐个试字体族，直到暗像素足够多；一个都画不出来就直接报清楚。
    """
    text = "jietuba 1234"
    rendered = None
    for family in ("Helvetica", "Arial", "Menlo", ".AppleSystemUIFont"):
        font = QFont(family, 44)
        metrics = QFontMetricsF(font)
        # 「暗像素够多」还不够：字体缺字形时会画出一堆豆腐块，像素一样多但认不出来。
        # 所以先问字体有没有这些字形，再渲染。
        if not all(metrics.inFont(char) for char in text if char.strip()):
            continue
        image = QImage(600, 160, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setPen(QColor("black"))
        painter.setFont(font)
        painter.drawText(20, 100, text)
        painter.end()
        if _dark_pixels(image) > 200:
            rendered = image
            break
    assert rendered is not None, "本机没有能渲染出这段文字的字体，OCR 用例的前提不成立"

    path = tmp_path / "agent-input.png"
    rendered.save(str(path), "PNG")
    return str(path)


class TestCliOutput:
    def test_status_is_parseable_json(self):
        code, out, _err = run_cli("--json", "status")

        assert code == 0
        data = parse_stdout(out)
        assert data["ok"] is True and data["command"] == "status"
        assert set(data["app"]["commands"]) == {"status", "capture", "ocr"}
        assert data["app"]["version"]

    def test_capture_writes_a_real_image(self, tmp_path):
        target = tmp_path / "shot.png"

        code, out, _err = run_cli("--json", "capture", "--out", str(target))

        data = parse_stdout(out)
        assert code == 0 and data["ok"] is True, data
        assert data["image"]["path"] == str(target)
        assert os.path.exists(target)
        saved = QImage(target)
        assert not saved.isNull()
        assert (saved.width(), saved.height()) == (data["image"]["width"],
                                                   data["image"]["height"])
        assert len(data["image"]["digest"]) == 16

    def test_capture_region_is_never_empty(self):
        """后端报 0×0 时要按图像尺寸兜底，不能让 JSON 自相矛盾。"""
        _code, out, _err = run_cli("--json", "capture")

        data = parse_stdout(out)
        assert data["ok"] is True
        _x, _y, width, height = data["image"]["region"]
        assert width == data["image"]["width"] and height == data["image"]["height"]

    def test_ocr_reads_the_given_image(self, text_image):
        code, out, _err = run_cli("--json", "ocr", "--from", text_image)

        data = parse_stdout(out)
        assert code == 0 and data["ok"] is True, data
        assert data["source"] == text_image
        assert data["line_count"] >= 1, data          # 失败时把整份 JSON 打出来便于归因
        assert 0 < data["average_confidence"] <= 1, data
        assert any(char.isalnum() for char in data["text"]), data

    def test_ocr_can_write_the_text_out(self, text_image, tmp_path):
        target = tmp_path / "text.txt"

        _code, out, _err = run_cli("--json", "ocr", "--from", text_image,
                                   "--text-out", str(target))

        data = parse_stdout(out)
        assert data["ok"] is True
        assert os.path.exists(target)
        assert open(target, encoding="utf-8").read() == data["text"]

    def test_ocr_without_a_source_says_what_to_do(self, tmp_path, monkeypatch):
        """没截过图又没给路径时要如实说明，而不是返回空文本。"""
        code, out, _err = run_cli("--json", "ocr")

        data = parse_stdout(out)
        if data["ok"]:                      # 之前截过图：走「最近一次截图」也合理
            assert data["source"]
        else:
            assert code == 1
            assert "截图" in data["error"] or "路径" in data["error"]

    def test_unknown_command_is_an_error_with_a_list(self):
        code, out, _err = run_cli("--json", "teleport")

        data = parse_stdout(out)
        assert code == 1 and data["ok"] is False
        assert data["available"] == ["status", "capture", "ocr"]

    def test_missing_command_is_a_usage_error(self):
        code, out, _err = run_cli("--json")

        data = parse_stdout(out)
        assert code == 2 and data["ok"] is False
        assert "缺少命令" in data["error"]


    def test_ocr_on_an_image_without_text_says_so(self, tmp_path):
        """识别成功但一行都没有时要说得明白：带上 note，而不是只给一串 0 让人猜。"""
        blank = QImage(320, 120, QImage.Format.Format_ARGB32)
        blank.fill(QColor("white"))
        path = tmp_path / "blank.png"
        blank.save(str(path), "PNG")

        code, out, _err = run_cli("--json", "ocr", "--from", str(path))

        data = parse_stdout(out)
        assert code == 0 and data["ok"] is True, data
        assert data["source"] == str(path)
        assert data["line_count"] == 0, data
        assert data["text"] == ""
        assert data.get("note"), f"零行结果没带 note: {data}"
        assert isinstance(data["note"], str)

    def test_the_zero_line_note_has_an_english_template(self):
        """note 用 T() 渲染，因此中英模板都要在——否则英文界面会露出半句中文。"""
        from core.log_translations import TRANSLATIONS

        assert TRANSLATIONS.get("没有识别到文字")


class TestCliParsing:
    """不走进程的解析层用例（覆盖进程内调用与参数裁剪）。"""

    def test_options_are_mapped_per_command(self):
        from agent import cli

        command, options, error = cli.parse_argv(
            ["--json", "ocr", "--from", "/tmp/a.png", "--text-out", "/tmp/b.txt"])

        assert (command, error) == ("ocr", "")
        assert options == {"path": "/tmp/a.png", "save_text": "/tmp/b.txt"}
        assert cli._clean_options("capture", options) == {"path": "/tmp/a.png"}
        assert cli._clean_options("ocr", options) == options
        assert cli._clean_options("status", options) == {}

    def test_missing_option_value_is_reported(self):
        from agent import cli

        _command, _options, error = cli.parse_argv(["--json", "capture", "--out"])

        assert "缺少值" in error

    def test_only_double_dash_json_counts_as_an_agent_call(self):
        from agent import cli

        assert cli.is_agent_invocation(["--json", "status"]) is True
        assert cli.is_agent_invocation([]) is False
        assert cli.is_agent_invocation(["main_app.py"]) is False

    def test_bridge_result_is_used_when_available(self, capsys):
        from agent import cli

        calls = []

        def fake_bridge(method, params):
            calls.append((method, params))
            return {"ok": True, "command": method, "via": "bridge"}

        code = cli.main(["--json", "status"], call_bridge=fake_bridge)

        assert code == 0
        assert calls == [("status", {})]
        assert json.loads(capsys.readouterr().out.strip())["via"] == "bridge"


class TestBridge:
    """真 QLocalServer + 真 QLocalSocket 往返。"""

    @pytest.fixture
    def bridge(self, qapp):
        from agent.bridge import AgentBridgeServer

        server = AgentBridgeServer()
        if not server.start():
            pytest.skip("本机起不了本地套接字")
        yield server
        server.stop()

    def test_round_trip_serves_a_real_command(self, bridge):
        from agent.bridge import call_bridge

        result = call_bridge("status", {}, timeout_ms=10000)

        assert result is not None and result["ok"] is True
        assert result["command"] == "status"

    def test_broken_json_is_reported(self, bridge, qapp):
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtNetwork import QLocalSocket

        from agent.bridge import socket_path

        socket = QLocalSocket()
        socket.connectToServer(socket_path())
        assert socket.waitForConnected(3000)

        socket.write(b"{not json")
        socket.flush()
        loop = QEventLoop()
        QTimer.singleShot(3000, loop.quit)
        socket.readyRead.connect(loop.quit)
        loop.exec()
        payload = json.loads(bytes(socket.readAll().data()).decode("utf-8"))

        assert payload["ok"] is False

    def test_without_a_server_the_client_returns_none(self, tmp_path, monkeypatch):
        from agent import bridge as bridge_module

        monkeypatch.setattr(bridge_module, "socket_path",
                            lambda: str(tmp_path / "missing.sock"))

        assert bridge_module.call_bridge("status", {}) is None


class TestNoWindowOrFocusSideEffects:
    """跑 CLI 的时候不能冒出窗口、不能抢焦点。

    判据是**这个进程自己**有没有开窗（按 pid 过滤窗口列表），以及前台应用有没有变——
    全局窗口数会被别的程序的开开关关搅动（本机实测过 30→28 这种噪声），拿它当判据只会得到
    随机的红。同一文件里还有一条**正对照**：真开一个窗口时探针必须看得见，否则「没检测到」
    说明不了任何事。
    """

    WINDOW_OWNER_POLL_S = 0.15

    def _windows_of(self, pid: int) -> list:
        """属于某个进程的窗口（本机 Quartz；取不到返回 None 表示探针不可用）。"""
        try:
            import Quartz
        except Exception:
            return None
        options = (Quartz.kCGWindowListOptionOnScreenOnly
                   | Quartz.kCGWindowListExcludeDesktopElements)
        windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
        return [win for win in windows if win.get("kCGWindowOwnerPID") == pid]

    def _frontmost_pid(self):
        try:
            import Quartz
        except Exception:
            return None
        app = Quartz.NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.processIdentifier() if app else None

    def _run_tracked(self, argv):
        """跑一个子进程，期间持续记录「它自己开的窗口」与前台应用的变化。"""
        process = subprocess.Popen([sys.executable, *argv], cwd=MAIN_DIR,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        front_before = self._frontmost_pid()
        owned = []
        fronts = {front_before}
        while process.poll() is None:
            found = self._windows_of(process.pid)
            if found:
                owned.extend(found)
            fronts.add(self._frontmost_pid())
            time.sleep(self.WINDOW_OWNER_POLL_S)
        out, err = process.communicate(timeout=30)
        fronts.add(self._frontmost_pid())
        return process.returncode, out, err, owned, fronts

    def test_the_probe_sees_a_window_when_one_really_opens(self):
        """正对照：探针必须能抓到「真开了一个窗口」，否则下面的绿灯不成立。"""
        if self._windows_of(os.getpid()) is None:
            pytest.skip("本机读不到窗口列表（需要 Quartz）")

        code = ("import time;"
                "from PySide6.QtWidgets import QApplication, QWidget;"
                "app = QApplication([]);"
                "w = QWidget(); w.resize(240, 140); w.show();"
                "app.processEvents();"
                "[app.processEvents() or time.sleep(0.05) for _ in range(40)]")

        _code, _out, _err, owned, _fronts = self._run_tracked(["-c", code])

        assert owned, "探针没看见明明已经打开的窗口：这条正对照失败，说明判据无效"

    def test_capture_does_not_open_windows_or_steal_focus(self):
        if self._windows_of(os.getpid()) is None:
            pytest.skip("本机读不到窗口列表（需要 Quartz）")

        code, out, _err, owned, fronts = self._run_tracked(["main_app.py", "--json", "capture"])
        data = parse_stdout(out)

        assert code == 0 and data["ok"] is True, data
        assert not owned, f"CLI 调用弹出了窗口: {owned}"
        assert len(fronts) == 1, f"CLI 调用期间前台应用变过: {fronts}"

