# -*- coding: utf-8 -*-
"""视觉模型的四种协议与任务模板。

用本机 stub HTTP 服务驱动**真的** ``ocr/vision_models.py``：请求真的发出去、真的解析回来，
断言的是「服务端看到了什么」——端点路径、鉴权头、请求体形状、以及响应解析出的正文。
四种协议各走一次完整往返（不是只测构造函数），模板差别的判据是服务端收到的指令文本不同。

真实厂商（Azure / Anthropic / Google）的线上调用需要各自的付费凭据，本机没有，因此
「线上能否真的成功」不在本文件的覆盖范围——这里钉的是协议形状。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PySide6.QtGui import QColor, QImage

from ocr.vision_models import (
    PROTOCOLS,
    TASKS,
    VisionModel,
    build_headers,
    build_request_body,
    convert_image,
    instruction_for,
)


class _StubHandler(BaseHTTPRequestHandler):
    """记录每次请求（含查询串与头），按协议回一个能解析的响应。"""

    seen = []
    response = None

    def _handle(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) or b"{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"__raw__": raw.decode("utf-8", "replace")}
        type(self).seen.append({
            "path": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": payload,
        })
        body = json.dumps(type(self).response or {}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _handle

    def log_message(self, *args):        # 别把测试日志刷满
        pass


@pytest.fixture
def stub_server():
    _StubHandler.seen = []
    _StubHandler.response = {"choices": [{"message": {"content": "ok"}}]}
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", _StubHandler
    server.shutdown()
    server.server_close()


def image(width=48, height=24) -> QImage:
    picture = QImage(width, height, QImage.Format.Format_ARGB32)
    picture.fill(QColor("#3366CC"))
    return picture


def _model(base_url, protocol, **kwargs) -> VisionModel:
    return VisionModel(name="probe", base_url=base_url, model_id="probe-model",
                       api_key="sk-test", protocol=protocol, **kwargs)


class TestProtocolRoundTrips:
    """四种协议各走一次真往返。"""

    def test_openai_compatible(self, stub_server):
        base, handler = stub_server
        handler.response = {"choices": [{"message": {"content": "  openai 正文  "}}]}

        result = convert_image(image(), _model(f"{base}/v1", "openai"))

        assert result.ok and result.text == "openai 正文"
        call = handler.seen[0]
        assert call["path"] == "/v1/chat/completions"
        assert call["headers"]["authorization"] == "Bearer sk-test"
        content = call["body"]["messages"][0]["content"]
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_azure_uses_deployment_path_and_api_key_header(self, stub_server):
        base, handler = stub_server
        result = convert_image(image(), _model(base, "azure", api_version="2023-05-15"))

        assert result.ok, result.error
        call = handler.seen[0]
        assert call["path"] == "/openai/deployments/probe-model/chat/completions?api-version=2023-05-15"
        assert call["headers"]["api-key"] == "sk-test"
        assert "authorization" not in call["headers"]
        assert call["body"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:")

    def test_anthropic_uses_messages_api_with_base64_source(self, stub_server):
        base, handler = stub_server
        handler.response = {"content": [
            {"type": "text", "text": "第一段"},
            {"type": "tool_use", "id": "x"},
            {"type": "text", "text": " 第二段"},
        ]}

        result = convert_image(image(), _model(base, "anthropic"))

        assert result.ok and result.text == "第一段 第二段"
        call = handler.seen[0]
        assert call["path"] == "/v1/messages"
        assert call["headers"]["x-api-key"] == "sk-test"
        assert call["headers"]["anthropic-version"]
        assert "authorization" not in call["headers"]
        blocks = call["body"]["messages"][0]["content"]
        assert blocks[1]["type"] == "image"
        assert blocks[1]["source"]["type"] == "base64"
        assert blocks[1]["source"]["media_type"] == "image/png"
        assert blocks[1]["source"]["data"] and not blocks[1]["source"]["data"].startswith("data:")

    def test_gemini_puts_the_key_in_the_query_and_reads_candidates(self, stub_server):
        base, handler = stub_server
        handler.response = {"candidates": [
            {"content": {"parts": [{"text": "gemini "}, {"text": "正文"}]}}]}

        result = convert_image(image(), _model(f"{base}/v1beta", "gemini"))

        assert result.ok and result.text == "gemini 正文"
        call = handler.seen[0]
        assert call["path"] == "/v1beta/models/probe-model:generateContent?key=sk-test"
        assert "authorization" not in call["headers"]
        parts = call["body"]["contents"][0]["parts"]
        assert "text" in parts[0]
        assert parts[1]["inline_data"]["mime_type"] == "image/png"
        assert parts[1]["inline_data"]["data"]


class TestProtocolDetails:
    def test_every_protocol_is_reachable(self):
        assert set(PROTOCOLS) == {"openai", "azure", "anthropic", "gemini"}

    def test_unknown_protocol_falls_back_to_openai(self):
        model = _model("https://example.com/v1", "hand-made-typo")
        assert model.protocol_id == "openai"
        assert model.endpoint() == "https://example.com/v1/chat/completions"

    def test_gemini_endpoint_keeps_an_api_key_out_of_the_log_line(self):
        from ocr.vision_models import _log_endpoint

        model = _model("https://generativelanguage.googleapis.com/v1beta", "gemini")
        assert "key=sk-test" in model.endpoint()
        assert "sk-test" not in _log_endpoint(model.endpoint())

    def test_azure_accepts_a_full_endpoint(self):
        model = _model("https://res.openai.azure.com/openai/deployments/d/chat/completions",
                       "azure")
        assert model.endpoint().endswith("?api-version=2024-06-01")

    def test_headers_are_empty_without_a_key(self):
        model = VisionModel(name="x", base_url="https://e/v1", model_id="m", protocol="anthropic")
        assert build_headers(model) == {"Content-Type": "application/json"}

    def test_anthropic_body_carries_the_image_as_base64(self):
        body = build_request_body(
            _model("https://e", "anthropic"), "data:image/webp;base64,QUJD", task="code")
        source = body["messages"][0]["content"][1]["source"]
        assert source == {"type": "base64", "media_type": "image/webp", "data": "QUJD"}
        assert body["max_tokens"] > 0


class TestTaskTemplates:
    def test_all_documented_templates_exist(self):
        assert {task.id for task in TASKS} == {"text", "code", "table", "formula",
                                              "general", "solve"}

    def test_templates_produce_different_instructions(self):
        instructions = {task.id: instruction_for(task.id) for task in TASKS}
        assert len(set(instructions.values())) == len(TASKS), "模板必须各自给出不同指令"

    def test_table_template_follows_the_output_format(self):
        assert instruction_for("table", "html") != instruction_for("table", "markdown")
        assert "HTML" in instruction_for("table", "html")

    def test_unknown_template_falls_back_to_general(self):
        assert instruction_for("nonsense") == instruction_for("general")

    def test_the_instruction_reaches_the_service(self, stub_server):
        base, handler = stub_server

        convert_image(image(), _model(f"{base}/v1", "openai"), task="code")
        convert_image(image(), _model(f"{base}/v1", "openai"), task="formula")

        first = handler.seen[0]["body"]["messages"][0]["content"][0]["text"]
        second = handler.seen[1]["body"]["messages"][0]["content"][0]["text"]
        assert first == instruction_for("code")
        assert second == instruction_for("formula")
        assert first != second


class TestFailures:
    def test_no_model_configured(self):
        result = convert_image(image(), None)
        assert not result.ok and result.error

    def test_missing_base_url(self):
        result = convert_image(image(), VisionModel(name="empty"))
        assert not result.ok and "地址" in result.error
