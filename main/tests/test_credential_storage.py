# -*- coding: utf-8 -*-
"""凭据存取接线（settings/tool_settings + ocr/vision_models）单元测试。

三件事要钉住：

1. **迁移**：升级前存在 QSettings 里的明文 key，第一次读时挪进密钥库并从配置里删掉——
   否则「改成了密钥库」只是新用户受益，老用户那份明文永远留在 plist/注册表里。
2. **回退**：拿不到密钥库（Linux）或写失败时，行为与改动前一致（明文），且不能把用户
   刚填的 key 静默丢掉。
3. **没有第二份真相**：写进密钥库的同时清掉明文副本。

密钥库本身用内存假实现——真实 Keychain 的往返由 test_platform_secrets.py 负责。
"""

import json

import pytest
from PySide6.QtWidgets import QApplication
from settings.tool_settings import ToolSettingsManager
from tests.conftest import InMemorySecretStore


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class _FailingSecretStore(InMemorySecretStore):
    """写不进的情况（钥匙串锁着 / 权限不够）。"""

    def set_secret(self, name, value):
        return False


@pytest.fixture
def store():
    """注入给配置管理器的内存存储——测试因此不碰真实钥匙串。"""
    return InMemorySecretStore()


@pytest.fixture
def manager(tmp_settings, store):
    return ToolSettingsManager(qsettings=tmp_settings, secret_store=store)


#: (读取方法, 写入方法, 旧的明文键, 密钥库里的名字)
SCALAR_CREDENTIALS = [
    ("get_deepl_api_key", "set_deepl_api_key", "app/deepl_api_key", "deepl_api_key"),
    ("get_google_translate_api_key", "set_google_translate_api_key",
     "translation/providers/google/api_key", "google_translate_api_key"),
    ("get_azure_translate_api_key", "set_azure_translate_api_key",
     "translation/providers/azure/api_key", "azure_translate_api_key"),
    ("get_amazon_translate_access_key_id", "set_amazon_translate_access_key_id",
     "translation/providers/amazon/access_key_id", "amazon_translate_access_key_id"),
    ("get_amazon_translate_secret_access_key", "set_amazon_translate_secret_access_key",
     "translation/providers/amazon/secret_access_key", "amazon_translate_secret_access_key"),
    ("get_amazon_translate_session_token", "set_amazon_translate_session_token",
     "translation/providers/amazon/session_token", "amazon_translate_session_token"),
]


class TestScalarCredentials:
    @pytest.mark.parametrize("getter,setter,legacy,secret_name", SCALAR_CREDENTIALS)
    def test_write_goes_to_the_store_and_clears_plaintext(
        self, manager, store, getter, setter, legacy, secret_name,
    ):
        getattr(manager, setter)("sk-test")
        assert store.data[secret_name] == "sk-test"
        assert not manager.qsettings.contains(legacy), "明文副本必须一起清掉"
        assert getattr(manager, getter)() == "sk-test"

    @pytest.mark.parametrize("getter,setter,legacy,secret_name", SCALAR_CREDENTIALS)
    def test_plaintext_is_migrated_on_first_read(
        self, manager, store, getter, setter, legacy, secret_name,
    ):
        manager.qsettings.setValue(legacy, "sk-legacy")
        assert getattr(manager, getter)() == "sk-legacy"
        assert store.data[secret_name] == "sk-legacy"
        assert not manager.qsettings.contains(legacy)

    def test_plaintext_wins_over_a_leftover_store_entry(self, manager, store, monkeypatch):
        """两份都在且不同：以用户设置页看到的那份（明文）为准，并覆盖密钥库里的旧条目。

        真机探针碰到过这个组合——密钥库里留着一条来路不明的同名条目，而设置里是用户刚填的
        key。这时要是「密钥库优先」，用户看到的 key 会在升级瞬间被悄悄换掉。
        """
        logged = []
        monkeypatch.setattr("core.logger.log_warning", lambda *a, **k: logged.append(a))
        manager.qsettings.setValue("app/deepl_api_key", "typed-by-user")
        store.data["deepl_api_key"] = "leftover"

        assert manager.get_deepl_api_key() == "typed-by-user"
        assert store.data["deepl_api_key"] == "typed-by-user"
        assert not manager.qsettings.contains("app/deepl_api_key")
        assert logged, "两份并存是异常情形，要留日志"

    def test_store_is_used_when_no_plaintext_left(self, manager, store):
        store.data["deepl_api_key"] = "from-store"
        assert manager.get_deepl_api_key() == "from-store"

    def test_clearing_removes_the_stored_secret(self, manager, store):
        manager.set_deepl_api_key("sk-test")
        manager.set_deepl_api_key("")
        assert "deepl_api_key" not in store.data
        assert "deepl_api_key" in store.deleted
        assert manager.get_deepl_api_key() == ""

    def test_whitespace_is_trimmed(self, manager, store):
        manager.set_google_translate_api_key("  sk-test  ")
        assert store.data["google_translate_api_key"] == "sk-test"


class TestSuiteIsolation:
    """护栏：测试不许碰开发机真实的系统钥匙串。

    没这条时，直接 ``ToolSettingsManager(qsettings=...)`` 建的用例（没有注入存储）会走回
    平台层的真实实现——本仓库就这样往开发机 Keychain 里塞进过 6 条测试条目，并由
    pyobjc 对象把 test_smart_translation.py 整个文件在拆除期弄崩。
    """

    def test_a_plain_manager_gets_the_isolation_fake(self, tmp_settings):
        from core.platform import secrets

        store_owner = getattr(secrets.get_secret, "__self__", None)
        assert isinstance(store_owner, InMemorySecretStore), (
            "conftest 的 isolated_secret_store 没生效："
            f"secret_store 落到了 {secrets.get_secret!r}"
        )

        manager = ToolSettingsManager(qsettings=tmp_settings)
        manager.set_deepl_api_key("sk-guard")
        assert manager.get_deepl_api_key() == "sk-guard"
        assert store_owner.data["deepl_api_key"] == "sk-guard"


class TestFallbacks:
    """拿不到密钥库或写失败时的行为——必须和改动前一致，且不丢用户填的值。"""

    def test_without_store_reads_and_writes_plaintext(self, tmp_settings):
        manager = ToolSettingsManager(
            qsettings=tmp_settings, secret_store=InMemorySecretStore(available=False))
        manager.set_deepl_api_key("sk-test")
        assert manager.qsettings.value("app/deepl_api_key") == "sk-test"
        assert manager.get_deepl_api_key() == "sk-test"

    def test_write_failure_keeps_the_key_in_plaintext(self, tmp_settings, monkeypatch):
        logged = []
        monkeypatch.setattr("core.logger.log_warning", lambda *a, **k: logged.append(a))
        store = _FailingSecretStore()
        manager = ToolSettingsManager(qsettings=tmp_settings, secret_store=store)

        manager.set_deepl_api_key("sk-test")

        assert store.data == {}
        assert manager.qsettings.value("app/deepl_api_key") == "sk-test"
        assert manager.get_deepl_api_key() == "sk-test"
        assert logged, "退回明文必须留一条日志，否则用户不知道 key 落回了配置文件"

    def test_empty_store_entry_still_migrates_the_plaintext(self, manager, store):
        """密钥库里没有这条时，明文照旧迁移（升级路径不能因为库里是空的就失效）。"""
        manager.qsettings.setValue("app/deepl_api_key", "old-plain")
        assert manager.get_deepl_api_key() == "old-plain"
        assert store.data["deepl_api_key"] == "old-plain"


class TestFormulaService:
    def test_config_round_trip_routes_the_key_through_the_store(self, manager, store):
        manager.set_formula_service_config("https://example.com/latex", "sk-formula", 30)
        assert store.data["formula_service_api_key"] == "sk-formula"
        assert not manager.qsettings.contains("app/formula_service_api_key")

        config = manager.get_formula_service_config()
        assert config["url"] == "https://example.com/latex"
        assert config["api_key"] == "sk-formula"
        assert config["timeout"] == 30

    def test_legacy_plaintext_key_is_migrated(self, manager, store):
        manager.set_app_setting("formula_service_url", "https://example.com/latex")
        manager.set_app_setting("formula_service_api_key", "sk-legacy")
        assert manager.get_formula_service_config()["api_key"] == "sk-legacy"
        assert store.data["formula_service_api_key"] == "sk-legacy"


class TestVisionModelKeys:
    @pytest.fixture(autouse=True)
    def _models(self, manager):
        from ocr.vision_models import VisionModel

        self.manager = manager
        self.model = VisionModel(
            name="local", base_url="http://127.0.0.1:8000/v1",
            model_id="gpt-4o-mini", api_key="sk-vision",
        )

    def _stored_json(self):
        raw = self.manager.get_app_setting("ocr_vision_models", "")
        return json.loads(raw) if raw else []

    def test_saving_strips_the_key_out_of_the_settings(self, store):
        from ocr.vision_models import load_models, upsert_model

        upsert_model(self.manager, self.model)

        assert store.data["vision_model:local"] == "sk-vision"
        payload = self._stored_json()
        assert payload and payload[0]["api_key"] == "", "配置里不该留明文 key"
        assert load_models(self.manager)[0].api_key == "sk-vision"

    def test_existing_plaintext_key_is_migrated(self, store):
        from ocr.vision_models import load_models

        self.manager.set_app_setting("ocr_vision_models", json.dumps([
            {"name": "local", "base_url": "http://127.0.0.1:8000/v1",
             "model_id": "gpt-4o-mini", "api_key": "sk-plain"},
        ], ensure_ascii=False))

        models = load_models(self.manager)

        assert models[0].api_key == "sk-plain"
        assert store.data["vision_model:local"] == "sk-plain"
        assert self._stored_json()[0]["api_key"] == ""

    def test_deleting_a_model_removes_its_secret(self, store):
        from ocr.vision_models import delete_model, upsert_model

        upsert_model(self.manager, self.model)
        delete_model(self.manager, "local")

        assert "vision_model:local" in store.deleted
        assert "vision_model:local" not in store.data

    def test_without_store_the_key_stays_in_settings(self, tmp_settings):
        from ocr.vision_models import load_models, upsert_model

        self.manager = ToolSettingsManager(
            qsettings=tmp_settings, secret_store=InMemorySecretStore(available=False))
        upsert_model(self.manager, self.model)

        assert self._stored_json()[0]["api_key"] == "sk-vision"
        assert load_models(self.manager)[0].api_key == "sk-vision"
