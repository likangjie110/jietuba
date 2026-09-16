# -*- coding: utf-8 -*-
"""
设置页的离线模型卡片

只测卡片自己的状态机：清单为空时的提示、安装状态与按钮文案的对应、删除时先释放
引擎再删文件。真实下载由 test_local_models.py 覆盖，这里不重复。
"""
import pytest

from translation import local_engine, local_models
from ui.settings_ui.local_models_card import LocalModelsGroup


class _StubDialog:
    """卡片的 tr() 正常走 Qt 翻译查表，这里只需要返回原文。"""

    def tr(self, text, *args, **kwargs):
        return text


@pytest.fixture
def model(tmp_path, monkeypatch):
    spec = local_models.ModelSpec(
        model_id="demo", display_name="Demo 模型", filename="model.bin",
        size_bytes=4, sha256="unused", languages=("zh", "en"),
        license_note="CC-BY-NC",
    )
    monkeypatch.setattr(local_models, "MODELS", {"demo": spec})
    monkeypatch.setattr(local_models, "models_root", lambda: tmp_path / "models")
    local_engine.release_engines()
    return spec


def _install_files(model_id: str) -> None:
    directory = local_models.model_dir(model_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.bin").write_bytes(b"1234")


def test_empty_manifest_shows_a_note_instead_of_an_empty_box(qapp, monkeypatch):
    monkeypatch.setattr(local_models, "MODELS", {})

    group = LocalModelsGroup(_StubDialog())

    assert group._rows == {}


def test_row_shows_install_then_delete(qapp, model):
    group = LocalModelsGroup(_StubDialog())
    row = group._rows["demo"]

    assert row["button"].text() == "Install"
    assert row["status"].text() == "Not installed"
    # 语言、体积、许可都要摆在明面上
    assert "CC-BY-NC" in row["status"].parent().contentLabel.text()

    _install_files("demo")
    group.refresh()

    assert row["button"].text() == "Delete"
    assert row["status"].text() == "Installed"


def test_delete_releases_engine_before_removing_files(qapp, model, monkeypatch):
    """Windows 上模型文件被 mmap 住，不先释放引擎就删不掉。"""
    released = []
    monkeypatch.setattr(local_engine, "release_engines",
                        lambda model_id=None: released.append(model_id))
    group = LocalModelsGroup(_StubDialog())
    _install_files("demo")
    group.refresh()

    group._on_button("demo")

    assert released == ["demo"]
    assert not local_models.model_dir("demo").exists()
    assert group._rows["demo"]["button"].text() == "Install"


def test_interrupted_download_is_reported_as_incomplete(qapp, model):
    """上次没下完的残件要看得见，不然用户以为得从头再来。"""
    group = LocalModelsGroup(_StubDialog())
    part = local_models.model_path("demo").with_name("model.bin.part")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"12")

    group.refresh()

    assert "Incomplete" in group._rows["demo"]["status"].text()
