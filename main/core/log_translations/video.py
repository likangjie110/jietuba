"""main/video/、main/history/ 与 core/platform/video.py 里 log_* 调用的中→英翻译表。"""

TRANSLATIONS: dict[str, str] = {
    # core/platform/video.py
    "QtMultimedia 不在，无法录制视频":
        "QtMultimedia is unavailable, cannot record video",
    "录制参数不被编码后端支持: container={container}, codec={codec}":
        "The encoding backend does not support these recording parameters: "
        "container={container}, codec={codec}",
    "没有可用的麦克风，本次录制不录声音":
        "No microphone is available, this recording has no audio",
    "装配录制会话": "Setting up the recording session",
    "推送录制帧": "Pushing a recording frame",

    # frame_source.py
    "缺少 mss，无法录制视频: {e}": "mss is missing, cannot record video: {e}",
    "录制抓帧失败": "Video frame capture failed",
    "录制抓帧失败: {e}": "Video frame capture failed: {e}",

    # video_recorder.py
    "视频录制开始: {w}x{h} → {out_w}x{out_h} @ {fps}fps ({container}/{codec}, {backend})":
        "Video recording started: {w}x{h} → {out_w}x{out_h} @ {fps}fps "
        "({container}/{codec}, {backend})",
    "停止视频录制，共 {frames} 帧": "Stopping video recording, {frames} frames total",
    "录制抓帧线程失败，停止录制: {message}":
        "The frame capture thread failed, stopping the recording: {message}",
    "录制编码器报错: {message}": "The recording encoder reported an error: {message}",
    "达到最长录制时长 {limit}s，自动停止":
        "Maximum recording duration of {limit}s reached, stopping automatically",
    "停止抓帧线程失败: {e}": "Failed to stop the frame capture thread: {e}",
    "录制期间丢弃了 {dropped} 帧（编码器来不及消费）":
        "Dropped {dropped} frames during recording (the encoder could not keep up)",
    "视频录制完成: {path}": "Video recording finished: {path}",
    "视频录制不可用：这个构建里没有可用的编码后端":
        "Video recording is unavailable: this build has no usable encoding backend",
    "没有指定视频保存路径": "No video output path was given",
    "录制区域太小，无法录制视频": "The recording area is too small to record video",
    "录制会话创建失败：编码后端不支持这组参数":
        "Failed to create the recording session: the encoding backend does not support "
        "these parameters",

    # record_window.py
    "视频录制窗口已启动, 区域={rect}": "Video recording window started, region={rect}",
    "视频录制窗口已关闭": "Video recording window closed",
    "关闭旧的视频录制窗口": "Closing the previous video recording window",
    "视频清晰度切换: {quality}": "Video quality changed: {quality}",
    "视频帧率切换: {fps}": "Video frame rate changed: {fps}",
    "读取视频保存目录": "Reading the video save directory",
    "创建视频保存目录": "Creating the video save directory",
    "关闭录制窗口部件失败: {e}": "Failed to close a recording window widget: {e}",
    "设置录制窗口失焦保持可见失败: {e}":
        "Failed to keep the recording window visible when inactive: {e}",

    # history/store.py
    "读取截图历史索引": "Reading the screenshot history index",
    "截图历史索引格式不对，按空历史处理":
        "The screenshot history index has an unexpected format, treating it as empty",
    "写入截图历史索引": "Writing the screenshot history index",
    "截图历史写入图片失败: {path}": "Failed to write a screenshot history image: {path}",
    "截图历史写入图片": "Writing a screenshot history image",
    "截图历史新增: {entry_id} ({source}, {width}x{height}, {size} 字节)":
        "Screenshot history entry added: {entry_id} ({source}, {width}x{height}, {size} bytes)",
    "删除截图历史文件": "Deleting a screenshot history image",
    "截图历史保留策略: 淘汰 {count} 条 ({reasons})":
        "Screenshot history retention: evicted {count} entries ({reasons})",
    "清空截图历史": "Clearing the screenshot history",

    # history/source.py
    "判断截图来源": "Determining the screenshot source",
    "读取窗口标题": "Reading the window title",
    "截图来源: {source}{label} (选区 {rect})":
        "Screenshot source: {source}{label} (selection {rect})",

    # history/recorder.py
    "截图历史已关闭，跳过记录": "Screenshot history is off, skipping",
    "记录截图历史": "Recording the screenshot history",
    "读取截图历史保留策略": "Reading the screenshot history retention settings",

    # history/window.py
    "复制历史截图": "Copying a history screenshot",
    "历史截图已复制: {entry_id}": "History screenshot copied: {entry_id}",
    "历史截图已钉图: {entry_id}": "History screenshot pinned: {entry_id}",
    "历史截图已另存: {path}": "History screenshot saved as: {path}",
}
