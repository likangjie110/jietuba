# -*- coding: utf-8 -*-
"""文字识别的模型档位：模型文件在不在，决定这一档能不能选。

PP-OCR 的 det/rec 是两个独立的 onnx。仓库里默认带一套（v6 small），用户可以自己把
更大的模型放进 `models/`，那时档位列表就该多出对应选项——所以档位表**按文件是否
存在**现算，而不是写死一张「支持哪些档位」的清单：写死的话，用户放了模型也选不到，
没放模型却能选到一个点了就报错的档位。

档位名与文件名的对应写在 `TIERS` 里；`ppocr_rust` 的构建变体（每档一个专用扩展）由
`engine` 字段声明，没有它就用通用的 `ppocr_rust` 扩展。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core.logger import T, log_debug, log_warning


@dataclass(frozen=True)
class ModelTier:
    """一个可选的识别档位。"""

    id: str
    label: str
    det: str            # det 模型文件名
    rec: str            # rec 模型文件名
    engine: str = ""    # 专用引擎名（空 = 通用 ppocr_rust）


#: 档位表（顺序即界面顺序）。文件名与 PaddleOCR 官方发布名一致。
TIERS = (
    ModelTier("v6_ultra_small", "PP-OCRv6 Ultra Small", "PP-OCRv6_det_ultra_small.onnx",
              "PP-OCRv6_rec_ultra_small.onnx"),
    ModelTier("v6_small", "PP-OCRv6 Small", "PP-OCRv6_det_small.onnx",
              "PP-OCRv6_rec_small.onnx"),
    ModelTier("v6_medium", "PP-OCRv6 Medium", "PP-OCRv6_det_medium.onnx",
              "PP-OCRv6_rec_medium.onnx"),
    ModelTier("v5_small", "PP-OCRv5 Small", "PP-OCRv5_det_small.onnx",
              "PP-OCRv5_rec_small.onnx"),
    ModelTier("v4_small", "PP-OCRv4 Small", "PP-OCRv4_det_small.onnx",
              "PP-OCRv4_rec_small.onnx"),
)

#: 默认档位（仓库自带的模型就是它）
DEFAULT_TIER = "v6_small"

#: 配置键
TIER_SETTING_KEY = "ocr_model_tier"


def model_dirs() -> list:
    """模型可能所在的目录，按优先级排列（与 engines._ppocr_model_paths 同一套规则）。"""
    import sys

    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "models"))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "models"))
    try:
        from core.resource_manager import ResourceManager

        candidates.append(ResourceManager.get_resource_path("models"))
    except Exception:
        pass
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "models"))
    seen = []
    for path in candidates:
        if path and path not in seen:
            seen.append(path)
    return seen


def tier_paths(tier: ModelTier) -> tuple:
    """档位对应的 (det, rec) 绝对路径；哪个文件缺就返回 None。"""
    for directory in model_dirs():
        det = os.path.join(directory, tier.det)
        rec = os.path.join(directory, tier.rec)
        if os.path.exists(det) and os.path.exists(rec):
            return det, rec
    return None, None


def is_tier_available(tier_id: str) -> bool:
    tier = tier_by_id(tier_id)
    if tier is None:
        return False
    det, rec = tier_paths(tier)
    return bool(det and rec)


def tier_by_id(tier_id: str) -> ModelTier | None:
    for tier in TIERS:
        if tier.id == tier_id:
            return tier
    return None


def available_tiers() -> list:
    """当前机器上真的能用的档位（两个模型文件都在）。"""
    available = []
    for tier in TIERS:
        det, rec = tier_paths(tier)
        if det and rec:
            available.append((tier.id, tier.label))
    if not available:
        log_warning(T("没有找到任何可用的 OCR 模型档位"), "OCR")
    return available


def selected_tier(config_manager=None, *, fallback: str = DEFAULT_TIER) -> str:
    """当前选中的档位：配置里有且可用就用它，否则退回第一个可用档位。"""
    available = dict(available_tiers())
    if config_manager is not None:
        try:
            configured = str(config_manager.get_app_setting(TIER_SETTING_KEY, fallback) or "")
        except Exception as e:
            from core.logger import log_exception

            log_exception(e, T("读取 OCR 模型档位"))
            configured = ""
        if configured in available:
            return configured
        if configured:
            log_debug(T("配置的 OCR 档位不可用，改用可用档位: {tier}", tier=configured), "OCR")
    if fallback in available:
        return fallback
    return next(iter(available), fallback)


def resolve(tier_id: str) -> tuple:
    """档位 → (engine_name, det_path, rec_path)；档位不可用时返回 (None, None, None)。"""
    tier = tier_by_id(tier_id)
    if tier is None:
        return None, None, None
    det, rec = tier_paths(tier)
    if not det or not rec:
        log_debug(T("OCR 档位不可用（缺模型文件）: {tier}", tier=tier_id), "OCR")
        return None, None, None
    return (tier.engine or "ppocr_rust"), det, rec
