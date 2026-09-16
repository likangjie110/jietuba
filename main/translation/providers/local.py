"""Offline local engine adapter（按需下载的离线模型，不联网）。"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Set

from .. import local_engine, local_models
from ..models import (
    TranslationErrorCode,
    TranslationRequest,
    TranslationResult,
    normalize_language_code,
)
from ..provider import TranslationProvider

MODEL_NOT_INSTALLED = "离线模型未安装：设置 → 翻译 → 本地离线引擎"


class LocalProvider(TranslationProvider):
    """本地离线模型。没有密钥，"配置"就等于"模型装没装"。"""

    provider_id = "local"
    display_name = "Offline (Local)"

    # 没有凭据要填——模型的安装/删除走设置页的模型卡片，不是文本输入框
    CREDENTIAL_FIELDS = ()
    HELP_LABEL = ""
    HELP_URL = ""

    def __init__(self, config: Mapping[str, Any]):
        self._model_id = str(config.get("model_id") or "")

    def _spec(self) -> Optional[local_models.ModelSpec]:
        """当前使用的模型。

        没显式指定就用第一个已安装的——只有一个模型时，不该再逼用户去选一次。
        """
        if self._model_id:
            return local_models.MODELS.get(self._model_id)
        for model_id in local_models.installed_models():
            return local_models.MODELS.get(model_id)
        return None

    def is_configured(self) -> bool:
        spec = self._spec()
        if spec is None or not local_models.is_installed(spec.model_id):
            return False
        return local_engine.runtime_available()

    def supported_target_languages(self) -> Optional[Set[str]]:
        spec = self._spec()
        if spec is None or not spec.lang_map:
            return None
        return set(spec.lang_map)

    def translate(self, request: TranslationRequest) -> TranslationResult:
        spec = self._spec()
        if spec is None or not local_models.is_installed(spec.model_id):
            return TranslationResult(
                success=False,
                error_code=TranslationErrorCode.NOT_CONFIGURED,
                error_message=MODEL_NOT_INSTALLED,
            )
        if not local_engine.runtime_available():
            return TranslationResult(
                success=False,
                error_code=TranslationErrorCode.NOT_CONFIGURED,
                error_message=local_engine.runtime_error(),
            )

        try:
            engine = local_engine.get_engine(
                spec.model_id, local_models.model_dir(spec.model_id), spec.lang_map
            )
            translated = engine.translate(
                request.text,
                request.source_lang,
                normalize_language_code(request.target_lang) or "en",
            )
        except local_engine.LocalEngineError as exc:
            return TranslationResult(
                success=False,
                error_code=TranslationErrorCode.UNKNOWN,
                error_message=str(exc),
            )

        if not translated.strip():
            return TranslationResult(
                success=False,
                error_code=TranslationErrorCode.INVALID_REQUEST,
                error_message="离线引擎没有返回内容",
            )
        return TranslationResult(
            success=True,
            translated_text=translated,
            detected_source_lang=request.source_lang or "",
        )
