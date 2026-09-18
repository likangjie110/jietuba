# -*- coding: utf-8 -*-
"""make_formula_tokenizer.py - 把 PP-FormulaNet 的 tokenizer 转成 ppocr_rust 认识的格式。

Rust 侧公式绑定（``rust_libs/ppocr_rust/src/formula.rs``）解码时只要「token id → token
文本」这一张表：它不跑完整 tokenizer（不需要 merges/预分词），也不需要把 50MB 的
tokenizer.json 塞进发行包。表的内容有两个来源，两者是同一份数据：

1. ``inference.yml`` 的 ``PostProcess.character_dict.fast_tokenizer_file``（PaddleOCR
   导出的配置，读它需要 pyyaml）；
2. ONNX 模型自己的 ``character`` 元数据 —— 就是上面那份 JSON 的字符串形式，读它只需
   要 onnx，而且与模型同源、不会配错。

脚本优先用 (1)，没有 pyyaml 时自动退回 (2)；两个都没有就报错并说明要装哪个。

输出的 JSON 形状::

    {
      "id_to_token": {"0": "<s>", "1": "<pad>", "23": "!", ...},
      "special_ids": [0, 1, ...],
      "vocab_size": 50000
    }

``special_ids`` 是被跳过的特殊 token（``skip_special_tokens=True`` 的那批）：PaddleOCR
的 UniMERNetDecode 先把序列截断到第一个 ``</s>``，再按「跳过特殊 token」解码，绑定要
照做，所以这里把 id 一起写出来，省得 Rust 侧靠 token 名字猜。注意词表本身的 id 从
``！``(23) 起，0~22 全是 AddedToken，**不含** blank/空格那种 PaddleOCR 老式字符表的
偏移约定（那是 det/rec 的 CTC 词表才有的事）。

用法::

    ./venv311/bin/python main/scripts/make_formula_tokenizer.py
    ./venv311/bin/python main/scripts/make_formula_tokenizer.py \\
        --config models/formula/inference.yml \\
        --onnx models/formula/PP-FormulaNet_plus-M.onnx \\
        --output models/formula/tokenizer.json

幂等：同一份输入的输出逐字节相同（id 按数值升序、紧凑 JSON、无时间戳）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple

#: 仓库根目录（本文件在 main/scripts/ 下）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 默认路径与 main/ocr/formula_engines.py 的常量保持一致
DEFAULT_CONFIG = os.path.join(REPO_ROOT, "models", "formula", "inference.yml")
DEFAULT_ONNX = os.path.join(REPO_ROOT, "models", "formula", "PP-FormulaNet_plus-M.onnx")
DEFAULT_OUTPUT = os.path.join(REPO_ROOT, "models", "formula", "tokenizer.json")


class SourceUnavailable(Exception):
    """某个来源读不了（缺依赖或缺文件），换下一个来源继续。"""


def _load_from_config(path: str) -> Dict[str, Any]:
    """从 inference.yml 里取 PostProcess.character_dict。"""
    if not os.path.exists(path):
        raise SourceUnavailable(f"配置文件不存在: {path}")
    try:
        import yaml
    except ImportError as e:  # pragma: no cover - 取决于本机装了什么
        raise SourceUnavailable(f"没装 pyyaml，无法解析 {path}（pip install pyyaml）: {e}") from e
    with open(path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    character_dict = (config or {}).get("PostProcess", {}).get("character_dict")
    if not isinstance(character_dict, dict):
        raise SourceUnavailable(f"{path} 里没有 PostProcess.character_dict")
    return character_dict


def _load_from_onnx(path: str) -> Dict[str, Any]:
    """从 ONNX 模型的 ``character`` 元数据里取同一份 character_dict。"""
    if not os.path.exists(path):
        raise SourceUnavailable(f"ONNX 模型不存在: {path}")
    try:
        import onnx
    except ImportError as e:  # pragma: no cover - 取决于本机装了什么
        raise SourceUnavailable(f"没装 onnx，无法读模型元数据 {path}（pip install onnx）: {e}") from e
    model = onnx.load(path, load_external_data=False)
    for prop in model.metadata_props:
        if prop.key == "character":
            return json.loads(prop.value)
    raise SourceUnavailable(f"{path} 的元数据里没有 character 字段")


def _build_tokenizer(character_dict: Dict[str, Any]) -> Dict[str, Any]:
    """character_dict → 绑定认的 id_to_token / special_ids。"""
    fast = character_dict.get("fast_tokenizer_file")
    if not isinstance(fast, dict):
        raise ValueError("character_dict 里没有 fast_tokenizer_file")

    id_to_token: Dict[int, str] = {}
    special_ids: List[int] = []
    for added in fast.get("added_tokens") or []:
        token_id = int(added["id"])
        id_to_token[token_id] = str(added["content"])
        if added.get("special"):
            special_ids.append(token_id)

    vocab = (fast.get("model") or {}).get("vocab")
    if not isinstance(vocab, dict) or not vocab:
        raise ValueError("fast_tokenizer_file.model.vocab 缺失或为空")
    for token, token_id in vocab.items():
        # 已由 added_tokens 占掉的 id 不再覆盖：HF 解析时 added token 优先
        id_to_token.setdefault(int(token_id), str(token))

    return {
        "id_to_token": {str(i): id_to_token[i] for i in sorted(id_to_token)},
        "special_ids": sorted(special_ids),
        "vocab_size": len(id_to_token),
    }


def build_from_sources(config_path: str, onnx_path: str) -> Tuple[Dict[str, Any], str]:
    """先试配置文件，再退回模型元数据；两条都读不了就把两边的原因一起报出来。

    返回值第二项是实际生效的来源（要打出来：走了回退路径而没人知道，排查起来很费劲）。
    """
    problems: List[str] = []
    for label, loader, path in (
        ("inference.yml", _load_from_config, config_path),
        ("ONNX 元数据", _load_from_onnx, onnx_path),
    ):
        try:
            return _build_tokenizer(loader(path)), f"{label}: {path}"
        except SourceUnavailable as e:
            problems.append(str(e))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            problems.append(f"{path} 里的 tokenizer 结构不认识: {e}")
    raise SystemExit("无法生成 tokenizer.json:\n  - " + "\n  - ".join(problems))


def write_tokenizer(payload: Dict[str, Any], output_path: str) -> None:
    """写紧凑 JSON：同一个输入永远得到同一份字节。"""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(text)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 PP-FormulaNet 的 tokenizer.json")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="inference.yml 路径")
    parser.add_argument("--onnx", default=DEFAULT_ONNX, help="ONNX 模型路径（回退来源）")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 tokenizer.json 路径")
    args = parser.parse_args(argv)

    payload, source = build_from_sources(args.config, args.onnx)
    write_tokenizer(payload, args.output)

    table = payload["id_to_token"]
    specials = payload["special_ids"]
    print(f"已写入 {args.output}")
    print(f"  来源: {source}")
    print(f"  词表条目: {payload['vocab_size']}（id {min(int(k) for k in table)}~{max(int(k) for k in table)}）")
    print(f"  特殊 token: {len(specials)} 个 -> {[table[str(i)] for i in specials[:6]]} ...")
    print(f"  抽样: {[(i, table[str(i)]) for i in (23, 24, 25)]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
