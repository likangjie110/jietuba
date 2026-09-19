# -*- coding: utf-8 -*-
"""Bing 免费接口（Edge 那条非官方路子）。

这里测的不是「接口通不通」——那取决于微软今天的心情，2026-07 他们就换过一次
地址，把一批软件一起打成 404。测的是这条路上出过问题、或下次再换时会先断掉的
那几处：请求形状（裸字符串数组 + 非空 UA）、不碰 Azure 的订阅密钥、以及「没有
密钥也算配好了」这个前置条件本身。
"""
import json
import urllib.error

import pytest

from translation.models import TranslationErrorCode, TranslationRequest
from translation.providers.bing_free import BingFreeTranslateProvider


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self) -> bytes:
        return self._body


def _translated(text="你好", detected="en") -> bytes:
    return json.dumps([{
        "detectedLanguage": {"language": detected, "score": 1.0},
        "translations": [{"text": text, "to": "zh-Hans"}],
    }]).encode("utf-8")


@pytest.fixture
def http(monkeypatch):
    box = {"calls": [], "reply": _translated()}

    def _urlopen(request, timeout=None):
        box["calls"].append(request)
        reply = box["reply"]
        if isinstance(reply, Exception):
            raise reply
        return _Response(reply)

    # translate() 是继承来的，urlopen 从 azure 那个模块里查，不是这儿
    monkeypatch.setattr(
        "translation.providers.azure.urllib.request.urlopen", _urlopen
    )
    return box


def _translate(**kwargs):
    return BingFreeTranslateProvider({}).translate(
        TranslationRequest(kwargs.pop("text", "hello"),
                           kwargs.pop("target_lang", "zh-Hans"), **kwargs)
    )


# ============================================================================
# 「不用配置」本身
# ============================================================================

def test_needs_no_credentials_at_all():
    """这个是选它的唯一理由：空配置也算配好了，否则整条路就没意义。"""
    provider = BingFreeTranslateProvider({})

    assert provider.is_configured() is True
    assert BingFreeTranslateProvider.CREDENTIAL_FIELDS == ()


def test_does_not_inherit_azure_help_link_or_subscription_key(http):
    """help_url 是继承来的，不清掉设置页会指向 Azure 门户。

    订阅密钥那套头一个都不该带上——这是免费接口，带了反而奇怪。
    """
    assert BingFreeTranslateProvider.HELP_URL == ""
    assert BingFreeTranslateProvider.HELP_LABEL == ""

    _translate()

    headers = {k.lower() for k in http["calls"][0].headers}
    assert not any("ocp-apim" in h for h in headers)


# ============================================================================
# 请求形状：这三条正是 2026-07 那次换接口会打断的地方
# ============================================================================

def test_body_is_a_bare_string_array(http):
    """老的 [{"Text": ...}] 形状会被现行接口直接拒掉。"""
    _translate(text="hello")

    assert json.loads(http["calls"][0].data) == ["hello"]


def test_user_agent_is_never_empty(http):
    """UA 空着接口回 400 "Client Browser Version not supported"。"""
    _translate()

    ua = http["calls"][0].get_header("User-agent")
    assert ua and ua.strip()


def test_url_carries_from_and_to(http):
    _translate(text="hello", target_lang="zh-Hant", source_lang="en-US")

    url = http["calls"][0].full_url
    assert url.startswith(BingFreeTranslateProvider.API_URL)
    assert "to=zh-Hant" in url
    assert "from=en" in url


def test_omits_from_when_detecting_language(http):
    """不传 source_lang 就是自动检测；传空的 from 是另一回事，别混。"""
    _translate(text="bonjour")

    assert "from=" not in http["calls"][0].full_url


# ============================================================================
# 响应与失败
# ============================================================================

def test_response_is_parsed_like_azure(http):
    http["reply"] = _translated("世界好", detected="en")

    result = _translate()

    assert result.success
    assert result.translated_text == "世界好"
    assert result.detected_source_lang == "en"


def test_network_failure_is_a_result_not_an_exception(http):
    http["reply"] = urllib.error.URLError("offline")

    result = _translate()

    assert not result.success
    assert result.error_code is TranslationErrorCode.NETWORK_ERROR


def test_overlong_text_is_rejected_before_sending(http):
    result = _translate(text="x" * (BingFreeTranslateProvider.MAX_TEXT_LENGTH + 1))

    assert result.error_code is TranslationErrorCode.INVALID_REQUEST
    assert http["calls"] == []


def test_error_message_names_bing_not_azure(http):
    """错误文案里的名字取自 display_name——继承 Azure 那家时它原本是写死的，
    用户选了 Bing 却被告知 Azure 出错了。"""
    http["reply"] = b"[]"  # 空数组，走「响应格式不对」那条路

    result = _translate()

    assert not result.success
    assert "Bing Translate (Free)" in result.error_message
    assert "Azure" not in result.error_message
