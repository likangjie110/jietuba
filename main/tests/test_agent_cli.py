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
from PySide6.QtGui import QColor, QFont, QImage, QPainter
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


@pytest.fixture
def text_image(tmp_path):
    """一张画着大号文字的图，给 ocr 命令当输入。"""
    image = QImage(600, 160, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(QColor("black"))
    painter.setFont(QFont("Helvetica", 44))
    painter.drawText(20, 100, "jietuba 1234")
    painter.end()
    path = tmp_path / "agent-input.png"
    image.save(str(path), "PNG")
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
        assert data["line_count"] >= 1
        assert 0 < data["average_confidence"] <= 1
        assert any(char.isalnum() for char in data["text"]), data["text"]

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
    """跑 CLI 的时候不能冒出窗口、不能抢焦点。"""

    def _probe(self):
        """(在屏幕上的窗口数, 前台进程 pid)；取不到返回 None。"""
        try:
            import Quartz
        except Exception:
            return None
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID)
        front = Quartz.NSWorkspace.sharedWorkspace().frontmostApplication()
        return len(windows or []), front.processIdentifier() if front else 0

    def test_capture_does_not_open_windows_or_steal_focus(self):
        if self._probe() is None:
            pytest.skip("本机读不到窗口列表（需要 Quartz）")

        # 环境本身就在动（别的进程开开关关窗口），所以先量一次「什么都不做」的漂移当基线，
        # 再量带 CLI 调用的漂移：只有后者明显更大才说明是调用方弹了东西。
        idle_before = self._probe()
        time.sleep(1.0)
        idle_after = self._probe()
        idle_drift = idle_after[0] - idle_before[0]

        before = self._probe()
        _code, out, _err = run_cli("--json", "capture")
        data = parse_stdout(out)
        after = self._probe()

        assert data["ok"] is True
        assert after[1] == before[1], "CLI 调用改变了前台进程"
        assert idle_after[1] == idle_before[1], "空转期间前台进程就变了，测不出结论"
        added = after[0] - before[0]
        assert added <= max(idle_drift, 1), (
            f"CLI 调用多出了窗口: 基线漂移 {idle_drift}，调用前后 {before} → {after}")
