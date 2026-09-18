"""main/settings/ 目录下 log_* 调用的中→英翻译表。"""

TRANSLATIONS: dict[str, str] = {
    "应用设置已重置为默认值": "App settings have been reset to defaults",
    "所有设置已重置为默认值": "All settings have been reset to defaults",
    "检测到首次运行，将自动打开设置窗口": "First run detected, settings window will open automatically",
    "根据用户设置：显示设置窗口": "Per user setting: showing settings window",
    "根据用户设置：不自动打开设置窗口": "Per user setting: not auto-opening settings window",
    "贴图手势配置无法解析，按默认处理": "Pin gesture config could not be parsed, using defaults",
    "全局鼠标绑定配置无法解析，按未绑定处理":
        "Mouse gesture config could not be parsed, treating the actions as unbound",
    "动作快捷键配置无法解析，按未绑定处理":
        "Action hotkey config could not be parsed, treating the actions as unbound",
    "托盘动作配置无法解析，按默认处理":
        "Tray action config could not be parsed, using defaults",
    "忽略程序列表无法解析，按空列表处理":
        "Ignored-app list could not be parsed, treating it as empty",
}
