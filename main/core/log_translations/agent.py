"""main/agent/ 目录下 log_* 调用的中→英翻译表。"""

TRANSLATIONS: dict[str, str] = {
    # agent/commands.py
    "抓屏失败（可能是缺少屏幕录制权限）":
        "Screen capture failed (screen recording permission may be missing)",
    "截图写入失败: {path}": "Failed to write the screenshot: {path}",
    "Agent 截图: {width}x{height} → {path}":
        "Agent capture: {width}x{height} → {path}",
    "没有可识别的图片：请先截图或给出 --from 路径":
        "No image to recognise: capture first or pass --from",
    "图片不存在: {path}": "Image does not exist: {path}",
    "OCR 不可用": "OCR is unavailable",
    "图片读不出来: {path}": "Could not read the image: {path}",
    "识别失败: {msg}": "Recognition failed: {msg}",
    "识别文本写入失败: {e}": "Failed to write the recognised text: {e}",
    "Agent 识别: {count} 行（置信度 {score}）":
        "Agent recognition: {count} line(s), confidence {score}",
    "读取应用版本": "Reading the application version",
    "读取视觉模型状态": "Reading the vision model status",
    "不认识的能力: {name}": "Unknown capability: {name}",
    "参数不正确: {e}": "Invalid arguments: {e}",
    "执行失败: {e}": "Execution failed: {e}",
    "执行 Agent 命令 {name}": "Running the agent command {name}",
    "抓屏后端报告的屏幕尺寸无效（{width}x{height}），按图像尺寸兜底":
        "Capture backend reported an invalid screen size ({width}x{height}), "
        "falling back to the image size",

    # agent/cli.py
    "Agent 命令经本机 bridge 执行: {command}":
        "Agent command served through the local bridge: {command}",
    "执行 Agent 命令行": "Running the agent command line",

    # agent/bridge.py
    "清理旧的 bridge 套接字": "Cleaning up the stale bridge socket",
    "Agent bridge 监听失败: {error}": "Agent bridge failed to listen: {error}",
    "Agent bridge 已启动: {path}": "Agent bridge started: {path}",
    "停止 Agent bridge": "Stopping the agent bridge",
    "启动 Agent bridge": "Starting the agent bridge",
    "请求不是合法 JSON": "The request is not valid JSON",
    "Agent bridge 收到调用: {method}": "Agent bridge received a call: {method}",
    "Agent bridge 连不上，改由本进程执行: {method}":
        "Agent bridge is unreachable, running {method} in this process instead",
    "Agent bridge 没有返回内容: {method}":
        "Agent bridge returned nothing: {method}",
    "解析 Agent bridge 响应": "Parsing the agent bridge response",
}
