"""main/video/ 与 core/platform/video.py 目录下 log_* 调用的中→英翻译表。"""

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
}
