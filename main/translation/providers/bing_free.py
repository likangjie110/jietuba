# -*- coding: utf-8 -*-
"""微软 Edge「翻译此页」用的免费接口——不用申请密钥，选它就是配好了。

与 Azure Translator 是同一套协议下游出来的：同样的 `from`/`to` 参数、同样的
`[{detectedLanguage, translations}]` 响应、同样的错误处理。所以这里继承
AzureTranslateProvider，只覆盖「打哪个地址」「怎么鉴权」「请求体长什么样」。

**这是一条会动的路，改之前先读这段。**
微软 2026-07 下线了老写法（`edge.microsoft.com/translate/auth` 取 JWT，再带
Bearer 调 `api-edge.cognitive.microsofttranslator.com`），全网一批软件同时拿到
404，现行的是这里这个免鉴权的 `/translate/translatetext`。实测过的几个脾气：

- 请求体必须是**裸字符串数组**，老的 `[{"Text": ...}]` 会被拒
- `User-Agent` 必须有值。空的回 400 "Client Browser Version not supported"，
  但随便给个非空值就过（Python-urllib/3.11 实测可用）
- 多余的查询参数不报错（`api-version=3.0` 一起发也照样 200），所以没把 Azure
  那套参数拆开
- 语言码不认识时回 400 且**响应体是空的**，不是 Azure 那种 JSON 错误——所以错误
  信息会退回 HTTP 原因短语

哪天它再变一次，多半又是换地址或换请求形状，改这个文件就够了。
"""
from __future__ import annotations

import json

from ..models import TranslationRequest
from .azure import AzureTranslateProvider

# 老地址 https://edge.microsoft.com/translate/auth 已于 2026-07 下线，别再改回去。
EDGE_API_URL = "https://edge.microsoft.com/translate/translatetext"

# 非空即可，值本身不被校验（空的才回 400）。写全是为了万一以后开始校验。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
)


class BingFreeTranslateProvider(AzureTranslateProvider):
    """Edge 免费翻译接口。没有凭据可填，所以设置页这一节是空的。"""

    provider_id = "bing_free"
    display_name = "Bing Translate (Free)"
    API_URL = EDGE_API_URL

    # 没有任何密钥——「配置」这一步不存在。设置页那一节因此只有底下那行提示，
    # test_translation_provider_fields 里为此有一条豁免（和 local 同款）。
    CREDENTIAL_FIELDS = ()
    NOTICE = (
        "Unofficial Edge endpoint, no API key needed. "
        "It may be rate-limited or stop working at any time."
    )
    # Azure 那家的订阅密钥帮助链接在这里没意义，必须显式清掉，否则会继承过来。
    HELP_LABEL = ""
    HELP_URL = ""

    def is_configured(self) -> bool:
        return True

    def _auth_headers(self) -> dict[str, str]:
        # 不鉴权，但 UA 不能空着（见模块注释）。
        return {"User-Agent": USER_AGENT}

    def _request_body(self, request: TranslationRequest) -> bytes:
        return json.dumps(
            [request.text], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
