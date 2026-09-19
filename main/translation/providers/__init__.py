"""Built-in translation providers."""

from .amazon import AmazonTranslateProvider
from .azure import AzureTranslateProvider
from .baidu import BaiduTranslateProvider
from .bing_free import BingFreeTranslateProvider
from .deepseek import DeepSeekProvider
from .deepl import DeepLProvider
from .google import GoogleTranslateProvider
from .local import LocalProvider

__all__ = [
    "AmazonTranslateProvider",
    "AzureTranslateProvider",
    "BaiduTranslateProvider",
    "BingFreeTranslateProvider",
    "DeepSeekProvider",
    "DeepLProvider",
    "GoogleTranslateProvider",
    "LocalProvider",
]
