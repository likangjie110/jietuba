# -*- coding: utf-8 -*-
"""视觉模型：把一张图交给大模型，拿回文字（Markdown / HTML / 代码解释 / LaTeX）。

和「公式识别的外部服务」同一套做法：标准库 urllib，不引第三方客户端。支持四种协议
（``PROTOCOLS``）——OpenAI 兼容、Azure OpenAI、Anthropic Claude、Google Gemini；它们
在**端点、鉴权头、请求体形状、响应结构**四处都不同，所以按协议分开构造与解析，而不是
硬凑成一种。

任务模板（``TASKS``）决定发给模型的指令：精确取字 / 代码解释 / 表格 / 公式 / 通用识图。
指令是**数据**而不是散在代码里的字符串，界面才能列出可选模板、测试才能断言"不同模板给出的
指令不同"。

外部服务是边界，因此它的失败**必须**如实报出来——网络错误、非 200、响应结构不对三种情况
分开写清楚，而不是统一成一句「转换失败」。
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

#: 输出格式（表格模板用哪个）
TARGETS = ("markdown", "html")

#: 支持的协议。它们不是"格式差异"而是四套不同的接口约定，所以逐个显式实现。
PROTOCOLS = ("openai", "azure", "anthropic", "gemini")

#: 协议在后端的默认版本参数：Azure 的 api-version、Anthropic 的 anthropic-version
DEFAULT_API_VERSION = "2024-06-01"
ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class VisionTask:
    """一个内置任务模板：id、界面标签（英文源，界面再 tr()）与发给模型的指令。"""

    id: str
    label: str
    instruction: str


#: 任务模板。指令写成英文——模型侧的提示词，不参与界面翻译。
TASKS: tuple = (
    VisionTask(
        "text", "Precise text extraction",
        "Extract all text from this image exactly as it appears. "
        "Keep the reading order, do not summarize, do not translate. Output plain text only.",
    ),
    VisionTask(
        "code", "Explain the code",
        "Explain what the code in this image does: its purpose, the main steps, and anything "
        "notable. Keep it short and concrete.",
    ),
    VisionTask(
        "table", "Table to Markdown",
        "Convert the content of this image into a Markdown table or text. "
        "Keep every cell, do not invent rows. Output Markdown only.",
    ),
    VisionTask(
        "formula", "Formula to LaTeX",
        "Transcribe the formula in this image as LaTeX. Output the LaTeX only, "
        "without explanation or code fences.",
    ),
    VisionTask(
        "general", "Describe the image",
        "Describe what this image shows, including the visible text and the layout. "
        "Be concise and factual.",
    ),
)

TASKS_BY_ID = {task.id: task for task in TASKS}

#: 默认任务（老配置没有这个键时用它 = 原来的行为）
DEFAULT_TASK = "table"

#: 单次请求超时（秒）
DEFAULT_TIMEOUT = 60

#: 请求里最多塞多少像素的图（过大的图先缩小，避免请求体几百 MB）
MAX_EDGE = 2000

#: 密钥在系统密钥库里的名字前缀：一个模型一条，按模型名区分
CREDENTIAL_PREFIX = "vision_model:"


@dataclass
class VisionModel:
    """一个视觉模型配置：协议 + 端点 + 模型 ID（+ Azure 的 api-version）。"""

    name: str
    base_url: str = ""
    model_id: str = ""
    api_key: str = ""
    vision: bool = True
    timeout: int = DEFAULT_TIMEOUT
    protocol: str = "openai"
    api_version: str = ""

    @property
    def protocol_id(self) -> str:
        """认识的协议才认，别的（老配置、手改坏了）按 OpenAI 兼容处理。"""
        value = str(self.protocol or "").strip().lower()
        return value if value in PROTOCOLS else "openai"

    def endpoint(self) -> str:
        """按协议补全端点；用户填到 base 或填到完整端点都认。"""
        base = str(self.base_url or "").strip().rstrip("/")
        if not base:
            return ""
        protocol = self.protocol_id
        if protocol == "azure":
            if "/chat/completions" in base:
                return base if "api-version=" in base else (
                    f"{base}?api-version={self.api_version_value}")
            version = self.api_version_value
            return (f"{base}/openai/deployments/{self.model_id}/chat/completions"
                    f"?api-version={version}")
        if protocol == "anthropic":
            return base if base.endswith("/messages") else f"{base}/v1/messages"
        if protocol == "gemini":
            # Gemini 把密钥放在查询参数里（见 build_request）；端点形如
            # {base}/models/{model}:generateContent?key=...
            path = base if ":generateContent" in base else (
                f"{base}/models/{self.model_id}:generateContent")
            return f"{path}{'&' if '?' in path else '?'}key={self.api_key}"

        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @property
    def api_version_value(self) -> str:
        return str(self.api_version or "").strip() or DEFAULT_API_VERSION

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
            protocol=str(raw.get("protocol", "") or "").strip() or "openai",
            api_version=str(raw.get("api_version", "") or "").strip(),
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
    models = [model for model in models if model is not None]
    return _load_credentials(config_manager, models)


def save_models(config_manager, models) -> bool:
    if config_manager is None:
        return False
    try:
        payload = [_strip_credentials(config_manager, model) for model in models]
        config_manager.set_app_setting(MODELS_KEY, json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as e:
        log_exception(e, T("保存视觉模型配置"))
        return False


def _secret_store(config_manager):
    """可用的凭据存储；没有就返回 None（调用方退回旧行为：key 留在配置里）。

    存在 ``config_manager`` 上的那份优先——测试往配置管理器里注入内存实现，就不必去碰
    真实的系统钥匙串（见 ``ToolSettingsManager.secret_store``）。
    """
    try:
        store = getattr(config_manager, "secret_store", None)
        if store is None:
            from core.platform import secrets as platform_secrets

            store = platform_secrets
        return store if store.is_available() else None
    except Exception as e:
        log_exception(e, T("查询系统密钥库"))
        return None


def _credential_name(model_name: str) -> str:
    return f"{CREDENTIAL_PREFIX}{model_name}"


def _load_credentials(config_manager, models) -> list:
    """把每个模型的 api_key 从密钥库补上，并顺手迁移配置里的明文副本。"""
    store = _secret_store(config_manager)
    if store is None:
        return models

    migrated = False
    for model in models:
        stored = store.get_secret(_credential_name(model.name))
        if stored:
            model.api_key = stored
        elif model.api_key and store.set_secret(_credential_name(model.name), model.api_key):
            # 迁移只在读时做一次：写回后配置里就没有明文了，下一次走上面的分支
            migrated = True
    if migrated:
        log_debug(T("已把视觉模型的密钥迁移到系统密钥库"), "VisionModel")
        save_models(config_manager, models)
    return models


def _strip_credentials(config_manager, model) -> dict:
    """落盘用的字典：api_key 交给系统密钥库，配置里只留空串。"""
    data = model.to_dict() if hasattr(model, "to_dict") else dict(model)
    store = _secret_store(config_manager)
    if store is None:
        return data
    key = str(data.get("api_key", "") or "").strip()
    name = str(data.get("name", "") or "").strip()
    if not name:
        return data
    if key:
        store.set_secret(_credential_name(name), key)
    data["api_key"] = ""
    return data


def upsert_model(config_manager, model: VisionModel) -> list:
    """按名字插入或覆盖一个模型，返回更新后的清单。"""
    models = [item for item in load_models(config_manager) if item.name != model.name]
    models.append(model)
    save_models(config_manager, models)
    return models


def delete_model(config_manager, name: str) -> list:
    models = [item for item in load_models(config_manager) if item.name != name]
    save_models(config_manager, models)
    store = _secret_store(config_manager)
    if store is not None:
        # 模型没了，它的密钥也没有再留着的理由
        store.delete_secret(_credential_name(name))
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


def instruction_for(task: str, target: str = "markdown") -> str:
    """任务模板对应的指令；表格模板再按输出格式分 Markdown / HTML。

    不认识的 id 回落到通用识图——手改坏的配置不该让功能直接不可用。
    """
    task_id = str(task or "").strip()
    if task_id not in TASKS_BY_ID:
        task_id = "general"
    if task_id == "table" and str(target or "").strip() == "html":
        return ("Convert the content of this image into an HTML table. "
                "Keep every cell, do not invent rows. Output HTML only.")
    return TASKS_BY_ID[task_id].instruction


def _log_endpoint(endpoint: str) -> str:
    """日志里用的端点：把查询参数里的密钥抹掉。

    Gemini 把 api key 放在 URL 的 ``key=`` 上，日志是被用户随手发出来的东西，不能带密钥。
    """
    text = str(endpoint or "")
    if "key=" not in text:
        return text
    head, _sep, _tail = text.partition("key=")
    return f"{head}key=***"


def _split_data_uri(data_uri: str) -> tuple:
    """``data:image/png;base64,XXXX`` → ``("image/png", "XXXX")``。

    OpenAI 兼容协议要整串 data URI，Anthropic / Gemini 要分开的 mime 与 base64。
    """
    text = str(data_uri or "")
    if not text.startswith("data:") or ";base64," not in text:
        return "image/png", ""
    head, payload = text.split(";base64,", 1)
    return head[len("data:"):] or "image/png", payload


def build_request_body(model: VisionModel, data_uri: str, target: str = "markdown",
                       task: str = DEFAULT_TASK) -> dict:
    """按协议构造请求体（图片 + 一条模板指令）。"""
    protocol = model.protocol_id
    instruction = instruction_for(task, target)

    if protocol == "anthropic":
        mime, payload = _split_data_uri(data_uri)
        return {
            "model": model.model_id,
            "max_tokens": 4096,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {"type": "image", "source": {
                        "type": "base64", "media_type": mime, "data": payload,
                    }},
                ],
            }],
        }

    if protocol == "gemini":
        mime, payload = _split_data_uri(data_uri)
        return {
            "contents": [{
                "parts": [
                    {"text": instruction},
                    {"inline_data": {"mime_type": mime, "data": payload}},
                ],
            }],
        }

    # openai / azure：同一套 chat completions 形状
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


def build_headers(model: VisionModel) -> dict:
    """按协议给鉴权头。Gemini 的密钥在查询参数里（见 ``endpoint``），这里不带。"""
    headers = {"Content-Type": "application/json"}
    key = str(model.api_key or "").strip()
    if not key:
        return headers
    protocol = model.protocol_id
    if protocol == "azure":
        headers["api-key"] = key
    elif protocol == "anthropic":
        headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    elif protocol == "gemini":
        pass
    else:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def parse_response(payload, protocol: str = "openai") -> VisionResult:
    """从响应里取正文；按协议走不同路径，结构不对时给出具体是哪里不对。"""
    if not isinstance(payload, dict):
        return VisionResult(False, error=T("视觉模型返回的不是 JSON 对象").render())
    if protocol == "anthropic":
        return _parse_anthropic(payload)
    if protocol == "gemini":
        return _parse_gemini(payload)
    return _parse_openai(payload)


def _parse_openai(payload: dict) -> VisionResult:
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


def _parse_anthropic(payload: dict) -> VisionResult:
    blocks = payload.get("content")
    if not isinstance(blocks, list) or not blocks:
        return VisionResult(False, error=T("视觉模型响应里没有 content").render())
    text = "".join(
        block.get("text", "") for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if not text.strip():
        return VisionResult(False, error=T("视觉模型返回了空内容").render())
    return VisionResult(True, text=text.strip())


def _parse_gemini(payload: dict) -> VisionResult:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return VisionResult(False, error=T("视觉模型响应里没有 candidates").render())
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return VisionResult(False, error=T("视觉模型返回了空内容").render())
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    if not text.strip():
        return VisionResult(False, error=T("视觉模型返回了空内容").render())
    return VisionResult(True, text=text.strip())


def convert_image(image: QImage, model: VisionModel | None, *, target: str = "markdown",
                  task: str = DEFAULT_TASK, opener=None) -> VisionResult:
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

    body = json.dumps(build_request_body(model, data_uri, target, task)).encode("utf-8")
    request = urllib.request.Request(model.endpoint(), data=body,
                                     headers=build_headers(model), method="POST")
    open_url = opener or urllib.request.urlopen
    log_debug(T("视觉模型请求: {protocol} {endpoint} ({model})", protocol=model.protocol_id,
                endpoint=_log_endpoint(model.endpoint()), model=model.model_id), "VisionModel")
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
    return parse_response(payload, model.protocol_id)
