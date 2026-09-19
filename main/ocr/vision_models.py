# -*- coding: utf-8 -*-
"""OpenAI 兼容的视觉模型：把一张图交给模型，拿回 Markdown / HTML。

和「公式识别的外部服务」同一套做法（标准库 urllib，不引第三方客户端）：协议就是
`POST {base_url}/chat/completions`，图片按 `image_url` 的 data URI 塞进消息里。
外部服务是边界，因此它的失败**必须**如实报出来——网络错误、非 200、响应结构不对
三种情况分开写清楚，而不是统一成一句「转换失败」。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from core.logger import T, log_debug, log_exception, log_warning

#: 配置键：模型清单（JSON 列表）与默认模型名
MODELS_KEY = "ocr_vision_models"
SELECTED_KEY = "ocr_vision_model"

#: 输出格式
TARGETS = ("markdown", "html")

#: 单次请求超时（秒）
DEFAULT_TIMEOUT = 60

#: 请求里最多塞多少像素的图（过大的图先缩小，避免请求体几百 MB）
MAX_EDGE = 2000


@dataclass
class VisionModel:
    """一个 OpenAI 兼容的视觉模型配置。"""

    name: str
    base_url: str = ""
    model_id: str = ""
    api_key: str = ""
    vision: bool = True
    timeout: int = DEFAULT_TIMEOUT

    def endpoint(self) -> str:
        """补全成 chat/completions 端点（用户填到 base 或填到完整端点都认）。"""
        base = str(self.base_url or "").strip().rstrip("/")
        if not base:
            return ""
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @property
    def usable(self) -> bool:
        return bool(self.endpoint() and self.model_id)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "VisionModel | None":
        if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
            return None
        return cls(
            name=str(raw["name"]).strip(),
            base_url=str(raw.get("base_url", "") or "").strip(),
            model_id=str(raw.get("model_id", "") or "").strip(),
            api_key=str(raw.get("api_key", "") or "").strip(),
            vision=bool(raw.get("vision", True)),
            timeout=int(raw.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT),
        )


@dataclass
class VisionResult:
    """一次转换的结果：成功时带文本，失败时带一条人能照做的说明。"""

    ok: bool
    text: str = ""
    error: str = ""

    def __bool__(self) -> bool:
        return self.ok


def load_models(config_manager) -> list:
    """读回模型清单；坏数据当空表（一条坏 JSON 不该让整个功能不可用）。"""
    if config_manager is None:
        return []
    try:
        raw = config_manager.get_app_setting(MODELS_KEY, "")
    except Exception as e:
        log_exception(e, T("读取视觉模型配置"))
        return []
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        log_warning(T("视觉模型配置不是合法 JSON，按空表处理"), "VisionModel")
        return []
    if not isinstance(data, list):
        return []
    models = [VisionModel.from_dict(item) for item in data]
    return [model for model in models if model is not None]


def save_models(config_manager, models) -> bool:
    if config_manager is None:
        return False
    try:
        payload = [model.to_dict() if hasattr(model, "to_dict") else dict(model)
                   for model in models]
        config_manager.set_app_setting(MODELS_KEY, json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as e:
        log_exception(e, T("保存视觉模型配置"))
        return False


def upsert_model(config_manager, model: VisionModel) -> list:
    """按名字插入或覆盖一个模型，返回更新后的清单。"""
    models = [item for item in load_models(config_manager) if item.name != model.name]
    models.append(model)
    save_models(config_manager, models)
    return models


def delete_model(config_manager, name: str) -> list:
    models = [item for item in load_models(config_manager) if item.name != name]
    save_models(config_manager, models)
    return models


def selected_model(config_manager) -> VisionModel | None:
    """当前选中的模型：配置里指定的那个，否则第一个可用的。"""
    models = load_models(config_manager)
    if not models:
        return None
    name = ""
    if config_manager is not None:
        try:
            name = str(config_manager.get_app_setting(SELECTED_KEY, "") or "")
        except Exception as e:
            log_exception(e, T("读取选中的视觉模型"))
    for model in models:
        if model.name == name and model.usable:
            return model
    for model in models:
        if model.usable:
            return model
    return None


def encode_image(image: QImage, *, max_edge: int = MAX_EDGE) -> str:
    """把 QImage 编码成 data URI（过大时先等比缩小，避免请求体过大）。"""
    if image is None or image.isNull():
        return ""
    from PySide6.QtCore import Qt

    if max(image.width(), image.height()) > max_edge:
        image = image.scaled(max_edge, max_edge, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    # 不要写成 QBuffer(QByteArray())：那个 QByteArray 是个临时对象，出了这行就没了，
    # 而 QBuffer 只留一个指针——写 PNG 时会往已释放的内存里写（实测直接段错误）。
    # QBuffer 无参构造会自带一块内部缓冲，生命周期跟着 buffer 走。
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    payload = bytes(buffer.data())
    buffer.close()
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


def build_request_body(model: VisionModel, data_uri: str, target: str) -> dict:
    """构造 OpenAI 兼容的请求体（图片 + 一句指令）。"""
    instruction = ("Convert the content of this image into a Markdown table or text. "
                   "Output Markdown only." if target == "markdown" else
                   "Convert the content of this image into an HTML table. Output HTML only.")
    return {
        "model": model.model_id,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }],
        "temperature": 0,
    }


def parse_response(payload) -> VisionResult:
    """从响应里取正文；结构不对时给出具体是哪里不对。"""
    if not isinstance(payload, dict):
        return VisionResult(False, error=T("视觉模型返回的不是 JSON 对象").render())
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return VisionResult(False, error=T("视觉模型响应里没有 choices").render())
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        # 有些服务把正文拆成多段
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        return VisionResult(False, error=T("视觉模型返回了空内容").render())
    return VisionResult(True, text=content.strip())


def convert_image(image: QImage, model: VisionModel | None, *, target: str = "markdown",
                  opener=None) -> VisionResult:
    """把图片交给模型转换；``opener`` 可注入（测试用本地 stub，生产走 urllib）。"""
    if model is None:
        return VisionResult(False, error=T("没有配置可用的视觉模型").render())
    if target not in TARGETS:
        target = "markdown"
    if not model.usable:
        return VisionResult(False, error=T("视觉模型缺少地址或模型 ID").render())

    data_uri = encode_image(image)
    if not data_uri:
        return VisionResult(False, error=T("没有可转换的图片").render())

    body = json.dumps(build_request_body(model, data_uri, target)).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if model.api_key:
        headers["Authorization"] = f"Bearer {model.api_key}"

    request = urllib.request.Request(model.endpoint(), data=body, headers=headers,
                                     method="POST")
    open_url = opener or urllib.request.urlopen
    log_debug(T("视觉模型请求: {endpoint} ({model})", endpoint=model.endpoint(),
                model=model.model_id), "VisionModel")
    try:
        with open_url(request, timeout=int(model.timeout or DEFAULT_TIMEOUT)) as response:
            status = getattr(response, "status", 200)
            raw = response.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return VisionResult(False, error=T("视觉模型返回 HTTP {code}: {detail}",
                                           code=e.code, detail=detail).render())
    except Exception as e:
        log_exception(e, T("调用视觉模型"))
        return VisionResult(False, error=T("调用视觉模型失败: {e}", e=e).render())

    if status != 200:
        return VisionResult(False, error=T("视觉模型返回 HTTP {code}", code=status).render())
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except (TypeError, ValueError) as e:
        log_exception(e, T("解析视觉模型响应"))
        return VisionResult(False, error=T("视觉模型响应不是合法 JSON").render())
    return parse_response(payload)
