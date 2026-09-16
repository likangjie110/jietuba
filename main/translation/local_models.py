# -*- coding: utf-8 -*-
"""
local_models.py - 离线翻译模型的下载与本地库

离线引擎的模型不随安装包分发（几百 MB，装了就白占没用到的用户的磁盘），改成
用户第一次要用时按需下载。这个模块只管两件事：**模型清单**和**下载/校验/删除**，
不认识任何推理引擎——CTranslate2 也好、ONNX 也好，拿到模型文件路径自己去加载。

放在用户数据目录而不是 exe 同级目录：安装包可能装在 Program Files 这种只读位置，
而下载的模型是要写盘的。（OCR 的模型是随包分发的只读资源，所以放 exe 同级，
两者规矩不同是有意的。）

下载行为：
- 支持断点续传（HTTP Range）。几百 MB 在国内网络下一次成功是奢望，断了从 0 开始
  重来会直接劝退用户。
- 主源失败自动换镜像源，`.part` 文件跨镜像复用（同一份内容）。
- 下完校验 sha256 再原子改名落盘；校验不过就删掉重下，不留下半个可用文件。
"""
import hashlib
import os
import shutil
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

from core.constants import get_app_data_dir

DOWNLOAD_CHUNK = 256 * 1024
CONNECT_TIMEOUT = 15          # 连接/首字节超时；下载过程中的读超时由 socket 默认值兜底
PART_SUFFIX = ".part"


def models_root() -> Path:
    """离线模型的存放目录（用户数据目录下，可写）。"""
    return get_app_data_dir() / "models" / "mt"


@dataclass(frozen=True)
class ModelSpec:
    """一个可下载的离线模型。

    filename 以 .zip 结尾时按压缩包处理：校验通过后解包到模型目录、删掉压缩包。
    推理引擎的模型基本都是一整个目录（模型权重 + 配置 + 分词器），打包成一个文件
    下载才能做到断点续传和单点校验。

    size_bytes 用于下载前预检磁盘和进度条总长。安装了与否的判断见 is_installed()。
    """

    model_id: str
    display_name: str
    filename: str
    size_bytes: int
    sha256: str
    languages: tuple = ()
    urls: tuple = ()          # 按顺序尝试：主源在前，镜像在后
    license_note: str = ""    # 要显示给用户的许可说明（比如"仅限非商用"）
    lang_map: dict = field(default_factory=dict)   # 应用语言码 → 引擎语言码
    entry_file: str = ""      # 解包后判断"装好了"的依据文件；空表示 filename 本身就是模型文件


# ── 模型清单 ────────────────────────────────────────────
# 新增模型：转好格式 → 上传到 GitHub Releases 附件（或镜像）→ 在这里登记。
# urls 至少给两个源：主源挂了要有退路。
MODELS: Dict[str, ModelSpec] = {}


def register_model(spec: ModelSpec) -> None:
    """登记一个模型（清单也可以由外部包注册，便于以后扩展别的引擎）。"""
    MODELS[spec.model_id] = spec


class DownloadCancelled(Exception):
    """用户取消下载。"""


class DownloadError(Exception):
    """所有源都失败，或校验不过。"""


class DownloadIncomplete(Exception):
    """本轮没下完（连接中断）。

    和「校验失败」要区别对待：没下完的 `.part` 是**有用的**，下次接着下就行；
    校验失败的残件是坏的，必须删掉。混在一起处理的话，网络一抖就要从 0 重下 600MB。
    """


ProgressCallback = Callable[[int, int], None]     # (已下载, 总字节)


def model_dir(model_id: str) -> Path:
    """模型的存放目录（不保证已安装）。引擎从这里加载。"""
    return models_root() / model_id


def model_path(model_id: str) -> Path:
    """下载产物的预期路径（不保证存在）。"""
    return model_dir(model_id) / MODELS[model_id].filename


def is_installed(model_id: str) -> bool:
    """模型是否已安装。

    刻意只做便宜的存在性检查（单文件看大小、压缩包看解包后的入口文件），不做全量
    哈希：600MB 哈希要一两秒，而这个过程会在每次选引擎、每次启动时跑。真正的完整性
    由下载完成时那一次校验保证。
    """
    spec = MODELS.get(model_id)
    if spec is None:
        return False
    try:
        if spec.entry_file:
            return (model_dir(model_id) / spec.entry_file).is_file()
        path = model_path(model_id)
        return path.is_file() and path.stat().st_size == spec.size_bytes
    except OSError:
        return False


def installed_models() -> list:
    return [model_id for model_id in MODELS if is_installed(model_id)]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_disk_space(needed: int, directory: Path) -> None:
    """提前看一眼磁盘，别让用户等到 99% 才失败。"""
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        return                      # 拿不到就算了，不因为这个拦住下载
    # 留 64MB 余量：解包/临时文件也要地方
    if free < needed + 64 * 1024 * 1024:
        raise DownloadError(
            f"磁盘空间不足：需要 {needed / 1048576:.0f}MB，可用 {free / 1048576:.0f}MB"
        )


def _download_from(url: str, target: Path, progress: Optional[ProgressCallback],
                   cancel: Optional[threading.Event]) -> None:
    """从单个源下载到 target，已存在的部分会被续传。"""
    offset = target.stat().st_size if target.exists() else 0
    request = urllib.request.Request(url, headers={"User-Agent": "jietuba"})
    if offset:
        request.add_header("Range", f"bytes={offset}-")

    with urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT) as response:
        # 服务端不支持 Range 时会回 200 并从头给整份——此时必须丢掉已下载的部分，
        # 否则拼出来的是「半份 + 整份」，文件看着大小对不上、哈希也过不了。
        if offset and response.status != 206:
            offset = 0

        length = int(response.headers.get("Content-Length") or 0)
        total = offset + length if length else 0
        done = offset

        with open(target, "wb" if offset == 0 else "ab") as handle:
            while True:
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled()
                chunk = response.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)

        # 连接中途断掉时 read() 只会安静地返回空，不会抛异常（Content-Length 对不上
        # 这件事得自己查）。不查的话就变成"下完一份短文件 → 校验失败 → 删掉重来"，
        # 断点续传等于没有。
        if total and done < total:
            raise DownloadIncomplete(f"未下完: {done}/{total}")


def _unpack(archive: Path, directory: Path) -> None:
    """把压缩包解到模型目录。

    逐条检查解出来的路径没有跑到目录外面：包是我们自己校验过的，但校验只能证明
    "和我上传的那份一致"，挡不住压缩包本身构造出的 ../ 路径（也就是 zip slip）。
    """
    directory = directory.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (directory / member.filename).resolve()
            if not target.is_relative_to(directory):
                raise DownloadError(f"压缩包内路径越界: {member.filename}")
        bundle.extractall(directory)


def install(model_id: str, progress: Optional[ProgressCallback] = None,
            cancel: Optional[threading.Event] = None) -> Path:
    """下载并安装模型，返回**模型目录**（多文件模型由引擎从该目录加载）。

    这个函数会在当前线程里阻塞到下载结束——调用方负责放到工作线程（界面线程调用
    会冻住 UI）。
    """
    spec = MODELS.get(model_id)
    if spec is None:
        raise DownloadError(f"未登记的模型: {model_id}")
    if is_installed(model_id):
        return model_dir(model_id)

    directory = model_dir(model_id)
    directory.mkdir(parents=True, exist_ok=True)
    _check_disk_space(spec.size_bytes, directory)

    final = model_path(model_id)
    part = final.with_name(final.name + PART_SUFFIX)

    last_error: Optional[Exception] = None
    for url in spec.urls:
        try:
            _download_from(url, part, progress, cancel)
            if _sha256(part) != spec.sha256:
                # 校验不过的残件没有续传价值，删掉重来
                part.unlink(missing_ok=True)
                raise DownloadError(f"校验失败: {url}")
            if spec.filename.lower().endswith(".zip"):
                _unpack(part, directory)     # 解包成功之后才删压缩包
                part.unlink(missing_ok=True)
            else:
                os.replace(part, final)      # 同盘原子改名，不会留下半个可用文件
            return directory
        except DownloadCancelled:
            raise                            # 取消是用户的意图，不换源重试
        except Exception as exc:
            last_error = exc
            continue

    raise DownloadError(f"所有下载源都失败: {last_error}")


def uninstall(model_id: str) -> bool:
    """删除已安装的模型，返回是否真的删掉了东西。"""
    directory = model_dir(model_id)
    if not directory.exists():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True


def partial_size(model_id: str) -> int:
    """未完成下载的字节数（设置页显示「已下载 xx%」用；0 表示没有残件）。"""
    spec = MODELS.get(model_id)
    if spec is None:
        return 0
    part = model_path(model_id).with_name(spec.filename + PART_SUFFIX)
    try:
        return part.stat().st_size if part.exists() else 0
    except OSError:
        return 0
