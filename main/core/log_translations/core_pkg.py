"""main/core/ 目录下 log_* 调用的中→英翻译表。"""

TRANSLATIONS: dict[str, str] = {
    # core/logger.py 自身的启动/关闭诊断信息
    "\n{sep}\nJietuba 截图工具 - 运行日志\n启动时间: {start_time}\n日志目录: {log_dir}\n"
    "日志级别: {file_level} (文件) / {console_level} (控制台)\n{sep}\n":
        "\n{sep}\nJietuba Screenshot Tool - Runtime Log\nStarted: {start_time}\nLog directory: {log_dir}\n"
        "Log level: {file_level} (file) / {console_level} (console)\n{sep}\n",
    "[WARN] [Logger] 日志功能已禁用": "[WARN] [Logger] Logging is disabled",
    "[WARN] [Logger] 日志系统已经初始化": "[WARN] [Logger] Logging system already initialized",
    "[OK] [Logger] 日志系统启动成功，日志文件：{log_path}": "[OK] [Logger] Logging system started, log file: {log_path}",
    "[ERROR] [Logger] 无法创建日志文件: {e}": "[ERROR] [Logger] Failed to create log file: {e}",
    "日志级别已设置为: {level_name}": "Log level set to: {level_name}",
    "📝 [Logger] 日志已启用": "📝 [Logger] Logging enabled",
    "🔇 [Logger] 日志已禁用": "🔇 [Logger] Logging disabled",
    "[Logger] 日志系统已初始化，无法更改日志目录": "[Logger] Logging system already initialized, cannot change log directory",
    "[OK] [Logger] 日志目录已设置为: {log_dir}": "[OK] [Logger] Log directory set to: {log_dir}",
    "🛑 [Logger] 日志系统关闭": "🛑 [Logger] Logging system shut down",
    "✂️ [日志截断] {name} 超过 {max_kb} KB，已截断": "✂️ [Log truncated] {name} exceeded {max_kb} KB, truncated",
    "[WARN] [日志截断] 处理 {name} 失败: {e}": "[WARN] [Log truncation] Failed to process {name}: {e}",
    "🗑️ [日志清理] 已删除 {deleted_count} 个过期日志文件（保留 {retention_days} 天）":
        "🗑️ [Log cleanup] Deleted {deleted_count} expired log file(s) (retention: {retention_days} days)",
    "[WARN] [日志清理] 清理失败: {e}": "[WARN] [Log cleanup] Cleanup failed: {e}",

    # core/constants.py
    "系统默认文字字体: {family}": "System default text font: {family}",

    # core/platform/shell.py
    "打开失败，系统没有可用的打开方式: {path}": "Open failed, no handler available: {path}",
    "用默认程序打开 {path}": "Opening {path} with the default application",
    "在文件管理器中定位 {path}": "Revealing {path} in the file manager",
    "桌面快捷方式尚未支持当前平台，跳过": "Desktop shortcuts are not supported on this platform yet, skipping",
    "创建桌面快捷方式": "Creating desktop shortcut",
    "已创建桌面快捷方式: {shortcut_path}": "Desktop shortcut created: {shortcut_path}",

    # core/platform/startup.py
    "开机自启尚未支持当前平台，跳过": "Autostart is not supported on this platform yet, skipping",
    "已写入开机自启注册表项: {command}": "Autostart registry entry written: {command}",
    "已删除开机自启注册表项": "Autostart registry entry removed",
    "设置开机自启": "Setting autostart",

    # core/clipboard_utils.py
    "剪切板: 图像为空": "Clipboard: image is empty",
    "剪切板: Win32 写入失败 ({e})": "Clipboard: Win32 write failed ({e})",
    "图像投递: 图像为空，跳过": "Image delivery: image is empty, skipping",
    "图像投递: 未请求复制或保存，跳过": "Image delivery: neither copy nor save was requested, skipping",
    "图像投递: 非 Windows 平台，剪贴板仍走主线程回退": "Image delivery: non-Windows platform, clipboard still falls back to the main thread",
    "异步图像投递完成 clipboard={clipboard_ok} save={save_ok} "
    "clipboard={clipboard_ms:.1f}ms save={save_ms:.1f}ms total={total_ms:.1f}ms":
        "Async image delivery complete clipboard={clipboard_ok} save={save_ok} "
        "clipboard={clipboard_ms:.1f}ms save={save_ms:.1f}ms total={total_ms:.1f}ms",
    "图像投递: 后台任务失败 ({exc})": "Image delivery: background task failed ({exc})",
    "剪贴板: {path_name} 写入时剪贴板被占用，准备重试 {attempt_next}/{total_attempts}":
        "Clipboard: {path_name} write found the clipboard busy, retrying {attempt_next}/{total_attempts}",
    "已复制到剪切板 (Win32) "
    "dibv5={dibv5_ms:.1f}ms png={png_ms:.1f}ms win32={win32_ms:.1f}ms":
        "Copied to clipboard (Win32) "
        "dibv5={dibv5_ms:.1f}ms png={png_ms:.1f}ms win32={win32_ms:.1f}ms",
    "已复制到剪切板 (Win32 CF_DIBV5 + PNG)": "Copied to clipboard (Win32 CF_DIBV5 + PNG)",
    "已复制到剪切板 (Qt)": "Copied to clipboard (Qt)",

    # core/export.py
    "选区为空": "Selection is empty",
    "导出选区: {selection_rect}, 目标大小: {w}x{h}": "Exporting selection: {selection_rect}, target size: {w}x{h}",
    "导出完成: {out_width}x{out_height}": "Export complete: {out_width}x{out_height}",
    "导出底图: {selection_rect}, 目标大小: {w}x{h}": "Exporting base image: {selection_rect}, target size: {w}x{h}",
    "导出底图完成: {out_width}x{out_height}": "Base image export complete: {out_width}x{out_height}",

    # core/i18n.py
    "加载 XML 翻译文件失败: {e}": "Failed to load XML translation file: {e}",
    "不支持的语言: {lang_code}": "Unsupported language: {lang_code}",
    "QApplication 实例不存在": "QApplication instance does not exist",
    "已加载语言 (QM): {lang_name} ({lang_code})": "Loaded language (QM): {lang_name} ({lang_code})",
    "QM 文件加载失败，尝试 XML: {qm_file}": "Failed to load QM file, falling back to XML: {qm_file}",
    "已加载语言 (XML): {lang_name} ({lang_code})": "Loaded language (XML): {lang_name} ({lang_code})",
    "加载翻译文件失败: {xml_file}": "Failed to load translation file: {xml_file}",
    "翻译文件不存在: {xml_file}，使用默认文本": "Translation file not found: {xml_file}, using default text",

    # core/bootstrap.py
    "已终止旧实例 (PID {old_pid})": "Terminated previous instance (PID {old_pid})",
    "旧实例 (PID {old_pid}) 终止失败": "Failed to terminate previous instance (PID {old_pid})",
    "实例记录 (PID {old_pid}) 未通过身份校验，不终止": "Instance record (PID {old_pid}) failed identity verification, not terminating",
    "终止旧实例": "Terminating previous instance",
    "写入实例记录": "Writing instance record",
    "清理实例记录": "Cleaning up instance record",
    "资源锁定：成功 {pinned} 个，失败 {failed} 个": "Resource pinning: {pinned} succeeded, {failed} failed",
    "资源锁定：已保护 {pinned} 个打包资源文件": "Resource pinning: protected {pinned} bundled resource file(s)",
    "预加载步骤执行失败": "Preload step execution failed",
    "工具栏预加载完成": "Toolbar preload complete",
    "工具栏预加载失败: {e}": "Toolbar preload failed: {e}",
    "开始预加载截图相关模块...": "Starting preload of screenshot-related modules...",
    "mss 模块已加载并预热": "mss module loaded and warmed up",
    "canvas 模块已加载": "canvas module loaded",
    "tools 模块已加载": "tools module loaded",
    "win32gui 模块已加载": "win32gui module loaded",
    "win32gui 未安装，跳过": "win32gui not installed, skipping",
    "win32clipboard 模块已加载": "win32clipboard module loaded",
    "win32clipboard 未安装，跳过": "win32clipboard not installed, skipping",
    "PNG 编码器已预热": "PNG encoder warmed up",
    "预热PNG编码器": "Warming up PNG encoder",
    "UI 组件已加载": "UI components loaded",
    "CaptureService 已加载": "CaptureService loaded",
    "GIF 模块已加载": "GIF module loaded",
    "GIF 模块预加载失败: {e}": "GIF module preload failed: {e}",
    "长截图模块已加载": "Scrolling screenshot module loaded",
    "长截图模块预加载失败: {e}": "Scrolling screenshot module preload failed: {e}",
    "截图模块预加载完成": "Screenshot module preload complete",
    "截图模块预加载失败: {e}": "Screenshot module preload failed: {e}",
    "OCR 功能已禁用，跳过预加载": "OCR feature disabled, skipping preload",
    "开始在后台线程预加载 OCR 模块和引擎...": "Starting background-thread preload of OCR module and engine...",
    "OCR 模块不可用（无OCR版本）": "OCR module unavailable (non-OCR build)",
    "OCR 预加载成功": "OCR preload succeeded",
    "OCR 引擎预加载失败": "OCR engine preload failed",
    "OCR 模块不存在（无OCR版本）": "OCR module not found (non-OCR build)",
    "OCR 预加载失败: {e}": "OCR preload failed: {e}",
    "OCR 引擎预加载失败: {e}": "OCR engine preload failed: {e}",
    "预加载设置窗口...": "Preloading settings window...",
    "设置窗口预加载完成": "Settings window preload complete",
    "启动时显示主界面": "Showing main window on startup",
    "剪贴板功能已禁用，跳过剪贴板窗口预创建": "Clipboard feature disabled, skipping clipboard window precreation",
    "剪贴板窗口预创建完成（未显示）": "Clipboard window precreated (not shown)",
    "剪贴板窗口预创建失败: {e}": "Clipboard window precreation failed: {e}",

    # core/platform/process.py
    "当前平台无法查询进程身份，跳过旧实例检查": "Process identity is unavailable on this platform, skipping the stale-instance check",
    "释放工作集": "Releasing working set",
    "加载 kernel32": "Loading kernel32",
    "查询进程标识": "Querying process identity",
    "关闭进程句柄": "Closing process handle",
    "终止进程": "Terminating process",
    "重开本程序": "Relaunching the app",
    "已安排退出后重新打开 {path}": "Scheduled reopening {path} after exit",

    # core/resource_manager.py
    "SVG 渲染图标": "Rendering SVG icon",
    "图标渲染失败，本次返回空图标: {svg_path}": "Icon rendering failed, returning an empty icon for now: {svg_path}",

    # core/save.py
    "保存失败 {target_path}: {exc}": "Save failed {target_path}: {exc}",
    "已保存文件: {target_path}": "File saved: {target_path}",
    "保存失败: {target_path}": "Save failed: {target_path}",
    "已保存PDF: {target_path}": "PDF saved: {target_path}",
    "保存PDF失败 {target_path}: {exc}": "PDF save failed {target_path}: {exc}",
    "保存路径不是绝对路径，回退到默认: {target_dir}": "Save path is not absolute, falling back to default: {target_dir}",
    "清理失败的占位文件": "Cleaning up failed placeholder file",

    # core/shortcut_manager.py
    "系统热键已临时禁用，忽略回调 (id={hotkey_id})": "System hotkey temporarily disabled, ignoring callback (id={hotkey_id})",
    "热键回调 id={hotkey_id}": "Hotkey callback id={hotkey_id}",
    "ShortcutManager 已安装（KeyPress + WM_HOTKEY）": "ShortcutManager installed (KeyPress + WM_HOTKEY)",
    "注册 handler: {handler_name} (优先级 {priority})，"
    "当前共 {handler_count} 个": "Registered handler: {handler_name} (priority {priority}), {handler_count} total",
    "注销 handler: {handler_name}": "Unregistered handler: {handler_name}",
    "按键被 {handler_name} 消费 (key=0x{key_hex:X})": "Key consumed by {handler_name} (key=0x{key_hex:X})",
    "系统热键被 {handler_name} 拦截 (id={hotkey_id})": "System hotkey intercepted by {handler_name} (id={hotkey_id})",
    "检查快捷键可用性": "Checking hotkey availability",

    # core/platform/pynput_macos.py 与 core/platform/pointer.py 的键盘监听
    "pynput 的 Darwin 后端不可用，跳过键盘布局主线程收口: {e}":
        "pynput's Darwin backend is unavailable, skipping the main-thread keyboard layout fix: {e}",
    "pynput 内部接口已变化，键盘布局主线程收口未安装（键盘监听可能崩进程）":
        "pynput internals changed, the main-thread keyboard layout fix was not installed "
        "(keyboard listeners may crash the process)",
    "非主线程请求键盘布局（Carbon 输入源接口只能在主线程调用），已退回缓存值或空值":
        "Keyboard layout requested off the main thread (Carbon input source APIs may only be "
        "called on the main thread); falling back to the cached or empty value",
    "启动键盘监听失败: {e}": "Failed to start keyboard listener: {e}",
    "当前平台无法监听全局鼠标事件": "This platform cannot listen for global mouse events",
    "启动鼠标监听失败: {e}": "Failed to start mouse listener: {e}",

    # core/platform/hotkey.py 与 core/shortcut_manager.py 的输入监听权限（macOS 辅助功能）
    "请求辅助功能权限": "Requesting accessibility permission",
    "全局热键收不到事件：macOS 需要「辅助功能」权限（系统设置 → 隐私与安全性 → 辅助功能）。"
    "授权后热键会自动生效，不用重启程序；重新打包过的应用请先用「−」移除旧条目再重新添加":
        "Global hotkeys receive no events: macOS requires Accessibility permission "
        "(System Settings → Privacy & Security → Accessibility). Hotkeys take effect as soon "
        "as you grant it, no restart needed; for a rebuilt app, remove the stale entry with "
        "\"−\" and add it again",
    "已获得「辅助功能」权限，全局热键监听已重启":
        "Accessibility permission granted, global hotkey listener restarted",

    # core/platform/capture.py 与 core/bootstrap.py 的「屏幕录制」权限
    "请求屏幕录制权限": "Requesting screen recording permission",
    "检查屏幕录制权限": "Checking screen recording permission",
    "截图里只有桌面、窗口都不见了：macOS 需要「屏幕录制」权限（系统设置 → 隐私与安全性 → 录屏与系统录音）。"
    "授权后要重启本程序；重新打包过的应用请先用「−」移除旧条目再重新添加":
        "Screenshots contain only the desktop and no windows: macOS requires Screen Recording "
        "permission (System Settings → Privacy & Security → Screen & System Audio Recording). "
        "Restart this app after granting it; for a rebuilt app, remove the stale entry with "
        "\"−\" and add it again",
    "保持窗口可见（不随失焦隐藏）": "Keeping a window visible when the app is inactive",
    "「屏幕录制」权限已开启，需要重启本程序后截图才包含窗口":
        "Screen Recording permission is now granted; restart this app before screenshots include windows",

    # core/actions.py：动作注册表和全局鼠标动作的执行
    "确定鼠标位置的抓取范围": "Determining the capture region at the cursor",
    "静默截屏": "Silent capture",
    "静默截屏: {width}x{height} @({x}, {y})": "Silent capture: {width}x{height} @({x}, {y})",
    "静默截图失败，动作未执行: {action_id}": "Silent capture failed, action skipped: {action_id}",
    "钉图创建失败": "Failed to create pinned window",
    "显示截图遮罩: {rect}": "Showing capture mask: {rect}",
    "显示截图遮罩": "Showing capture mask",
    "读取忽略程序列表": "Reading the ignored-application list",
    "不认识的动作: {action_id}": "Unknown action: {action_id}",
    "动作触发: {action_id}": "Action triggered: {action_id}",
    "开始识别截图文字": "Started reading text from the screenshot",
    "开始识别表格文字": "Started reading table text from the screenshot",
    "识别截图文字": "Reading text from the screenshot",
    "未识别到文字": "No text recognized",
    "识别到的文字已复制到剪贴板（{count} 字）":
        "Recognized text copied to the clipboard ({count} characters)",
    "OCR 不可用，无法识别截图文字": "OCR unavailable, cannot read text from the screenshot",
    "截图内容为空，无法识别文字": "Empty screenshot, cannot read text",
    # -- core/platform/window_ops.py（亚克力背景）--
    "窗口是子部件，没有独立原生窗口，跳过亚克力背景":
        "Window is a child widget without its own native window; skipping acrylic background",
    "应用亚克力背景（Windows）": "Applying the acrylic background (Windows)",
    "应用亚克力背景（macOS）": "Applying the acrylic background (macOS)",
    "平台不支持亚克力背景，跳过": "Platform does not support the acrylic background, skipping",
    "当前平台没有独立的「应用图标」，跳过":
        "This platform has no separate application icon, skipping",
    "设置应用图标": "Setting the application icon",
    "Win11 系统背景材质不可用（hr={hr}），改试 accent 路径":
        "Windows 11 system backdrop unavailable (hr={hr}), trying the accent path instead",
    "Windows 的亚克力两条路都不通，退回不透明外观":
        "Both Windows acrylic paths failed, falling back to an opaque look",
    "应用亚克力背景": "Applying the acrylic background",

    # -- core/platform/focus.py --
    "获取前台程序名": "Getting the foreground app name",

    # -- core/resource_manager.py（自定义 logo）--
    "读取自定义 logo 路径": "Reading the custom logo path",
    "自定义 logo 文件不存在: {path}": "Custom logo file does not exist: {path}",
    "自定义 logo 无法解析为图片: {path}": "Custom logo could not be parsed as an image: {path}",
    "应用图标已刷新: {state}": "Application icon refreshed: {state}",

    # -- core/actions.py --
    "剪贴板里没有图片，无法翻译": "No image in the clipboard, cannot translate",
    "读取翻译参数": "Reading translation settings",
    "图片翻译: {width}x{height}": "Image translation: {width}x{height}",
    "读取截图保存目录": "Reading the screenshot save directory",
    "没有配置截图保存目录，无法打开": "No screenshot save directory configured, cannot open it",
    "打开截图保存目录失败: {folder}": "Failed to open the screenshot save directory: {folder}",
    "剪贴板里没有文字，无法贴图": "No text in the clipboard, cannot create a pin",
    "已把剪贴板文字做成贴图（{count} 字）":
        "Created a pin from the clipboard text ({count} characters)",

    # -- core/shortcut_manager.py --
    "全局鼠标监听已启动（{count} 个手势）": "Global mouse listener started ({count} gestures)",
    "全局鼠标监听启动失败，鼠标动作不可用":
        "Failed to start the global mouse listener; mouse actions are unavailable",
    "忽略不认识的动作绑定: {action_id}": "Ignoring unknown action binding: {action_id}",
    "忽略不认识的鼠标手势: {gesture}": "Ignoring unknown mouse gesture: {gesture}",
    "忽略不认识的修饰键: {modifier}": "Ignoring unknown modifier key: {modifier}",

    # -- 2026-09-19 新增设置与更新源 --
    "打开更新源失败: {url}": "Failed to open the update source: {url}",
    "网络代理: 直连": "Network proxy: direct connection",
    "网络代理: {host}:{port}": "Network proxy: {host}:{port}",
    "读取代理配置": "Reading the proxy configuration",
    "代理连接失败: {host}:{port} {e}": "Proxy connection failed: {host}:{port} {e}",
    "图片已按文件形式放入剪贴板: {path}": "Image offered to the clipboard as a file: {path}",
    "图片写入临时文件失败: {path}": "Failed to write the image to a temporary file: {path}",
    "把图片以文件形式放进剪贴板": "Offering the image to the clipboard as a file",
    "读取主程序实例": "Reading the main app instance",
    "读取识别结果处理选项": "Reading the recognition result options",
    "读取公式引擎设置": "Reading the formula engine setting",
    "公式已复制到剪贴板（{count} 字）": "Formula copied to the clipboard ({count} characters)",
    "设置的公式引擎不可用，改用 {engine}: {configured}": "Configured formula engine is unavailable, using {engine} instead: {configured}",
    "未识别到公式": "No formula recognized",
}
