from .capture_service import CaptureService

# 窗口枚举（智能选区）已迁到 core/platform/window；以前这里会重导出
# WindowFinder 与 is_smart_selection_available，现在请直接从平台层导入。

__all__ = ['CaptureService']
