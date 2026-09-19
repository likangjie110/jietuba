# -*- coding: utf-8 -*-
"""本机 bridge：常驻应用开一个只服务当前用户的本地套接字，Agent 通过它复用应用内的能力。

为什么需要它：OCR 引擎、视觉模型配置、屏幕录制权限都在**已经跑起来的应用**里准备好了；
让 Agent 每次调用都另起一个进程去加载一遍模型，既慢又可能撞上权限/文件锁。所以常驻应用
监听一个本机套接字，CLI 优先走它；应用没在跑时 CLI 自己执行（返回结构完全相同）。

安全边界（照 Ta 的做法，也是唯一合理的做法）：套接字文件落在**当前用户的应用数据目录**里，
靠文件系统权限隔离；不做网络监听、不做远程访问、请求与响应里只走方法名与参数。
"""

import json
import os

from core.logger import T, log_debug, log_exception, log_warning

#: 套接字文件名（放在应用数据目录里，按用户隔离）
SOCKET_NAME = "agent.sock"

#: 单次调用的默认超时（毫秒）：识别与截图都可能在秒级
DEFAULT_TIMEOUT_MS = 30000


def socket_path() -> str:
    """套接字的完整路径；目录不存在时建出来。"""
    from core.platform.paths import app_data_dir

    folder = app_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / SOCKET_NAME)


class AgentBridgeServer:
    """常驻应用侧的服务端（QLocalServer，跑在 Qt 事件循环里）。"""

    def __init__(self, parent=None):
        self._server = None
        self._parent = parent
        self._listening = False

    @property
    def is_listening(self) -> bool:
        return self._listening

    def start(self) -> bool:
        """开始监听；端口被占（上一次没退干净）时先清掉再起。"""
        from PySide6.QtNetwork import QLocalServer

        path = socket_path()
        server = QLocalServer(self._parent)
        # 上一次异常退出会留下套接字文件，Qt 默认拒绝监听：先删掉自己的残留
        if os.path.exists(path):
            try:
                QLocalServer.removeServer(path)
            except Exception as e:
                log_exception(e, T("清理旧的 bridge 套接字"))
        if not server.listen(path):
            log_warning(T("Agent bridge 监听失败: {error}", error=server.errorString()), "Agent")
            return False
        server.newConnection.connect(self._on_connection)
        self._server = server
        self._listening = True
        log_debug(T("Agent bridge 已启动: {path}", path=path), "Agent")
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                from PySide6.QtNetwork import QLocalServer

                QLocalServer.removeServer(socket_path())
            except Exception:
                pass
            self._server = None
        self._listening = False

    def _on_connection(self) -> None:
        """读一行请求 → 执行 → 写一行响应；一个连接一次调用。"""
        server = self._server
        if server is None:
            return
        socket = server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda: self._handle(socket))
        socket.disconnected.connect(socket.deleteLater)

    def _handle(self, socket) -> None:
        from agent.commands import dumps, run_command

        raw = bytes(socket.readAll().data()).decode("utf-8", "replace").strip()
        if not raw:
            return
        request = {}
        try:
            request = json.loads(raw)
        except ValueError:
            socket.write(dumps({"ok": False, "error": T("请求不是合法 JSON").render()})
                         .encode("utf-8"))
            socket.flush()
            socket.disconnectFromServer()
            return
        method = str(request.get("method", "") or "")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        log_debug(T("Agent bridge 收到调用: {method}", method=method), "Agent")
        socket.write(dumps(run_command(method, params)).encode("utf-8"))
        socket.flush()
        socket.disconnectFromServer()


def call_bridge(method: str, params: dict | None = None,
                timeout_ms: int = DEFAULT_TIMEOUT_MS):
    """客户端：有常驻应用就返回结果字典，没在跑就返回 None（调用方自己执行）。

    用 QLocalSocket 而不是裸 socket：它跨平台（Windows 上走命名管道），且与 QLocalServer
    成对，不容易在路径格式上踩坑。
    """
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from PySide6.QtNetwork import QLocalSocket

    path = socket_path()
    if not os.path.exists(path):
        return None
    app = QCoreApplication.instance()
    if app is None:
        # 客户端也要有核心应用实例（事件循环与定时器依赖它）
        app = QCoreApplication([])

    socket = QLocalSocket()
    socket.connectToServer(path)
    if not socket.waitForConnected(500):
        log_debug(T("Agent bridge 连不上，改由本进程执行: {method}", method=method), "Agent")
        return None

    payload = json.dumps({"method": method, "params": params or {}},
                         ensure_ascii=False).encode("utf-8")
    socket.write(payload)
    socket.flush()

    loop = QEventLoop()
    buffer = {"data": b"", "done": False}

    def _read():
        buffer["data"] += bytes(socket.readAll().data())
        if buffer["data"]:
            buffer["done"] = True
            loop.quit()

    socket.readyRead.connect(_read)
    socket.disconnected.connect(loop.quit)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(int(timeout_ms))
    if not buffer["done"]:
        loop.exec()
    timer.stop()

    if not buffer["data"]:
        log_warning(T("Agent bridge 没有返回内容: {method}", method=method), "Agent")
        return None
    try:
        return json.loads(buffer["data"].decode("utf-8", "replace"))
    except ValueError as e:
        log_exception(e, T("解析 Agent bridge 响应"))
        return None
