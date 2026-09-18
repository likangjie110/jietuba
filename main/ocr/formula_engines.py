# -*- coding: utf-8 -*-
"""formula_engines.py - 内建公式识别引擎。

两个引擎的分工（见 ``builtin_formula_engines()``，顺序即优先级）：

    ppocr_formula   Rust 侧 PP-FormulaNet 绑定，本地推理，离线可用但有模型体积
    external_service 自建/第三方 HTTP 服务，本机不落模型，但要联网、要填 URL

两者都实现 ``formula.FormulaEngine`` 这一份契约，注册表（``formula.py``）不认识具体
实现。本模块只负责「造出引擎」，注册由调用方决定——本仓库干净环境里两个引擎都不可用
（没模型、没配 URL），所以调用方必须按「不可用」显式降级。

外部服务只认本仓库定义的这一版协议：POST ``{"image_base64": "<PNG base64>"}``，
响应接受 ``{"latex"}`` / ``{"result"}`` / ``{"data": {"latex"}}`` 三种形状。
"""
import base64
import importlib.util
import json
import os
import sys
import threading
import traceback as _tb
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from core.logger import T

from .engine import OcrEngine, ocr_log
from .formula import FormulaEngine

#: PP-FormulaNet 的模型与 tokenizer，相对资源根（开发环境是仓库根，打包后是 exe 同级
#: 或 _MEIPASS）。改文件名只改这里。
#: 模型是 PP-FormulaNet_plus-M 的 ONNX 导出（单文件、输入 x=[N,1,384,384]，输出 token id）；
#: tokenizer.json 由 main/scripts/make_formula_tokenizer.py 从 inference.yml / 模型元数据生成。
FORMULA_MODEL_REL_PATH = "models/formula/PP-FormulaNet_plus-M.onnx"
FORMULA_TOKENIZER_REL_PATH = "models/formula/tokenizer.json"

#: 外部公式服务的默认超时（秒）
DEFAULT_SERVICE_TIMEOUT = 15


# ══════════════════════════════════════════════════════════
# ppocr_formula（Rust 侧 PP-FormulaNet 绑定）
# ══════════════════════════════════════════════════════════

def formula_model_paths() -> Tuple[Optional[str], Optional[str]]:
    """返回 (模型路径, tokenizer 路径)。

    查找顺序与 `ocr/engines.py::_ppocr_model_paths` 保持一致，因为打包脚本
    （`build_macos_app.py` / `build_with_ocr_onefile.py`）是把 `models/` 放到**可执行文件
    同级目录**的，而 `ResourceManager` 走的是 `_MEIPASS` —— 两者在 .app 里不是同一个地方：

      1) 打包后：``dirname(sys.executable)/models``（外置，启动快、可替换）
      2) 打包后回退：``_MEIPASS/models``
      3) 开发环境：仓库根 ``models/``

    三条都不中时返回首选路径，让 `_missing_model_files` 如实报出缺哪个文件。
    """
    bases: List[str] = []
    try:
        if getattr(sys, "frozen", False):
            bases.append(os.path.join(os.path.dirname(sys.executable), "models"))
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                bases.append(os.path.join(meipass, "models"))
        else:
            from core.resource_manager import ResourceManager

            bases.append(ResourceManager.get_resource_path("models"))
    except Exception as e:
        ocr_log(T("解析 PP-FormulaNet 模型路径失败: {e}", e=e), "DEBUG")

    def pair(base: str) -> Tuple[str, str]:
        return (
            os.path.join(base, "formula", os.path.basename(FORMULA_MODEL_REL_PATH)),
            os.path.join(base, "formula", os.path.basename(FORMULA_TOKENIZER_REL_PATH)),
        )

    for base in bases:
        model, tokenizer = pair(base)
        if os.path.exists(model) and os.path.exists(tokenizer):
            return model, tokenizer

    if not bases:
        return None, None
    return pair(bases[0])


def _missing_model_files() -> List[str]:
    """列出缺失的模型文件（相对路径），齐备时返回空表。"""
    model, tokenizer = formula_model_paths()
    missing = []
    for rel_path, path in (
        (FORMULA_MODEL_REL_PATH, model),
        (FORMULA_TOKENIZER_REL_PATH, tokenizer),
    ):
        if not path or not os.path.exists(path):
            missing.append(rel_path)
    return missing


def probe_ppocr_formula() -> Tuple[bool, Optional[str]]:
    """探测 Rust 公式绑定与模型，返回 ``(是否可用, 不可用原因)``。

    单独抽成模块级函数是为了留一个替换点：测试注入探测结果即可，不必真的装扩展、
    也不需要仓库里有模型文件。

    不可用原因把「缺绑定」和「缺文件」分开说：发行版没带模型是常态，一句「不可用」
    会让用户以为装上扩展就好了。
    """
    reasons: List[str] = []
    module = None
    try:
        if importlib.util.find_spec("ppocr_rust") is None:
            reasons.append("ppocr_rust 扩展未安装")
        else:
            import ppocr_rust

            module = ppocr_rust
            if not hasattr(module, "FormulaEngine"):
                reasons.append("ppocr_rust 模块没有导出 FormulaEngine（当前扩展只带文本 OCR 绑定）")
    except Exception as e:
        reasons.append(f"ppocr_rust 导入失败: {e}")

    missing = _missing_model_files()
    if missing:
        reasons.append("缺少模型/tokenizer 文件: " + ", ".join(missing))

    if module is not None and not missing and hasattr(module, "FormulaEngine"):
        # 扩展自己知道模型文件是否完整（比如 tokenizer 语法、onnx 的 meta），
        # 这里只是把判断权交回去；老版本扩展没这个函数就按文件存在性作数
        checker = getattr(module, "formula_model_present", None)
        if callable(checker):
            model, tokenizer = formula_model_paths()
            try:
                if not checker(model, tokenizer):
                    reasons.append("ppocr_rust 报告模型/tokenizer 不可用")
            except Exception as e:
                reasons.append(f"ppocr_rust.formula_model_present 调用失败: {e}")

    if reasons:
        reason = "；".join(reasons)
        ocr_log(T("PP-FormulaNet 引擎不可用: {reason}", reason=reason), "DEBUG")
        return False, reason

    ocr_log(T("ppocr_rust 公式引擎可用 (PP-FormulaNet)"), "DEBUG")
    return True, None


class PPFormulaNetEngine(FormulaEngine):
    """PP-FormulaNet —— Rust + ONNX Runtime 的公式识别（ppocr_rust 的公式绑定）。

    模型与文本 OCR 是两套文件，本仓库默认不带，所以 ``is_available()`` 在干净环境里
    就是 False。
    """

    name = "ppocr_formula"
    label = "PP-FormulaNet (Rust)"

    def __init__(self):
        super().__init__()
        self._engine = None          # ppocr_rust.FormulaEngine 实例，None 即未初始化
        self._init_lock = threading.Lock()
        self._probe: Optional[Tuple[bool, Optional[str]]] = None

    def _probe_once(self) -> Tuple[bool, Optional[str]]:
        if self._probe is None:
            self._probe = probe_ppocr_formula()
        return self._probe

    def is_available(self) -> bool:
        available, reason = self._probe_once()
        if reason:
            self.last_error = reason
        return available

    def initialize(self) -> bool:
        if not self.is_available():
            self.last_error = self.last_error or "PP-FormulaNet 引擎不可用"
            ocr_log(T("PP-FormulaNet 引擎不可用: {reason}", reason=self.last_error), "WARN")
            return False
        if self._engine is not None:
            return True
        with self._init_lock:
            if self._engine is not None:
                return True
            model, tokenizer = formula_model_paths()
            try:
                ocr_log(T("正在初始化 PP-FormulaNet 引擎 (Rust + ort)..."), "DEBUG")
                import ppocr_rust

                self._engine = ppocr_rust.FormulaEngine(model, tokenizer)
                ocr_log(T("PP-FormulaNet 引擎初始化成功"), "DEBUG")
                return True
            except Exception as e:
                self.last_error = f"PP-FormulaNet 初始化失败: {str(e)}"
                ocr_log(
                    T("PP-FormulaNet 初始化失败: {e}\n{tb}", e=str(e), tb=_tb.format_exc()),
                    "ERROR",
                )
                self._engine = None
                return False

    def recognize_formula(self, pixmap) -> str:
        if not self.is_available():
            return ""
        if self._engine is None and not self.initialize():
            return ""
        try:
            image = OcrEngine._as_image(pixmap)
            if image.isNull():
                return ""

            # 取到局部变量：release() 可能在别的线程把 self._engine 置空
            engine = self._engine
            if engine is None:
                return ""
            latex = engine.recognize_formula(OcrEngine.png_bytes(image)) or ""
            return latex.strip()
        except Exception as e:
            self.last_error = f"PP-FormulaNet 识别失败: {str(e)}"
            ocr_log(
                T("PP-FormulaNet 识别失败: {e}\n{tb}", e=str(e), tb=_tb.format_exc()),
                "ERROR",
            )
            return ""

    def release(self) -> None:
        # 会话持有 ONNX 模型（几十 MB），能当场释放的不要等进程退出
        if self._engine is not None:
            close = getattr(self._engine, "close", None)
            if callable(close):
                close()
            self._engine = None


# ══════════════════════════════════════════════════════════
# external_service（HTTP 公式服务）
# ══════════════════════════════════════════════════════════

def _post_json(
    url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: float
) -> Dict[str, Any]:
    """POST 一份 JSON 并返回解析后的响应体。

    传输这层单独抽出来是为了留替换点：协议层（请求长什么样、响应怎么取）能在没有
    真实服务端的机器上验证，测试替换本函数即可。
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", **headers},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("公式服务返回的不是 JSON 对象")
    return data


#: 1×1 白色 PNG。verify() 拿它探一次连通性，就为不依赖截图/绘图环境。
_WHITE_PIXEL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR42mP4//8/AAX+Av4zEpUUAAAAAElFTkSuQmCC"
)


def _white_pixel_png() -> bytes:
    return base64.b64decode(_WHITE_PIXEL_PNG_B64)


class ExternalFormulaServiceEngine(FormulaEngine):
    """外部公式服务：把图片 base64 塞进 JSON 发给配置的 URL。

    URL/密钥/超时都现读配置，不做可用性缓存——设置页里填完 URL 就期望能立刻可用。
    """

    name = "external_service"
    label = "External Formula Service"

    # ── 配置 ──────────────────────────────────────────

    def _service_config(self) -> Tuple[str, str, float]:
        """``(url, api_key, timeout)``。缺 URL 是正常状态（用户没配），不是错误。"""
        from settings import get_tool_settings_manager

        manager = get_tool_settings_manager()
        url = str(manager.get_app_setting("formula_service_url", "") or "").strip()
        api_key = str(manager.get_app_setting("formula_service_api_key", "") or "").strip()
        try:
            timeout = float(manager.get_app_setting("formula_service_timeout", DEFAULT_SERVICE_TIMEOUT))
        except (TypeError, ValueError):
            timeout = float(DEFAULT_SERVICE_TIMEOUT)
        if timeout <= 0:
            timeout = float(DEFAULT_SERVICE_TIMEOUT)
        return url, api_key, timeout

    @staticmethod
    def _headers(api_key: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    @staticmethod
    def _extract_latex(data: Dict[str, Any]) -> str:
        """从响应的三种形状里取 LaTeX：``{"latex"}`` / ``{"result"}`` / ``{"data": {"latex"}}``。"""
        if not isinstance(data, dict):
            return ""
        for key in ("latex", "result"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        nested = data.get("data")
        if isinstance(nested, dict):
            value = nested.get("latex")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    # ── FormulaEngine ────────────────────────────────

    def is_available(self) -> bool:
        url, _, _ = self._service_config()
        if not url:
            self.last_error = "外部公式服务未配置 URL"
            return False
        return True

    def recognize_formula(self, pixmap) -> str:
        url, api_key, timeout = self._service_config()
        if not url:
            self.last_error = "外部公式服务未配置 URL"
            ocr_log(T("外部公式服务未配置 URL"), "WARN")
            return ""
        try:
            image = OcrEngine._as_image(pixmap)
            if image.isNull():
                return ""
            payload = {"image_base64": base64.b64encode(OcrEngine.png_bytes(image)).decode("ascii")}
            data = _post_json(url, payload, self._headers(api_key), timeout)
            latex = self._extract_latex(data)
            if not latex:
                self.last_error = "外部公式服务没有返回 LaTeX"
                ocr_log(T("外部公式服务没有返回 LaTeX"), "INFO")
                return ""
            self.last_error = None
            return latex
        except Exception as e:
            self.last_error = f"外部公式服务请求失败: {str(e)}"
            ocr_log(
                T("外部公式服务请求失败: {e}\n{tb}", e=str(e), tb=_tb.format_exc()),
                "ERROR",
            )
            return ""

    def verify(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """拿一张 1×1 白图试一次请求，返回 ``(是否连通, 失败原因)``。

        设置页的「验证」按钮用它——参数留空取当前配置；填了就用填的值，这样用户能
        在保存前先试。只要能应答就算连通：白图本来就识别不出公式，拿「有没有 LaTeX」
        判连通会把正常的空响应判成失败。
        """
        cfg_url, cfg_key, cfg_timeout = self._service_config()
        url = (url if url is not None else cfg_url).strip()
        api_key = (api_key if api_key is not None else cfg_key).strip()
        if not url:
            return False, "未填写服务地址"
        if timeout is None:
            timeout = cfg_timeout
        try:
            payload = {"image_base64": base64.b64encode(_white_pixel_png()).decode("ascii")}
            _post_json(url, payload, self._headers(api_key), timeout)
        except Exception as e:
            self.last_error = f"外部公式服务连接失败: {str(e)}"
            ocr_log(T("外部公式服务连接失败: {e}", e=str(e)), "WARN")
            return False, f"连接失败: {e}"
        self.last_error = None
        return True, ""


def builtin_formula_engines() -> List[FormulaEngine]:
    """本次构建注册的内建公式引擎。列表顺序 = 自动选择时的优先级。"""
    return [PPFormulaNetEngine(), ExternalFormulaServiceEngine()]


__all__ = [
    "DEFAULT_SERVICE_TIMEOUT",
    "FORMULA_MODEL_REL_PATH",
    "FORMULA_TOKENIZER_REL_PATH",
    "ExternalFormulaServiceEngine",
    "PPFormulaNetEngine",
    "builtin_formula_engines",
    "formula_model_paths",
    "probe_ppocr_formula",
]
