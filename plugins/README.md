# plugins/ — 外部 OCR 引擎插件目录

把插件放在这个目录里就会在启动时被自动加载，不需要改本仓库的任何代码。

- **打包版**：`jietuba_pp.exe` 同级的 `plugins/`（和 `models/` 一个规矩）
- **源码运行**：仓库根目录的 `plugins/`
- 本目录里除本文件外都不入库（见 `.gitignore`），插件是用户自己的东西

## 写一个插件

一个插件 = 这个目录下一个 `.py` 文件，或者一个带 `__init__.py` 的文件夹。
它必须暴露 `register(manager)`，在里面把引擎注册进去：

```python
# plugins/my_ocr.py
from ocr.engine import OcrEngine


class MyEngine(OcrEngine):
    name = "my_ocr"          # 引擎 id：set_ocr_engine("my_ocr") 用它，别和内置的重名
    label = "My OCR"         # 只用于日志和状态显示

    def is_available(self) -> bool:
        """扩展、模型、外部程序是否齐备。会被反复调用，结果要自己缓存。"""
        return True

    def recognize(self, pixmap, return_format="dict"):
        """识别一张 QPixmap/QImage。"""
        # 基类提供了两个现成的转换：
        #   self.rgb_bytes(pixmap) -> (data, w, h, stride)  同进程吃裸像素的引擎用
        #   self.png_bytes(pixmap) -> bytes                 跨进程/走文件接口的引擎用
        from ocr.engine import format_result
        rows = [[[[0, 0], [9, 0], [9, 5], [0, 5]], "识别到的文字", 0.99]]
        return format_result(rows, return_format, 0.0)

    # 可选：initialize(language) 预热、release() 释放资源、is_loaded()/status_text() 报状态


def register(manager):
    manager.register_engine(MyEngine())
```

`recognize()` 的结果要按 `[[box, text, score], ...]` 交给 `format_result()`，
不要自己拼 dict/list/text —— 下游（钉图文字层、翻译）依赖这个统一结构。

## 约定与行为

| 情况 | 行为 |
|---|---|
| 模块名以下划线或点开头 | 跳过（本地私有文件不放插件） |
| 没有 `register(manager)` | 记一条警告日志后跳过，不影响其它插件 |
| 加载或 `register()` 抛异常 | 记一条警告日志后跳过，**不影响程序启动** |
| 插件名与已加载的模块重名 | 跳过（避免顶掉真正的依赖） |
| 插件引擎的 `name` 与内置引擎相同 | 覆盖内置的那个 |

插件目录扫描发生在 OCR 第一次被使用的时候（`OCRManager` 构造），不是程序启动时——
所以放错插件不会影响截图、剪贴板这些和 OCR 无关的功能。

## 为什么不做成 entry points

本应用主要靠单文件 exe 分发，用户不会 `pip install` 任何东西；目录扫描对「把包丢进
`plugins/`」这个动作才真正生效。接口和发现逻辑见 `main/ocr/engine.py`、
`main/ocr/plugins.py`。
