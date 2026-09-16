"""
OCR 引擎对比 —— 跑一批带标准答案的样本，比较各引擎的准确率与耗时。

要不要接一个新引擎（比如微信 OCR）应该由数据决定，不是由感觉决定。这个脚本
从 ocr 注册表里取引擎（插件目录里的引擎也会出现），所以新引擎只要按
`plugins/README.md` 的约定注册进来，就能直接进对比。

用法:
    # 1. 生成一组合成样本（自带标准答案，零人工成本）
    python main/scripts/compare_ocr_engines.py --make-samples samples/

    # 2. 对比所有可用引擎
    python main/scripts/compare_ocr_engines.py --samples samples/

    # 3. 只跑指定引擎
    python main/scripts/compare_ocr_engines.py --samples samples/ --engines ppocr_rust

样本格式：每张图旁边一个同名 .txt，内容是这张图的**准确文字**：
    samples/chat_zh.png
    samples/chat_zh.txt

--make-samples 生成的是合成样本（PIL 渲染），只能当基线：它量的是"引擎认不认得
这些字"，量不出真实截图里的抗锯齿、深色主题、压缩噪声差异。真实结论请用自己的
截图 + 手打文本，放进同一个目录即可。

指标:
    CER      字符错误率 = 编辑距离 / 标准答案长度，忽略所有空白；0 = 全对，越低越好
    折叠后   再把全角半角标点折到一起后的 CER——中文 OCR 把「：」输出成「:」不算
             认错字，两个数的差额就是"标点风格"的影响
    命中     整张图一字不差的比例
    耗时     单张中位数，首次调用（模型预热）不计入
"""
import argparse
import sys
import time
import unicodedata
from pathlib import Path
from typing import NamedTuple

MAIN_DIR = Path(__file__).resolve().parents[1]
if str(MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(MAIN_DIR))

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


# ── 指标 ────────────────────────────────────────────────

def levenshtein(a: str, b: str) -> int:
    """编辑距离（滚动数组：O(len(a)*len(b)) 时间、O(len(b)) 空间）。"""
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(
                previous[j] + 1,                                    # 删除
                current[j - 1] + 1,                                 # 插入
                previous[j - 1] + (char_a != char_b),               # 替换
            ))
        previous = current
    return previous[-1]


def normalize(text: str) -> str:
    """比之前去掉全部空白。

    OCR 输出的换行/空格位置和标准答案很难完全一致（一行断在哪、词间要不要空格），
    把空白算进错误率会淹没真正的识别错误。
    """
    return "".join(text.split())


def fold(text: str) -> str:
    """在 normalize 基础上再做 NFKC 折叠（：→:、（→( 等全角半角统一）。

    中文 OCR 把全角标点输出成半角是常态。严格比会把它算成识别错误，但用户复制走的
    文本里这个冒号是圆是方并不影响使用——两个 CER 都报，差额就是"标点风格"的占比，
    "字到底认没认对"看折叠后的那个。
    """
    return unicodedata.normalize("NFKC", normalize(text))


def character_error_rate(expected: str, actual: str, folded: bool = False) -> float:
    """CER = 编辑距离 / 标准答案长度。标准答案为空白时按 1 个字符算。

    folded=True 时先做 NFKC 折叠，全角半角标点差异不计入错误。
    """
    prepare = fold if folded else normalize
    ref, hyp = prepare(expected), prepare(actual)
    return levenshtein(ref, hyp) / max(len(ref), 1)


# ── 跑样本 ──────────────────────────────────────────────

def load_samples(directory: Path):
    """读入 (名字, QImage, 标准答案) 列表；缺标准答案的图跳过并说明。"""
    from PySide6.QtGui import QImage

    samples = []
    for image_path in sorted(directory.iterdir()):
        if image_path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        label_path = image_path.with_suffix(".txt")
        if not label_path.is_file():
            print(f"[跳过] {image_path.name}：缺少同名 .txt 标准答案")
            continue
        image = QImage(str(image_path))
        if image.isNull():
            print(f"[跳过] {image_path.name}：图像读不出来")
            continue
        samples.append((image_path.stem, image, label_path.read_text(encoding="utf-8")))
    return samples


class Result(NamedTuple):
    name: str
    cer: float          # 严格：标点全半角不同也算错
    cer_folded: float   # 折叠全半角后的 CER
    exact: bool
    ms: float
    output: str


def benchmark(engine, samples):
    """逐张识别，返回每张的 CER / 耗时。"""
    if not engine.initialize():
        raise RuntimeError(f"初始化失败: {engine.last_error}")
    engine.recognize(samples[0][1], "text")     # 首次调用含预热，不计入耗时

    rows = []
    for name, image, expected in samples:
        start = time.perf_counter()
        output = engine.recognize(image, "text")
        elapsed = (time.perf_counter() - start) * 1000
        if not isinstance(output, str):         # 引擎出错时返回的不是文本
            output = ""
        rows.append(Result(
            name=name,
            cer=character_error_rate(expected, output),
            cer_folded=character_error_rate(expected, output, folded=True),
            exact=normalize(expected) == normalize(output),
            ms=elapsed,
            output=output,
        ))
    return rows


def report(engine, rows):
    mean_cer = sum(row.cer for row in rows) / len(rows)
    mean_folded = sum(row.cer_folded for row in rows) / len(rows)
    exact = sum(1 for row in rows if row.exact)
    times = sorted(row.ms for row in rows)
    median_ms = times[len(times) // 2]

    print(f"\n{engine.name}  ({engine.label})")
    print(f"  平均 CER {mean_cer:.2%}（折叠全半角后 {mean_folded:.2%}）"
          f"   一字不差 {exact}/{len(rows)}   单张中位 {median_ms:.0f} ms")
    for row in rows:
        print(f"    {'✓' if row.exact else '✗'} {row.name:<22}"
              f" CER {row.cer:>7.2%} → {row.cer_folded:>7.2%}  {row.ms:>6.0f} ms")

    worst = max(rows, key=lambda row: row.cer_folded)
    if worst.cer_folded > 0:
        print(f"  最差样本 {worst.name} 的输出: {worst.output.strip()[:70]!r}")
    return mean_folded


# ── 合成样本 ────────────────────────────────────────────

SAMPLE_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
)

# (名字, 字号, 前景色, 背景色, 缩放, 文件名后缀, 行)
SAMPLE_SPECS = (
    ("zh_body", 24, "#1a1a1a", "#ffffff", 1.0, ".png", [
        "截图吧是一款面向 Windows 的截图与剪贴板管理软件",
        "支持区域截图、窗口识别、GIF 录制与长截图拼接",
    ]),
    ("zh_small", 13, "#1a1a1a", "#ffffff", 1.0, ".png", [
        "小字场景：设置里的说明文字通常只有 12 到 13 像素",
        "这一档最容易掉字，尤其笔画多的汉字",
    ]),
    ("zh_dark", 22, "#d4d4d4", "#1e1e1e", 1.0, ".png", [
        "深色主题下的浅色文字识别",
        "编辑器、终端、IDE 的截图大多是这种配色",
    ]),
    ("zh_lowcontrast", 20, "#b8b8b8", "#f2f2f2", 1.0, ".png", [
        "低对比度：浅灰字压在浅灰底上",
        "禁用状态的控件和占位文字长这样",
    ]),
    ("mixed", 22, "#202020", "#ffffff", 1.0, ".png", [
        "Error 0x80070005: 拒绝访问 (Access is denied)",
        "版本 2026.09.14 发布于 2026-09-14 21:35",
    ]),
    ("chat", 20, "#111111", "#ffffff", 1.0, ".png", [
        "张三 14:20",
        "会议时间改为 14:00，地点在 A301 会议室",
        "李四 14:22",
        "收到，我准时到，需要我带上上周的报表吗",
    ]),
    ("numbers", 22, "#111111", "#ffffff", 1.0, ".png", [
        "订单号 20260914001    金额 ¥1,234.56",
        "库存 87 件    税率 13%    合计 ¥1,395.05",
    ]),
    ("scaled", 30, "#1a1a1a", "#ffffff", 0.55, ".png", [
        "这张图先按大字号渲染再缩到 55%，模拟被缩放的截图",
        "缩小会削弱笔画，是 OCR 的常见失分场景",
    ]),
    ("jpeg", 22, "#1a1a1a", "#ffffff", 1.0, ".jpg", [
        "JPEG 压缩会在文字边缘留下块状噪声",
        "聊天软件转发过的图基本都是这个状态",
    ]),
    ("long", 20, "#1a1a1a", "#ffffff", 1.0, ".png", [
        "长文本主要用于看整体稳定性：段落越长，个别字错一点 CER 影响越小，",
        "但如果引擎会在长行中间断句或者漏掉整行，这里会立刻暴露出来。",
        "另外，标点符号、全角半角、中英文之间的空格也是常见的失分点——",
        "标准答案里有 1,234.56 这样的数字，也有 （） 这种全角括号。",
    ]),
)


def _find_font(size: int):
    from PIL import ImageFont

    for path in SAMPLE_FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    raise SystemExit("找不到中文字体，请在 SAMPLE_FONT_CANDIDATES 里补一个字体路径")


def make_samples(directory: Path) -> int:
    """生成合成样本（图 + 同名 .txt 标准答案）。"""
    from PIL import Image, ImageDraw

    directory.mkdir(parents=True, exist_ok=True)
    for name, size, fg, bg, scale, suffix, lines in SAMPLE_SPECS:
        font = _find_font(size)
        pad, line_height = 18, int(size * 1.7)
        width = int(max(font.getlength(line) for line in lines)) + pad * 2
        height = line_height * len(lines) + pad * 2

        image = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            draw.text((pad, pad + index * line_height), line, font=font, fill=fg)

        if scale != 1.0:
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)

        target = directory / f"{name}{suffix}"
        if suffix == ".jpg":
            image.save(target, quality=60)
        else:
            image.save(target)
        target.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
        print(f"  生成 {target.name}（{size}px, 缩放 {scale:.0%}）")

    print(f"共 {len(SAMPLE_SPECS)} 个样本 → {directory}")
    return 0


# ── 入口 ────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="OCR 引擎准确率 / 耗时对比")
    parser.add_argument("--samples", type=Path, help="样本目录（图 + 同名 .txt 标准答案）")
    parser.add_argument("--make-samples", type=Path, metavar="DIR", help="生成合成样本到该目录")
    parser.add_argument("--engines", help="只测这些引擎（逗号分隔的 id）")
    args = parser.parse_args()

    if args.make_samples:
        make_samples(args.make_samples)
        if args.samples is None:
            return 0        # 只生成样本，不跑
    if not args.samples:
        parser.error("需要 --samples 或 --make-samples 之一")

    samples = load_samples(args.samples)
    if not samples:
        print(f"{args.samples} 里没有可用样本（需要 图 + 同名 .txt）")
        return 1

    from ocr.ocr_manager import OCRManager
    manager = OCRManager()

    names = manager.get_available_engines()
    if args.engines:
        wanted = [name.strip() for name in args.engines.split(",") if name.strip()]
        for name in wanted:
            if name not in names:
                print(f"[跳过] {name}：当前不可用")
        names = [name for name in wanted if name in names]
    if not names:
        print("没有可用的 OCR 引擎。装上 j-ppocr 并把模型放到 models/，"
              "或按 plugins/README.md 放一个插件。")
        return 1

    print(f"样本 {len(samples)} 个，引擎 {', '.join(names)}")
    results = {}
    for name in names:
        engine = manager.get_engine(name)
        try:
            rows = benchmark(engine, samples)
        except Exception as exc:
            print(f"\n{name}: 跑不动 —— {exc}")
            continue
        results[name] = report(engine, rows)

    if len(results) > 1:
        print("\n总结（平均 CER，折叠全半角后，越低越好）")
        for name, cer in sorted(results.items(), key=lambda item: item[1]):
            print(f"  {name:<18} {cer:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
