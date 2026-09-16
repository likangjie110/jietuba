# -*- coding: utf-8 -*-
"""
本地离线引擎与 provider

这台机器上没有 ctranslate2/transformers，正好——推理后端是被替换掉的
（monkeypatch _load_backend），这里测的是引擎自己的逻辑：懒加载、切句、语言码映射、
释放，以及 provider 在"模型没装""运行库没打进去"这些情况下的错误码和文案。
"""
import pytest

from translation import local_engine, local_models
from translation.models import TranslationErrorCode, TranslationRequest
from translation.providers.local import LocalProvider


@pytest.fixture
def backend_calls(monkeypatch):
    """假推理后端：记录每次调用，把目标语言码标在结果里。"""
    calls = []

    class _Backend:
        def translate(self, text, source_code, target_code):
            calls.append((text, source_code, target_code))
            return f"[{target_code}]{text}"

    monkeypatch.setattr(local_engine, "_load_backend", lambda model_dir: _Backend())
    monkeypatch.setattr(local_engine, "_import_error", lambda: None)
    local_engine.release_engines()
    yield calls
    local_engine.release_engines()


@pytest.fixture
def installed_model(tmp_path, monkeypatch):
    """登记一个真的存在文件的模型，模型目录指向临时目录。"""
    spec = local_models.ModelSpec(
        model_id="demo",
        display_name="Demo",
        filename="model.bin",
        size_bytes=4,
        sha256="unused",
        languages=("zh", "en"),
        lang_map={"zh-Hans": "zho_Hans", "en": "eng_Latn"},
    )
    monkeypatch.setattr(local_models, "MODELS", {"demo": spec})
    monkeypatch.setattr(local_models, "models_root", lambda: tmp_path / "models")
    directory = local_models.model_dir("demo")
    directory.mkdir(parents=True)
    (directory / "model.bin").write_bytes(b"1234")
    return spec


class TestEngine:

    def test_stays_lazy_until_first_translate(self, installed_model, monkeypatch):
        loaded = []
        monkeypatch.setattr(local_engine, "_import_error", lambda: None)
        monkeypatch.setattr(local_engine, "_load_backend",
                            lambda model_dir: loaded.append(model_dir) or _FakeBackend())

        engine = local_engine.LocalEngine(local_models.model_dir("demo"), {})
        assert engine.loaded is False
        assert loaded == []

        engine.translate("hi", "en", "zh-Hans")
        assert engine.loaded is True
        assert len(loaded) == 1

    def test_splits_sentences_and_maps_language_codes(self, installed_model, backend_calls):
        engine = local_engine.LocalEngine(
            local_models.model_dir("demo"), installed_model.lang_map)

        engine.translate("你好。世界！", "zh-Hans", "en")

        assert [call[0] for call in backend_calls] == ["你好。", "世界！"]
        # 应用语言码被换成模型自己的码
        assert {(call[1], call[2]) for call in backend_calls} == {("zho_Hans", "eng_Latn")}

    def test_unmapped_language_passes_through(self, installed_model, backend_calls):
        """映射表里没有的码原样传下去——很多模型直接认 en/zh。"""
        engine = local_engine.LocalEngine(local_models.model_dir("demo"), {})

        engine.translate("hello", "en", "ja")

        assert backend_calls == [("hello", "en", "ja")]

    def test_release_drops_the_model(self, installed_model, backend_calls):
        engine = local_engine.LocalEngine(local_models.model_dir("demo"), {})
        engine.translate("hi", None, "en")
        assert engine.loaded is True

        engine.release()

        assert engine.loaded is False

    def test_missing_runtime_is_reported_readably(self, installed_model, monkeypatch):
        monkeypatch.setattr(local_engine, "_import_error",
                            lambda: "缺少离线翻译运行库: ctranslate2")
        engine = local_engine.LocalEngine(local_models.model_dir("demo"), {})

        with pytest.raises(local_engine.LocalEngineError) as excinfo:
            engine.translate("hi", None, "en")

        assert "ctranslate2" in str(excinfo.value)

    def test_engine_is_cached_per_model(self, installed_model, backend_calls):
        """provider 每次调用都是新实例，模型不能跟着实例走。"""
        first = local_engine.get_engine("demo", local_models.model_dir("demo"), {})
        second = local_engine.get_engine("demo", local_models.model_dir("demo"), {})

        assert first is second
        assert local_engine.loaded_models() == []      # 还没真正加载

        first.translate("hi", None, "en")
        assert local_engine.loaded_models() == ["demo"]

        local_engine.release_engines("demo")
        assert local_engine.loaded_models() == []


class TestProvider:

    def test_not_configured_when_model_is_missing(self, tmp_path, monkeypatch):
        spec = local_models.ModelSpec(
            model_id="demo", display_name="Demo", filename="model.bin",
            size_bytes=4, sha256="unused", urls=())
        monkeypatch.setattr(local_models, "MODELS", {"demo": spec})
        monkeypatch.setattr(local_models, "models_root", lambda: tmp_path / "models")
        provider = LocalProvider({})

        assert provider.is_configured() is False
        result = provider.translate(TranslationRequest(text="hi", target_lang="en"))
        assert result.error_code is TranslationErrorCode.NOT_CONFIGURED
        assert "未安装" in result.error_message

    def test_not_configured_when_runtime_is_missing(self, installed_model, monkeypatch):
        monkeypatch.setattr(local_engine, "_import_error",
                            lambda: "缺少离线翻译运行库: ctranslate2")
        provider = LocalProvider({"model_id": "demo"})

        assert provider.is_configured() is False
        result = provider.translate(TranslationRequest(text="hi", target_lang="en"))
        assert "ctranslate2" in result.error_message

    def test_translate_happy_path(self, installed_model, backend_calls):
        provider = LocalProvider({"model_id": "demo"})

        assert provider.is_configured() is True
        result = provider.translate(
            TranslationRequest(text="你好。", target_lang="en", source_lang="zh-Hans"))

        assert result.success is True
        assert result.translated_text == "[eng_Latn]你好。"

    def test_engine_failure_becomes_a_result_not_an_exception(self, installed_model, monkeypatch):
        def _boom(model_dir):
            raise RuntimeError("权重文件损坏")

        monkeypatch.setattr(local_engine, "_load_backend", _boom)
        monkeypatch.setattr(local_engine, "_import_error", lambda: None)
        local_engine.release_engines()

        result = LocalProvider({"model_id": "demo"}).translate(
            TranslationRequest(text="hi", target_lang="en"))

        assert result.success is False
        assert result.error_code is TranslationErrorCode.UNKNOWN
        assert "权重文件损坏" in result.error_message

    def test_empty_translation_is_reported(self, installed_model, monkeypatch):
        monkeypatch.setattr(local_engine, "_load_backend",
                            lambda model_dir: _EmptyBackend())
        monkeypatch.setattr(local_engine, "_import_error", lambda: None)
        local_engine.release_engines()

        result = LocalProvider({"model_id": "demo"}).translate(
            TranslationRequest(text="hi", target_lang="en"))

        assert result.success is False
        assert result.error_code is TranslationErrorCode.INVALID_REQUEST

    def test_supported_languages_come_from_the_manifest(self, installed_model):
        provider = LocalProvider({"model_id": "demo"})

        assert provider.supported_target_languages() == {"zh-Hans", "en"}

    def test_falls_back_to_the_first_installed_model(self, installed_model, backend_calls):
        """没指定模型时不该逼用户再选一次——只有一个就直接用。"""
        provider = LocalProvider({})

        assert provider.is_configured() is True
        assert provider._spec().model_id == "demo"


class _FakeBackend:
    def translate(self, text, source_code, target_code):
        return f"[{target_code}]{text}"


class _EmptyBackend:
    def translate(self, text, source_code, target_code):
        return "   "
