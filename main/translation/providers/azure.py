# -*- coding: utf-8 -*-
"""Azure Cognitive Services Translator provider."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Mapping

from core import log_error

from ..models import (
    TranslationErrorCode,
    TranslationRequest,
    TranslationResult,
    normalize_language_code,
)
from ..provider import TextField, TranslationProvider


class AzureTranslateProvider(TranslationProvider):
    """Microsoft Azure Translator (Cognitive Services) REST API v3.0."""

    provider_id = "azure"
    display_name = "Azure Translator"
    API_URL = "https://api.cognitive.microsofttranslator.com/translate"
    API_VERSION = "3.0"
    MAX_TEXT_LENGTH = 50_000

    _LANGUAGE_CODES: dict[str, str] = {
        "zh-Hans": "zh-Hans",
        "zh-Hant": "zh-Hant",
        "en-US": "en",
        "en-GB": "en",
        "pt-BR": "pt",
    }

    def __init__(self, config: Mapping[str, Any]):
        self._api_key = str(config.get("api_key", "") or "").strip()
        self._region = str(config.get("region", "") or "").strip()
        self._endpoint = str(
            config.get("endpoint", "") or ""
        ).strip().rstrip("/")

    CREDENTIAL_FIELDS = (
        TextField("azure_translate_api_key", "Azure API Key",
                  "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", secret=True),
        TextField("azure_translate_region", "Azure Region", "eastasia"),
        TextField("azure_translate_endpoint", "Azure Endpoint",
                  "Optional, use default if empty"),
    )
    HELP_LABEL = "portal.azure.com"
    HELP_URL = "https://portal.azure.com/"

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def translate(self, request: TranslationRequest) -> TranslationResult:
        if not request.text or not request.text.strip():
            return self._error(
                TranslationErrorCode.INVALID_REQUEST, "Text is empty"
            )
        if len(request.text) > self.MAX_TEXT_LENGTH:
            return self._error(
                TranslationErrorCode.INVALID_REQUEST,
                f"Text exceeds {self.display_name}'s "
                f"{self.MAX_TEXT_LENGTH:,}-character limit",
            )
        if not self.is_configured():
            return self._error(
                TranslationErrorCode.NOT_CONFIGURED,
                "Azure Translator API key is not configured",
            )

        base_url = (
            f"{self._endpoint}/translate"
            if self._endpoint
            else self.API_URL
        )
        params: dict[str, str] = {
            "api-version": self.API_VERSION,
            "to": self._to_azure_code(request.target_lang),
        }
        if request.source_lang:
            params["from"] = self._to_azure_code(request.source_lang)

        url = f"{base_url}?{urllib.parse.urlencode(params)}"

        headers: dict[str, str] = {
            "Content-Type": "application/json; charset=utf-8",
            "X-ClientTraceId": str(uuid.uuid4()),
            **self._auth_headers(),
        }

        http_request = urllib.request.Request(
            url=url,
            data=self._request_body(request),
            method="POST",
            headers=headers,
        )

        try:
            with urllib.request.urlopen(
                http_request, timeout=request.timeout
            ) as response:
                raw = json.loads(response.read().decode("utf-8"))

            if not raw or not isinstance(raw, list):
                return self._error(
                    TranslationErrorCode.UNKNOWN,
                    f"Invalid {self.display_name} response",
                )
            translations = raw[0].get("translations", [])
            if not translations:
                return self._error(
                    TranslationErrorCode.UNKNOWN,
                    f"Invalid {self.display_name} response",
                )
            translated = str(translations[0].get("text", "") or "")
            if not translated:
                return self._error(
                    TranslationErrorCode.UNKNOWN,
                    f"Invalid {self.display_name} response",
                )
            detected = ""
            detected_lang = raw[0].get("detectedLanguage", {})
            if detected_lang:
                detected = normalize_language_code(
                    detected_lang.get("language", "")
                ) or ""
            return TranslationResult(
                success=True,
                translated_text=translated,
                detected_source_lang=detected,
            )
        except urllib.error.HTTPError as exc:
            return self._http_error(exc)
        except TimeoutError:
            # 读超时抛的是 TimeoutError，它不是 URLError 的子类，不加这条就会
            # 一路掉进下面的兜底：归类成 UNKNOWN，还把「The read operation
            # timed out」这种英文原文直接甩给用户。
            log_error(
                f"Azure Translate timed out after {request.timeout}s",
                "AzureTranslate",
            )
            return self._error(
                TranslationErrorCode.NETWORK_ERROR,
                f"Request timed out after {request.timeout}s",
            )
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            log_error(
                f"Azure Translate network error: {reason}",
                "AzureTranslate",
            )
            return self._error(
                TranslationErrorCode.NETWORK_ERROR,
                f"Network error: {reason}",
            )
        except (ValueError, UnicodeDecodeError) as exc:
            log_error(
                f"Azure Translate response error: {exc}",
                "AzureTranslate",
            )
            return self._error(
                TranslationErrorCode.UNKNOWN,
                f"Failed to parse {self.display_name} response",
            )
        except Exception as exc:
            log_error(
                f"Azure Translate request failed: {exc}",
                "AzureTranslate",
            )
            return self._error(
                TranslationErrorCode.UNKNOWN,
                f"Translation failed: {exc}",
            )

    def _auth_headers(self) -> dict[str, str]:
        """鉴权头。抽成方法是为了让同一套 v3 协议的另一家只换这一处。

        bing_free 继承整个 translate()，只覆盖这个方法、_request_body 和 API_URL。
        """
        headers = {"Ocp-Apim-Subscription-Key": self._api_key}
        if self._region:
            headers["Ocp-Apim-Subscription-Region"] = self._region
        return headers

    def _request_body(self, request: TranslationRequest) -> bytes:
        """v3 的请求体：一个带 Text 字段的元素。

        Edge 那个免费接口收的是裸字符串数组，形状不一样，由子类覆盖。
        """
        return json.dumps(
            [{"Text": request.text}],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def _to_azure_code(cls, language_code: str) -> str:
        return cls._LANGUAGE_CODES.get(language_code, language_code)

    def _http_error(
        self, error: urllib.error.HTTPError
    ) -> TranslationResult:
        try:
            body = error.read()
            data = json.loads(body.decode("utf-8")) if body else {}
        except (ValueError, UnicodeDecodeError):
            data = {}
        # Azure returns {"error":{"code":"...", "message":"..."}}
        err_obj = data.get("error", {}) if isinstance(data, dict) else {}
        err_code = str(err_obj.get("code", "") or "")
        message = str(
            err_obj.get("message")
            or error.reason
            or f"HTTP {error.code}"
        )
        code = self._map_error_code(err_code, error.code)
        log_error(
            f"Azure Translate HTTP {error.code}: {err_code or message}",
            "AzureTranslate",
        )
        return self._error(code, message)

    @staticmethod
    def _map_error_code(
        err_code: str, status_code: int
    ) -> TranslationErrorCode:
        code_lower = err_code.lower()
        if status_code in {401, 403} or "unauthorized" in code_lower:
            return TranslationErrorCode.AUTH_FAILED
        if status_code == 429 or "ratelimit" in code_lower:
            return TranslationErrorCode.RATE_LIMITED
        if status_code == 400:
            if "language" in code_lower:
                return TranslationErrorCode.UNSUPPORTED_LANGUAGE
            return TranslationErrorCode.INVALID_REQUEST
        if status_code >= 500:
            return TranslationErrorCode.NETWORK_ERROR
        return TranslationErrorCode.UNKNOWN

    @staticmethod
    def _error(
        code: TranslationErrorCode, message: str
    ) -> TranslationResult:
        return TranslationResult(
            success=False,
            error_code=code,
            error_message=message,
        )
