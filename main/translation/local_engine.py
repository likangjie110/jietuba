# -*- coding: utf-8 -*-
"""
local_engine.py - 离线翻译的推理层

夹在两层之间：下面是「模型文件 + 推理运行库」，上面是 provider 契约
（providers/local.py 只关心 is_configured / translate）。这里负责三件事：

1. **懒加载**：模型几百 MB，只有真正要翻译时才加载；加载过一次就常驻，
   重复翻译不重复付这个代价。
2. **切句**：神经翻译模型对超长输入会截断或劣化，按行/句切开逐段翻译再拼回。
3. **语言码映射**：应用用的是 BCP-47 风格（zh-Hans/en/ja/ko），模型用的是自己
   那套（NLLB 是 zho_Hans/eng_Latn/jpn_Jpan/kor_Hang）。映射表随模型走
   （ModelSpec.lang_map），换模型不用改代码。

运行库（ctranslate2 + 分词器）是随安装包分发的——模型可以按需下载，推理库不行：
exe 用户的 site-packages 在打包时就冻结了，pip install 对他无效。所以这里 import
失败意味着"包打得不全"，不是"用户没装"。
"""
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

from core.logger import T

# 句末标点（中英日）+ 换行。切句只为了不让长文本劣化，不需要语言学上的准确，
# 切错顶多是把两句话一起翻，不会出错。
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])|(?<=\.\s)|(?<=\n)")


def runtime_available() -> bool:
    """推理运行库是否可用。"""
    return _import_error() is None


def runtime_error() -> str:
    """运行库不可用时的原因（给界面显示）。"""
    return _import_error() or ""


def _import_error() -> Optional[str]:
    try:
        import ctranslate2  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as exc:
        return f"缺少离线翻译运行库: {exc.name}"
    return None


class LocalEngineError(Exception):
    """推理过程中的可读错误（模型坏、语言不支持等）。"""


class _Ct2Backend:
    """CTranslate2 + HuggingFace 分词器的实际调用。

    单独成类是为了让 LocalEngine 的逻辑（切句、映射、缓存）可以在没有装
    ctranslate2 的机器上被测试——测试替换掉 _load_backend 即可。
    """

    def __init__(self, model_dir: Path):
        import ctranslate2
        from transformers import AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        self._translator = ctranslate2.Translator(
            str(model_dir), device="cpu", compute_type="int8",
        )

    def translate(self, text: str, source_code: Optional[str], target_code: str) -> str:
        if source_code:
            # NLLB 这类模型用 src_lang 决定源语言，分词前必须设
            self._tokenizer.src_lang = source_code
        tokens = self._tokenizer.convert_ids_to_tokens(self._tokenizer.encode(text))
        results = self._translator.translate_batch(
            [tokens],
            target_prefix=[[target_code]],
            beam_size=1,          # 交互场景优先要快；要质量可以把这里调大
        )
        output_tokens = results[0][0].tokens
        target_ids = self._tokenizer.convert_tokens_to_ids(output_tokens)
        return self._tokenizer.decode(target_ids, skip_special_tokens=True)


def _load_backend(model_dir: Path):
    """加载真正的推理后端。测试里替换这个函数。"""
    return _Ct2Backend(model_dir)


class LocalEngine:
    """一个模型对应一个实例，首次 translate 时加载，之后常驻。"""

    def __init__(self, model_dir: Path, lang_map: Optional[Dict[str, str]] = None):
        self._model_dir = Path(model_dir)
        self._lang_map = dict(lang_map or {})
        self._backend = None
        self._lock = threading.Lock()

    # ── 加载 / 释放 ───────────────────────────────────

    @property
    def loaded(self) -> bool:
        return self._backend is not None

    def _ensure_loaded(self) -> None:
        if self._backend is not None:
            return
        with self._lock:
            if self._backend is not None:
                return
            error = _import_error()
            if error:
                raise LocalEngineError(error)
            if not self._model_dir.is_dir():
                raise LocalEngineError(f"模型目录不存在: {self._model_dir}")
            try:
                self._backend = _load_backend(self._model_dir)
            except Exception as exc:
                raise LocalEngineError(f"模型加载失败: {exc}") from exc

    def release(self) -> None:
        """释放模型占用的内存。

        Windows 上 CTranslate2 会把权重 mmap 住，文件句柄跟着模型走——不先释放就删
        模型目录，删除会失败。所以设置页"删除模型"必须先调这里。
        """
        with self._lock:
            self._backend = None

    # ── 翻译 ─────────────────────────────────────────

    def _map(self, code: Optional[str]) -> Optional[str]:
        """应用语言码 → 模型语言码；表里没有就原样传下去（很多模型直接认 en/zh）。"""
        if not code:
            return None
        return self._lang_map.get(code, code)

    def translate(self, text: str, source_lang: Optional[str] = None,
                  target_lang: str = "en") -> str:
        text = (text or "").strip()
        if not text:
            return ""
        self._ensure_loaded()

        source_code = self._map(source_lang)
        target_code = self._map(target_lang) or target_lang
        segments = [seg for seg in _SENTENCE_END.split(text) if seg.strip()]
        if not segments:
            return ""

        try:
            translated = [
                self._backend.translate(segment, source_code, target_code)
                for segment in segments
            ]
        except Exception as exc:
            raise LocalEngineError(f"翻译失败: {exc}") from exc

        # 逐段翻译会丢掉段间原有的空行/空格，用换行拼回；用户拿到的是能读的段落
        return "\n".join(part.strip() for part in translated if part.strip())


# ── 引擎缓存 ────────────────────────────────────────────
# provider 每次翻译都是新建实例（ProviderRegistry.create），模型不能跟着实例走，
# 否则每翻一句话就要重新加载几百 MB。

_engines: Dict[str, LocalEngine] = {}
_engines_lock = threading.Lock()


def get_engine(model_id: str, model_dir: Path, lang_map: Optional[Dict[str, str]] = None) -> LocalEngine:
    """取（必要时创建）某个模型的引擎实例。"""
    with _engines_lock:
        engine = _engines.get(model_id)
        if engine is None:
            engine = LocalEngine(model_dir, lang_map)
            _engines[model_id] = engine
        return engine


def release_engines(model_id: Optional[str] = None) -> None:
    """释放引擎（不传 model_id 就全释放）。删除模型前必须调。"""
    with _engines_lock:
        targets: List[str] = [model_id] if model_id else list(_engines)
        for key in targets:
            engine = _engines.pop(key, None)
            if engine is not None:
                engine.release()


def loaded_models() -> List[str]:
    """当前常驻内存的模型（设置页显示"已加载"用）。"""
    with _engines_lock:
        return [key for key, engine in _engines.items() if engine.loaded]


def log_runtime_state() -> None:
    """启动时记一条日志，排查"为什么离线翻译没生效"时有据可查。"""
    from core.logger import log_debug
    error = _import_error()
    log_debug(T("离线翻译运行库不可用: {error}", error=error) if error
              else T("离线翻译运行库可用"), "Translation")
