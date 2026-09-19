# -*- coding: utf-8 -*-
"""长截图成品的后期处理：接缝人工修正、固定标题栏/底栏消除、超长图分段。

拼接本身在 Rust 侧（``longstitch``），这里管的是拼完之后用户还要做的事——三件：

- ``shift_seam``：接缝错位时把该位置以下的内容上/下挪 N 像素。挪正数等于多留 N 行
  （内容被推下去），挪负数等于删掉 N 行（内容提上来），高度随之变化，正好是 ±1/±10px
  这种「手动微调」的语义。
- ``detect_fixed_band`` / ``remove_fixed_bands``：滚动截图里固定的标题栏、底栏、输入框
  会在每帧的同一位置重复出现；把「所有帧都一模一样」的首/尾若干行判为固定条，从成品里切掉。
- ``split_segments``：超长图按最大高度切成若干段，逐段落盘。切是按行切、无损，
  拼回去与整图逐像素相同。

全部是纯函数（进 PIL Image 出 PIL Image），与拼接引擎、界面都不耦合。
"""

from PIL import Image

from core.logger import T, log_debug, log_warning

#: 固定条判定的默认参数
DETECT_TOLERANCE = 2         # 单个通道允许的差异（抗压缩噪声）
DETECT_MIN_HEIGHT = 8        # 比这还窄的「固定条」多半是巧合，不动它
DETECT_MAX_RATIO = 0.3       # 固定条最多占帧高的三成，否则说明判定错了
#: 判定时每隔几列取一个采样点（整行逐像素比太慢，采样足够可靠）
DETECT_SAMPLE_STEP = 4


def shift_seam(image, seam_y: int, delta: int):
    """把 ``seam_y`` 以下的内容整体挪 ``delta`` 像素（正数往下、负数往上）。

    ``delta`` 为正时重复接缝处的行来「撑开」，为负时删掉若干行来「收拢」；
    接缝位置超出图像范围或 ``delta`` 为 0 时原样返回拷贝。
    """
    if image is None:
        return image
    width, height = image.size
    if height <= 0 or width <= 0 or not delta:
        return image.copy()
    seam = max(0, min(int(seam_y), height))
    if delta > 0:
        source = seam if seam < height else height - 1
        if source < 0:
            return image.copy()
        band = image.crop((0, source, width, source + 1)).resize((width, delta))
        output = Image.new(image.mode, (width, height + delta))
        output.paste(image.crop((0, 0, width, seam)), (0, 0))
        output.paste(band, (0, seam))
        output.paste(image.crop((0, seam, width, height)), (0, seam + delta))
    else:
        remove = min(-int(delta), height - seam)
        if remove <= 0:
            return image.copy()
        output = Image.new(image.mode, (width, height - remove))
        output.paste(image.crop((0, 0, width, seam)), (0, 0))
        output.paste(image.crop((0, seam + remove, width, height)), (0, seam))
    log_debug(T("接缝修正: y={seam} 位移 {delta}px → {height}px", seam=seam, delta=delta,
                height=output.size[1]), "Stitch")
    return output


def split_segments(image, max_height: int):
    """按 ``max_height`` 把长图切成若干段（无损、按行切）。

    ``max_height`` 非正或图本来就不超限时返回单元素列表——调用方不必分情况处理。
    """
    if image is None:
        return []
    width, height = image.size
    if max_height is None or max_height <= 0 or height <= max_height:
        return [image.copy()]
    segments = []
    for top in range(0, height, max_height):
        segments.append(image.crop((0, top, width, min(top + max_height, height))))
    log_debug(T("长截图分段: {count} 段（每段上限 {limit}px）", count=len(segments),
                limit=max_height), "Stitch")
    return segments


def _row_matches(first, other, top: int, width: int, tolerance: int, step: int) -> bool:
    for x in range(0, width, step):
        a = first.getpixel((x, top))
        b = other.getpixel((x, top))
        if a == b:
            continue
        if isinstance(a, int):
            return False
        if any(abs(int(left) - int(right)) > tolerance for left, right in zip(a, b)):
            return False
    return True


def detect_fixed_band(frames, edge: str = "top", *, tolerance: int = DETECT_TOLERANCE,
                      min_height: int = DETECT_MIN_HEIGHT,
                      max_ratio: float = DETECT_MAX_RATIO) -> int:
    """检测首/尾固定条的像素高度；没有固定条时返回 0。

    判据是「每一帧在这一行上长得一样」——固定标题栏、状态栏、输入框都满足，
    而正常的滚动内容不会。帧数少于 2 时返回 0：没有第二帧就无从比较。
    """
    usable = [frame for frame in (frames or []) if frame is not None and frame.size[0] > 0]
    if len(usable) < 2:
        return 0
    width, height = usable[0].size
    limit = min(height, int(height * max_ratio))
    if limit < min_height:
        return 0
    step = DETECT_SAMPLE_STEP

    found = 0
    for index in range(limit):
        top = index if edge == "top" else height - 1 - index
        if all(_row_matches(usable[0], frame, top, width, tolerance, step)
               for frame in usable[1:]):
            found += 1
        else:
            break
    if found < min_height:
        return 0
    if edge == "top":
        log_debug(T("检测到顶部固定条: {height}px", height=found), "Stitch")
    else:
        log_debug(T("检测到底部固定条: {height}px", height=found), "Stitch")
    return found


def remove_fixed_bands(image, *, top: int = 0, bottom: int = 0):
    """切掉头/尾的固定条；参数为 0 的那一侧不动。"""
    if image is None:
        return image
    width, height = image.size
    top = max(0, int(top or 0))
    bottom = max(0, int(bottom or 0))
    if top + bottom >= height:
        log_warning(T("固定条高度超过图片本身，跳过消除"), "Stitch")
        return image.copy()
    if not top and not bottom:
        return image.copy()
    output = image.crop((0, top, width, height - bottom))
    log_debug(T("已消除固定条: 顶 {top}px / 底 {bottom}px → {height}px", top=top,
                bottom=bottom, height=output.size[1]), "Stitch")
    return output
