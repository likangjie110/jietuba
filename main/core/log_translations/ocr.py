"""main/ocr/ 目录下 log_* 调用的中→英翻译表。"""

TRANSLATIONS: dict[str, str] = {
    "高精度引擎可用 (Rust FFI)": "High-precision engine available (Rust FFI)",
    "高精度引擎不可用 (系统组件未找到)": "High-precision engine unavailable (system component not found)",
    "高精度引擎检测失败: {e}": "High-precision engine detection failed: {e}",
    "windows_media_ocr 库不可用: {e}": "windows_media_ocr library unavailable: {e}",
    "解析 ppocr_rust 模型路径失败: {e}": "Failed to resolve ppocr_rust model path: {e}",
    "ppocr_rust 引擎可用 (Rust + ort PP-OCR)": "ppocr_rust engine available (Rust + ort PP-OCR)",
    "ppocr_rust 已安装但缺少模型文件": "ppocr_rust is installed but model files are missing",
    "ppocr_rust 未安装": "ppocr_rust is not installed",
    "ppocr_rust 引擎检测失败: {e}": "ppocr_rust engine detection failed: {e}",
    "ppocr_rust 引擎不可用": "ppocr_rust engine unavailable",
    "不支持的引擎类型: {engine_type}": "Unsupported engine type: {engine_type}",
    "引擎不可用: {engine_type}": "Engine unavailable: {engine_type}",
    "切换引擎: {old} -> {new}": "Switching engine: {old} -> {new}",
    "使用 {engine} 引擎 ({label})": "Using {engine} engine ({label})",
    "Windows OCR 支持的语言: {langs}": "Windows OCR supported languages: {langs}",
    "自动选择引擎: {engine}": "Auto-selected engine: {engine}",
    "OCR 引擎识别异常: {e}\n{tb}": "OCR engine raised during recognition: {e}\n{tb}",
    "扫描插件目录失败: {e}": "Failed to scan plugin directory: {e}",
    "插件与已加载模块重名，跳过: {name}": "Plugin name collides with a loaded module, skipped: {name}",
    "插件缺少 register(manager)，跳过: {name}": "Plugin has no register(manager), skipped: {name}",
    "加载 OCR 插件失败: {name}: {e}": "Failed to load OCR plugin: {name}: {e}",
    "已加载 OCR 插件: {names}": "Loaded OCR plugins: {names}",
    "正在初始化 ppocr_rust 引擎 (Rust + ort)...": "Initializing ppocr_rust engine (Rust + ort)...",
    "ppocr_rust 引擎初始化成功": "ppocr_rust engine initialized successfully",
    "正在初始化高精度引擎 (Rust FFI)...": "Initializing high-precision engine (Rust FFI)...",
    "高精度引擎初始化成功": "High-precision engine initialized successfully",
    "初始化 windows_media_ocr 引擎(语言配置: {language} -> {ocr_lang})": "Initializing windows_media_ocr engine (language: {language} -> {ocr_lang})",
    "windows_media_ocr 引擎初始化成功": "windows_media_ocr engine initialized successfully",
    "OCR 管理器状态已重置": "OCR manager state reset",
    "释放 OCR 资源时出错: {e}": "Error while releasing OCR resources: {e}",
    "ppocr_rust 初始化失败: {e}\n{tb}": "ppocr_rust initialization failed: {e}\n{tb}",
    "高精度引擎初始化失败: {e}\n{tb}": "High-precision engine initialization failed: {e}\n{tb}",
    "windows_media_ocr 初始化失败: {e}\n{tb}": "windows_media_ocr initialization failed: {e}\n{tb}",
    "ppocr_rust 识别失败: {e}\n{tb}": "ppocr_rust recognition failed: {e}\n{tb}",
    "高精度引擎识别失败: {e}\n{tb}": "High-precision engine recognition failed: {e}\n{tb}",
    "windows_media_ocr 识别失败: {e}\n{tb}": "windows_media_ocr recognition failed: {e}\n{tb}",

    # -- ocr/formula.py（公式识别）--
    "公式引擎未注册: {name}": "Formula engine is not registered: {name}",
    "公式引擎不可用: {name}": "Formula engine is unavailable: {name}",
    "公式识别不可用：没有可用的公式引擎":
        "Formula recognition is unavailable: no formula engine available",
    "公式识别异常: {e}": "Formula recognition raised: {e}",
    "公式引擎没识别到内容: {name}": "Formula engine recognized nothing: {name}",

    # -- ocr/formula_engines.py（内建公式引擎）--
    "解析 PP-FormulaNet 模型路径失败: {e}": "Failed to resolve PP-FormulaNet model path: {e}",
    "PP-FormulaNet 引擎不可用: {reason}": "PP-FormulaNet engine unavailable: {reason}",
    "ppocr_rust 公式引擎可用 (PP-FormulaNet)": "ppocr_rust formula engine available (PP-FormulaNet)",
    "正在初始化 PP-FormulaNet 引擎 (Rust + ort)...":
        "Initializing PP-FormulaNet engine (Rust + ort)...",
    "PP-FormulaNet 引擎初始化成功": "PP-FormulaNet engine initialized successfully",
    "PP-FormulaNet 初始化失败: {e}\n{tb}": "PP-FormulaNet initialization failed: {e}\n{tb}",
    "PP-FormulaNet 识别失败: {e}\n{tb}": "PP-FormulaNet recognition failed: {e}\n{tb}",
    "外部公式服务未配置 URL": "External formula service URL is not configured",
    "外部公式服务没有返回 LaTeX": "External formula service returned no LaTeX",
    "外部公式服务请求失败: {e}\n{tb}": "External formula service request failed: {e}\n{tb}",
    "外部公式服务连接失败: {e}": "External formula service connection failed: {e}",

    # ocr/vision_models.py
    "查询系统密钥库": "Querying the system key store",
    "已把视觉模型的密钥迁移到系统密钥库":
        "Vision model keys migrated to the system key store",
}
