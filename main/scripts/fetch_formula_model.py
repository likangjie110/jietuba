# -*- coding: utf-8 -*-
"""fetch_formula_model.py - 下载 PP-FormulaNet 的 ONNX 模型与配置。

仓库**不带**公式模型（PP-FormulaNet_plus-M 的 ONNX 有近 600MB，进 git 不合适），
`models/formula/` 也在 .gitignore 里，所以想用 Rust 侧的公式识别就得先跑一次这个脚本。

来源是 HuggingFace 上的单文件 Paddle2ONNX 导出（输入 x=[N,1,384,384]，输出 token id）：

    https://huggingface.co/jinzhenj/PP-FormulaNet_plus-M_onnx

下载完还要生成绑定用的 tokenizer.json（它从模型元数据/inference.yml 里抽词表），
脚本结尾会把命令打出来。

用法::

    ./venv311/bin/python main/scripts/fetch_formula_model.py
    ./venv311/bin/python main/scripts/fetch_formula_model.py --force
    # 国内直连不通时换镜像：
    ./venv311/bin/python main/scripts/fetch_formula_model.py --endpoint https://hf-mirror.com

下载先落 `.part` 再改名：中断只会留下一个半截临时文件，不会被当成「已经下好」。
已存在的文件默认跳过（想重下加 --force）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import urllib.error
import urllib.request
from typing import List, Tuple

#: 仓库根目录（本文件在 main/scripts/ 下）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 默认模型仓库与目标目录
DEFAULT_REPO = "jinzhenj/PP-FormulaNet_plus-M_onnx"
DEFAULT_ENDPOINT = "https://huggingface.co"
DEFAULT_DIR = os.path.join(REPO_ROOT, "models", "formula")

#: (远端文件名, 落地文件名)。落地名与 main/ocr/formula_engines.py 的常量对应，
#: 改名要两边一起改。
FILES: List[Tuple[str, str]] = [
    ("inference.onnx", "PP-FormulaNet_plus-M.onnx"),
    ("inference.yml", "inference.yml"),
]

#: 流式下载的块大小
CHUNK = 1024 * 1024


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: str) -> None:
    """下载到 ``<target>.part`` 再改名；失败时把临时文件删掉。"""
    part = target + ".part"
    request = urllib.request.Request(url, headers={"User-Agent": "jietuba-formula-fetch"})
    try:
        with urllib.request.urlopen(request) as response, open(part, "wb") as handle:
            expected = int(response.headers.get("Content-Length") or 0)
            done = 0
            last_report = 0.0
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                # 进度按 10% 打一次，别把日志刷爆
                if expected and done / expected - last_report >= 0.1:
                    last_report = done / expected
                    print(f"    {done * 100 // expected}%  {_human(done)}/{_human(expected)}")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        if os.path.exists(part):
            os.remove(part)
        raise SystemExit(f"下载失败 {url}: {e}") from e
    shutil.move(part, target)


def fetch(repo: str, endpoint: str, target_dir: str, force: bool) -> List[str]:
    """下载缺失的文件，返回落地路径列表。"""
    os.makedirs(target_dir, exist_ok=True)
    paths: List[str] = []
    for remote_name, local_name in FILES:
        target = os.path.join(target_dir, local_name)
        if os.path.exists(target) and not force:
            print(f"已存在，跳过: {target}（{_human(os.path.getsize(target))}）")
            paths.append(target)
            continue
        url = f"{endpoint.rstrip('/')}/{repo}/resolve/main/{remote_name}"
        print(f"下载 {url}")
        download(url, target)
        print(f"  -> {target}（{_human(os.path.getsize(target))}）")
        paths.append(target)
    return paths


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载 PP-FormulaNet ONNX 模型与配置")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="HuggingFace 仓库 id")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="HF 站点（可用镜像）")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="落地目录")
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    args = parser.parse_args(argv)

    paths = fetch(args.repo, args.endpoint, args.dir, args.force)
    print("\n校验（sha256）：")
    for path in paths:
        print(f"  {_sha256(path)}  {os.path.basename(path)}")
    print(
        "\n下一步生成绑定用的 tokenizer.json：\n"
        f"  {sys.executable} {os.path.join(REPO_ROOT, 'main', 'scripts', 'make_formula_tokenizer.py')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
