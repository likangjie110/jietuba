# -*- coding: utf-8 -*-
"""
离线翻译模型的下载与校验

下载代码最容易出的不是"报错"，而是"看着下完了、内容是坏的"——续传拼错、镜像串台、
校验被跳过。这里用一个支持 Range 的本地 HTTP 服务器把几条路径都跑一遍：全新下载、
中断后续传、校验不过要删残件、主源挂了换镜像、用户取消。

不碰真实网络，也不碰真实的模型文件。
"""
import hashlib
import http.server
import threading

import pytest

from translation import local_models as lm


def _make_server(payload: bytes):
    """起一个支持 Range 的极简 HTTP 服务器，返回 (base_url, state)。"""
    state = {"fail_after": None, "range_requests": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):        # 别把测试输出刷满
            pass

        def do_GET(self):
            start = 0
            header = self.headers.get("Range")
            if header:
                state["range_requests"] += 1
                start = int(header.split("=")[1].split("-")[0])
            body = payload[start:]
            if start:
                self.send_response(206)
                self.send_header(
                    "Content-Range",
                    f"bytes {start}-{len(payload) - 1}/{len(payload)}",
                )
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            cut = len(body) if state["fail_after"] is None else min(state["fail_after"], len(body))
            try:
                self.wfile.write(body[:cut])
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}/model.bin", state, server


@pytest.fixture
def model(tmp_path, monkeypatch):
    """登记一个假模型，模型目录指向临时目录。返回 (spec, payload, state)。"""
    # 512KB：要大于一个下载块（256KB），否则「下到一半取消」根本无从发生
    payload = hashlib.sha256(b"jietuba").digest() * 16384
    url, state, server = _make_server(payload)

    spec = lm.ModelSpec(
        model_id="demo-model",
        display_name="Demo 模型",
        filename="model.bin",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        languages=("zh", "en"),
        urls=(url,),
    )
    monkeypatch.setattr(lm, "MODELS", {spec.model_id: spec})
    monkeypatch.setattr(lm, "models_root", lambda: tmp_path / "models")
    yield spec, payload, state
    server.shutdown()


class TestInstall:

    def test_fresh_download_lands_verified_file(self, model):
        spec, payload, _ = model
        progress = []

        directory = lm.install(spec.model_id, progress=lambda done, total: progress.append((done, total)))

        assert (directory / spec.filename).read_bytes() == payload
        assert lm.is_installed(spec.model_id) is True
        # 进度是单调递增的，且以「全部下完」收尾
        assert [done for done, _ in progress] == sorted(done for done, _ in progress)
        assert progress[-1] == (len(payload), len(payload))

    def test_second_install_is_a_noop(self, model):
        spec, _, state = model
        lm.install(spec.model_id)
        before = state["range_requests"]

        lm.install(spec.model_id)

        assert state["range_requests"] == before

    def test_connection_drop_keeps_partial_for_resume(self, model):
        """断线只该留下残件，不该毁掉已下载的部分。"""
        spec, payload, state = model
        state["fail_after"] = len(payload) // 3

        with pytest.raises(lm.DownloadError):
            lm.install(spec.model_id)

        assert lm.is_installed(spec.model_id) is False
        assert lm.partial_size(spec.model_id) == len(payload) // 3

    def test_resume_continues_from_where_it_stopped(self, model):
        spec, payload, state = model
        state["fail_after"] = len(payload) // 3
        with pytest.raises(lm.DownloadError):
            lm.install(spec.model_id)

        state["fail_after"] = None
        directory = lm.install(spec.model_id)

        assert (directory / spec.filename).read_bytes() == payload   # 拼接结果必须完整
        assert state["range_requests"] == 1           # 第二次确实走了续传

    def test_checksum_mismatch_drops_the_partial_file(self, model, monkeypatch):
        """校验不过的残件是坏的，留着只会让下次也失败。"""
        spec, _, _ = model
        broken = lm.ModelSpec(**{**spec.__dict__, "sha256": "0" * 64})
        monkeypatch.setattr(lm, "MODELS", {spec.model_id: broken})

        with pytest.raises(lm.DownloadError):
            lm.install(spec.model_id)

        assert lm.partial_size(spec.model_id) == 0
        assert lm.is_installed(spec.model_id) is False

    def test_falls_back_to_the_next_source(self, model, monkeypatch):
        spec, payload, _ = model
        dead = "http://127.0.0.1:1/unreachable"
        with_mirror = lm.ModelSpec(**{**spec.__dict__, "urls": (dead, spec.urls[0])})
        monkeypatch.setattr(lm, "MODELS", {spec.model_id: with_mirror})

        directory = lm.install(spec.model_id)

        assert (directory / spec.filename).read_bytes() == payload

    def test_cancel_stops_and_leaves_partial(self, model):
        spec, payload, _ = model
        cancel = threading.Event()

        def _cancel_after_first_chunk(done, total):
            cancel.set()

        with pytest.raises(lm.DownloadCancelled):
            lm.install(spec.model_id, progress=_cancel_after_first_chunk, cancel=cancel)

        assert lm.is_installed(spec.model_id) is False
        assert 0 < lm.partial_size(spec.model_id) < len(payload)


class TestStore:

    def test_is_installed_rejects_wrong_size(self, model):
        """大小对不上就是没装好——启动时不做全量哈希，只能靠这个。"""
        spec, _, _ = model
        lm.install(spec.model_id)

        path = lm.model_path(spec.model_id)
        path.write_bytes(b"x")

        assert lm.is_installed(spec.model_id) is False

    def test_uninstall_removes_files(self, model):
        spec, _, _ = model
        lm.install(spec.model_id)

        assert lm.uninstall(spec.model_id) is True

        assert lm.is_installed(spec.model_id) is False
        assert lm.installed_models() == []

    def test_unknown_model_is_rejected(self, model):
        with pytest.raises(lm.DownloadError):
            lm.install("nope")

    def test_installed_models_tracks_real_files(self, model):
        spec, payload, _ = model
        assert lm.installed_models() == []

        lm.install(spec.model_id)

        assert lm.installed_models() == [spec.model_id]


def _make_zip(files: dict) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


class TestArchiveModels:
    """压缩包形式的模型：推理引擎的模型基本都是一整个目录。"""

    @pytest.fixture
    def zip_model(self, tmp_path, monkeypatch):
        payload = _make_zip({"model.bin": b"weights", "config.json": b"{}"})
        url, _, server = _make_server(payload)
        spec = lm.ModelSpec(
            model_id="zip-model",
            display_name="Zip 模型",
            filename="bundle.zip",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            languages=("zh", "en"),
            urls=(url,),
            entry_file="model.bin",
        )
        monkeypatch.setattr(lm, "MODELS", {spec.model_id: spec})
        monkeypatch.setattr(lm, "models_root", lambda: tmp_path / "models")
        yield spec
        server.shutdown()

    def test_unpacks_and_removes_the_archive(self, zip_model):
        directory = lm.install(zip_model.model_id)

        assert (directory / "model.bin").read_bytes() == b"weights"
        assert (directory / "config.json").is_file()
        assert not (directory / zip_model.filename).exists()   # 解包完不留压缩包占地方
        assert lm.is_installed(zip_model.model_id) is True

    def test_entry_file_decides_installed(self, zip_model):
        """解包后文件被删掉也算没装——否则引擎会去加载一个不存在的模型。"""
        directory = lm.install(zip_model.model_id)
        (directory / "model.bin").unlink()

        assert lm.is_installed(zip_model.model_id) is False

    def test_rejects_paths_escaping_the_model_dir(self, tmp_path, monkeypatch):
        """压缩包能通过校验，不代表里面的路径是规矩的。"""
        payload = _make_zip({"../escaped.txt": b"nope"})
        url, _, server = _make_server(payload)
        spec = lm.ModelSpec(
            model_id="evil",
            display_name="Evil",
            filename="bundle.zip",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            urls=(url,),
            entry_file="model.bin",
        )
        monkeypatch.setattr(lm, "MODELS", {"evil": spec})
        monkeypatch.setattr(lm, "models_root", lambda: tmp_path / "models")

        with pytest.raises(lm.DownloadError):
            lm.install("evil")

        assert not (tmp_path / "models" / "escaped.txt").exists()
        server.shutdown()
