//! PP-FormulaNet 公式识别：ONNX 推理 + tokenizer 词表解码。
//!
//! 与文本引擎（`engine.rs`）分成两个文件：公式识别是「一张公式图进、一串 LaTeX 出」的
//! encoder-decoder，没有 det/rec 两段式、没有 CTC 对齐、也没有文本框，能复用的只有
//! 「ort 加载」和「CHW 张量」这两个概念，硬塞进 `engine.rs` 只会让两条流程互相牵制。
//!
//! 绑定对着的是 PP-FormulaNet_plus-M 的 ONNX 导出（PaddleOCR 系）：
//! 输入 `x`: `[N, 1, 384, 384]` float32（单通道灰度），输出 `fetch_name_0`: `[N, T]`
//! int64（token id，图里已经做完贪心解码）。前后处理都照官方那套来：前处理同 HF 的
//! `PPFormulaNetImageProcessor`（crop_margin → 等比缩到装得进 384×384 → 居中补黑边 →
//! 灰度 → `(x/255-mean)/std`），后处理同 PaddleOCR 的 `UniMERNetDecode`（截断到第一个
//! `</s>` → 跳过特殊 token → ByteLevel 还原字节）。
//!
//! 实测（2026-09-19，PP-FormulaNet_plus-M 593MB）：加载 0.5s、单张 0.1~0.5s；LaTeX
//! 渲染的真实公式主体能逐字符对上，Qt 合成的小字号图会丢或误判个别字符——原样写 `^`
//! 而不是上标、笔画糊在一起时最明显。换权重时先看 `input_size`/`output_ids` 认不认形状。

use std::path::Path;
use std::sync::Mutex;

use image::GrayImage;
use ort::session::Session;
use ort::value::{DynValue, Tensor, TensorElementType, ValueType};
use pyo3::prelude::*;
use pyo3::types::PyTuple;

use crate::to_py;

/// 模型把输入尺寸声明成动态维（-1）时的兜底尺寸。
///
/// PP-FormulaNet_plus-M 把 384×384 写死在 ONNX 里，正常走的是模型声明；只有导出成动态
/// 尺寸的权重才会用到这两个常量，所以它们只是「猜一个常见的」而不是判据。
const DEFAULT_INPUT_H: usize = 384;
const DEFAULT_INPUT_W: usize = 384;

/// 归一化参数：`(x / 255 - mean) / std`。
///
/// 出自 PaddleOCR 的 `UniMERNetTestTransform`（albumentations `Normalize((0.7931,)*3,
/// (0.1738,)*3)`，见 `ppocr/data/imaug/unimernet_aug.py`）：这组数是灰度公式图的统计量，
/// 白底（255）归一化后约 1.19，与裁剪阈值 200 也对得上。
const NORM_MEAN: f32 = 0.7931;
const NORM_STD: f32 = 0.1738;

/// 裁剪阈值：拉伸到 0~255 的灰度里，比它暗的算「墨迹」。
const MARGIN_THRESHOLD: f32 = 200.0;

/// 补边用的颜色（已归一化）。
///
/// PaddleOCR 用 `ImageOps.expand(img, padding)` 且没传 `fill`，PIL 的默认 `fill=0` 是
/// 黑色，所以补边在归一化后是 `(0/255 - mean)/std ≈ -4.56`，既不是 0 也不是白底。
const PAD_VALUE: f32 = (0.0 - NORM_MEAN) / NORM_STD;

// ====================== 前处理 ======================

/// Rec.601 亮度：`0.299R + 0.587G + 0.114B`。
///
/// 参考实现全是 Rec.601（PIL 的 `convert("L")`、cv2/albumentations 的 ToGray、
/// torch 的 `rgb_to_grayscale`），而 `image` crate 的 `to_luma8` 用的是 Rec.709 权重，
/// 彩色公式图上会有偏差，所以这里自己算。
fn luma_601(r: u8, g: u8, b: u8) -> u8 {
    let value = 0.299 * r as f32 + 0.587 * g as f32 + 0.114 * b as f32;
    value.round().clamp(0.0, 255.0) as u8
}

/// 解码后的图 → 灰度图；带 alpha 时先按白底合成。
///
/// 透明像素的 RGB 通常是 0，不合成的话整张图会被当成「全是黑墨迹」——从网页/PDF 里
/// 抠出来的公式 PNG 经常是透明底。
fn to_gray(img: &image::DynamicImage) -> GrayImage {
    let rgba = img.to_rgba8();
    let has_alpha = rgba.pixels().any(|pixel| pixel[3] != 255);
    let mut gray = GrayImage::new(rgba.width(), rgba.height());
    for (x, y, pixel) in rgba.enumerate_pixels() {
        let (r, g, b) = if has_alpha {
            let alpha = pixel[3] as f32 / 255.0;
            let over_white =
                |c: u8| (c as f32 * alpha + 255.0 * (1.0 - alpha)).clamp(0.0, 255.0) as u8;
            (
                over_white(pixel[0]),
                over_white(pixel[1]),
                over_white(pixel[2]),
            )
        } else {
            (pixel[0], pixel[1], pixel[2])
        };
        gray.put_pixel(x, y, image::Luma([luma_601(r, g, b)]));
    }
    gray
}

/// 裁掉公式四周的白边：PaddleOCR 的 `UniMERNetImgDecode.crop_margin`。
///
/// 先把灰度按 min/max 拉伸到 0~255（图不一定是纯黑纯白），再用 200 作阈值圈出墨迹的
/// 外接矩形。整图一个颜色、找不到墨迹、或墨迹框宽高比超过 200:1 时不裁——这几种情况下
/// 那个框都不代表「公式本体」，硬裁只会把图毁掉。
fn crop_margin(gray: &GrayImage) -> GrayImage {
    let (w, h) = gray.dimensions();
    let mut min = u8::MAX;
    let mut max = u8::MIN;
    for pixel in gray.pixels() {
        min = min.min(pixel[0]);
        max = max.max(pixel[0]);
    }
    if max == min {
        return gray.clone();
    }
    let span = (max - min) as f32;
    let (mut x0, mut y0, mut x1, mut y1) = (w, h, 0u32, 0u32);
    for (x, y, pixel) in gray.enumerate_pixels() {
        let stretched = (pixel[0] - min) as f32 / span * 255.0;
        if stretched < MARGIN_THRESHOLD {
            x0 = x0.min(x);
            y0 = y0.min(y);
            x1 = x1.max(x + 1);
            y1 = y1.max(y + 1);
        }
    }
    // x1 <= x0 表示一个墨迹像素也没有（这时 x0 还是初始的 w）
    if x1 <= x0 || y1 <= y0 {
        return gray.clone();
    }
    let (cw, ch) = (x1 - x0, y1 - y0);
    if cw.max(ch) as f32 / cw.min(ch) as f32 > 200.0 {
        return gray.clone();
    }
    image::imageops::crop_imm(gray, x0, y0, cw, ch).to_image()
}

/// PNG → 模型输入张量数据（CHW，灰度按模型声明的通道数铺开）。
///
/// 顺序照 PaddleOCR：裁白边 → 等比缩放到「装得进」模型输入尺寸 → 居中补黑边 → 归一化。
/// 通道数取模型声明：PP-FormulaNet_plus-M 是单通道（图里自己 repeat 成 3 通道），
/// 也有导出直接要 3 通道，复制同一份灰度对两者都成立。
fn preprocess(png: &[u8], channels: usize, dst_h: usize, dst_w: usize) -> Result<Vec<f32>, String> {
    if channels == 0 || dst_h == 0 || dst_w == 0 {
        return Err(format!(
            "公式模型输入尺寸不合法: {channels}×{dst_h}×{dst_w}"
        ));
    }
    let img = image::load_from_memory_with_format(png, image::ImageFormat::Png)
        .map_err(|e| format!("解码 PNG 失败: {e}"))?;
    // 公式识别看的是墨迹形状，颜色没有语义；ToGray 提到裁剪之前，裁边与内容用的是同一份灰度
    let gray = crop_margin(&to_gray(&img));

    // 等比缩放：参考实现先按短边 resize 到 384、再 thumbnail 把长边压到 384，合起来等价于
    // 「长边缩到目标边长」；这里一步到位（省掉两次取整），滤波链因此与它略有差别
    // （它 BILINEAR+BICUBIC，这里 Triangle），实测两张图逐 token 结果一致
    let (src_w, src_h) = (gray.width().max(1) as f32, gray.height().max(1) as f32);
    let scale = (dst_w as f32 / src_w).min(dst_h as f32 / src_h);
    let new_w = ((src_w * scale).round() as usize).clamp(1, dst_w);
    let new_h = ((src_h * scale).round() as usize).clamp(1, dst_h);
    let content = image::imageops::resize(
        &gray,
        new_w as u32,
        new_h as u32,
        image::imageops::FilterType::Triangle,
    );

    // 静态输入尺寸都是 32 的倍数（384），PaddleOCR 最后那步「补到 32 的倍数」在这里是空操作；
    // 补边位置按它 random_padding=False 的分支走左侧/上侧 delta//2，也就是居中
    let x0 = (dst_w - new_w) / 2;
    let y0 = (dst_h - new_h) / 2;

    let plane = dst_h * dst_w;
    let mut data = vec![PAD_VALUE; channels * plane];
    for y in 0..new_h {
        for x in 0..new_w {
            let value =
                (content.get_pixel(x as u32, y as u32)[0] as f32 / 255.0 - NORM_MEAN) / NORM_STD;
            let offset = (y + y0) * dst_w + (x + x0);
            for c in 0..channels {
                data[c * plane + offset] = value;
            }
        }
    }
    Ok(data)
}

/// 从模型自己的输入声明里取 (通道数, 高, 宽)。
///
/// 常规导出是 `[N, C, H, W]`；维度是 -1 或者形状压根不是张量时，通道数按 1、尺寸按默认
/// 常量兜底——读模型声明比写死常量强，但动态导出的模型仍然只能靠默认值。
fn input_size(session: &Session) -> (usize, usize, usize) {
    let shape = match session.inputs().first().map(|input| input.dtype()) {
        Some(ValueType::Tensor { shape, .. }) => shape.to_vec(),
        _ => Vec::new(),
    };
    let dim = |i: usize| shape.get(i).copied().unwrap_or(-1);
    let dynamic_or = |v: i64, fallback: usize| if v > 0 { v as usize } else { fallback };
    (
        dynamic_or(dim(1), 1),
        dynamic_or(dim(2), DEFAULT_INPUT_H),
        dynamic_or(dim(3), DEFAULT_INPUT_W),
    )
}

// ====================== 输出解码 ======================

/// 从模型输出张量里取 token id 序列。
///
/// PP-FormulaNet_plus-M 的图里带着自回归解码循环，直接输出 int64 的 `[N, T]` id；也有
/// 导出只给裸 logits（float `[N, T, C]`），那就得自己贪心取 argmax。两种都认——只认一种
/// 的话「换个权重就不可用」会被误判成代码坏了。
fn output_ids(value: &DynValue) -> Result<Vec<usize>, String> {
    let dtype = value.dtype();
    let ty = match dtype {
        ValueType::Tensor { ty, .. } => ty,
        _ => return Err("公式模型输出不是张量".to_string()),
    };
    match ty {
        TensorElementType::Int64 => {
            let (shape, data) = value
                .try_extract_tensor::<i64>()
                .map_err(|e| format!("读取公式模型输出失败: {e}"))?;
            // 单图推理 batch 恒为 1，取最后一位（序列长度）那一段就是这张图的 id
            let t = shape.last().copied().unwrap_or(0).max(0) as usize;
            if data.len() < t {
                return Err("公式模型输出张量长度与形状不符".to_string());
            }
            Ok(data[..t].iter().map(|&v| v.max(0) as usize).collect())
        }
        TensorElementType::Int32 => {
            let (shape, data) = value
                .try_extract_tensor::<i32>()
                .map_err(|e| format!("读取公式模型输出失败: {e}"))?;
            let t = shape.last().copied().unwrap_or(0).max(0) as usize;
            if data.len() < t {
                return Err("公式模型输出张量长度与形状不符".to_string());
            }
            Ok(data[..t].iter().map(|&v| v.max(0) as usize).collect())
        }
        TensorElementType::Float32 => {
            let (shape, data) = value
                .try_extract_tensor::<f32>()
                .map_err(|e| format!("读取公式模型输出失败: {e}"))?;
            if shape.len() < 2 {
                return Err(format!("公式模型 logits 形状不认识: {shape:?}"));
            }
            let c = shape[shape.len() - 1].max(0) as usize;
            let t = shape[shape.len() - 2].max(0) as usize;
            if c == 0 || t == 0 || data.len() < t * c {
                return Err(format!("公式模型 logits 形状与数据长度不符: {shape:?}"));
            }
            let mut ids = Vec::with_capacity(t);
            for row_index in 0..t {
                let row = &data[row_index * c..(row_index + 1) * c];
                let mut best = 0usize;
                let mut best_v = f32::NEG_INFINITY;
                for (i, &v) in row.iter().enumerate() {
                    if v > best_v {
                        best_v = v;
                        best = i;
                    }
                }
                ids.push(best);
            }
            Ok(ids)
        }
        other => Err(format!("公式模型输出元素类型不支持: {other:?}")),
    }
}

/// GPT-2 的 ByteLevel 反表：字符码点 → 原始字节，表里没填的是 0x100（哨兵）。
///
/// tokenizer 的 decoder 是 `ByteLevel`（`inference.yml` 的 fast_tokenizer_file 里写着），
/// 它的解码不是「把 token 拼起来」而是「每个字符还原成一个字节、拼完再整体按 UTF-8 解释」
/// —— 所以 `Ġ` 是空格、`Ã©` 是 `é` 的两半，逐 token 转字符串会得到乱码。
static BYTE_DECODER: [u16; 0x144] = build_byte_decoder();

/// 按 GPT-2 的 bytes_to_unicode 规则建反表：可打印 ASCII 与 Latin-1 的可见区保持原样，
/// 其余字节顺序映射到 U+0100 起的码点。
const fn build_byte_decoder() -> [u16; 0x144] {
    let mut table = [0x100u16; 0x144];
    let mut byte = 0usize;
    let mut extra = 0u16;
    while byte < 256 {
        let printable = (byte >= 0x21 && byte <= 0x7E)
            || (byte >= 0xA1 && byte <= 0xAC)
            || (byte >= 0xAE && byte <= 0xFF);
        let codepoint = if printable {
            byte as u16
        } else {
            let c = 0x100 + extra;
            extra += 1;
            c
        };
        table[codepoint as usize] = byte as u16;
        byte += 1;
    }
    table
}

/// 把一个 token 的字符按 ByteLevel 反表还原成字节，追加到 `out`。
fn push_token_bytes(token: &str, out: &mut Vec<u8>) -> Result<(), String> {
    for ch in token.chars() {
        let mapped = BYTE_DECODER.get(ch as usize).copied().unwrap_or(0x100);
        if mapped == 0x100 {
            return Err(format!(
                "token 里的字符 {ch:?} 不在 ByteLevel 表里，词表与解码方式不匹配"
            ));
        }
        out.push(mapped as u8);
    }
    Ok(())
}

/// 特殊 token 的角色。`Some(true)` 是结束符。
///
/// 只有词表没带 `special_ids`（绑定生成 script 之外的词表）时才靠名字判断，名字各版本
/// 写法不统一（`</s>` / `eos`），所以都认下来。
fn special_role(token: &str) -> Option<bool> {
    match token {
        "</s>" | "<eos>" | "eos" | "<|endoftext|>" => Some(true),
        "<s>" | "<sos>" | "sos" | "bos" | "<bos>" | "<pad>" | "pad" | "<unk>" | "<blank>" => {
            Some(false)
        }
        _ => None,
    }
}

// ====================== tokenizer JSON ======================
//
// 只解析词表需要的子集（对象/数组/字符串/数字/字面量），故意不引 serde_json：
// 这个 crate 的依赖表里没有它，为一份扁平词表再拉一套序列化框架进来不划算。
// 代价是这里的解析器只保证「能吃下 script 生成的那份 tokenizer.json」，不做通用 JSON
// 的兼容性承诺。

/// 递归深度上限：坏文件里一串 `[[[[...` 能把递归下降的栈顶爆掉，宁可报错。
const MAX_JSON_DEPTH: usize = 64;

/// JSON 节点的最小集合。真假与 null 只做结构识别——词表里用不到它们的取值。
enum Json {
    Null,
    Bool,
    Num(f64),
    Str(String),
    Arr(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

impl Json {
    fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Obj(entries) => entries.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    fn as_str(&self) -> Option<&str> {
        match self {
            Json::Str(s) => Some(s),
            _ => None,
        }
    }

    fn as_usize(&self) -> Option<usize> {
        match self {
            Json::Num(v) if *v >= 0.0 => Some(*v as usize),
            _ => None,
        }
    }
}

struct JsonParser<'a> {
    bytes: &'a [u8],
    pos: usize,
}

impl<'a> JsonParser<'a> {
    fn new(text: &'a str) -> Self {
        Self {
            bytes: text.as_bytes(),
            pos: 0,
        }
    }

    fn parse(mut self) -> Result<Json, String> {
        self.skip_ws();
        self.value(0)
    }

    fn skip_ws(&mut self) {
        while let Some(&c) = self.bytes.get(self.pos) {
            if c.is_ascii_whitespace() {
                self.pos += 1;
            } else {
                break;
            }
        }
    }

    fn peek(&self) -> Result<u8, String> {
        self.bytes
            .get(self.pos)
            .copied()
            .ok_or_else(|| "tokenizer JSON 意外结束".to_string())
    }

    fn next_byte(&mut self) -> Result<u8, String> {
        let c = self.peek()?;
        self.pos += 1;
        Ok(c)
    }

    fn expect(&mut self, want: u8) -> Result<(), String> {
        let got = self.next_byte()?;
        if got != want {
            return Err(format!(
                "tokenizer JSON 位置 {} 期望 {:?}，实际 {:?}",
                self.pos - 1,
                want as char,
                got as char
            ));
        }
        Ok(())
    }

    fn literal(&mut self, word: &str) -> Result<(), String> {
        let end = self.pos + word.len();
        if end <= self.bytes.len() && &self.bytes[self.pos..end] == word.as_bytes() {
            self.pos = end;
            Ok(())
        } else {
            Err(format!("tokenizer JSON 里不是字面量 {word}"))
        }
    }

    fn value(&mut self, depth: usize) -> Result<Json, String> {
        if depth > MAX_JSON_DEPTH {
            return Err("tokenizer JSON 嵌套过深".to_string());
        }
        match self.peek()? {
            b'{' => self.object(depth),
            b'[' => self.array(depth),
            b'"' => Ok(Json::Str(self.string()?)),
            b't' => {
                self.literal("true")?;
                Ok(Json::Bool)
            }
            b'f' => {
                self.literal("false")?;
                Ok(Json::Bool)
            }
            b'n' => {
                self.literal("null")?;
                Ok(Json::Null)
            }
            c if c == b'-' || c.is_ascii_digit() => self.number(),
            c => Err(format!(
                "tokenizer JSON 位置 {} 出现意外字符 {:?}",
                self.pos, c as char
            )),
        }
    }

    fn object(&mut self, depth: usize) -> Result<Json, String> {
        self.expect(b'{')?;
        let mut entries = Vec::new();
        self.skip_ws();
        if self.peek()? == b'}' {
            self.pos += 1;
            return Ok(Json::Obj(entries));
        }
        loop {
            self.skip_ws();
            let key = self.string()?;
            self.skip_ws();
            self.expect(b':')?;
            self.skip_ws();
            let val = self.value(depth + 1)?;
            entries.push((key, val));
            self.skip_ws();
            match self.peek()? {
                b',' => self.pos += 1,
                b'}' => {
                    self.pos += 1;
                    break;
                }
                c => {
                    return Err(format!(
                        "tokenizer JSON 对象里缺少 , 或 }}（位置 {}，实际 {:?}）",
                        self.pos, c as char
                    ))
                }
            }
        }
        Ok(Json::Obj(entries))
    }

    fn array(&mut self, depth: usize) -> Result<Json, String> {
        self.expect(b'[')?;
        let mut items = Vec::new();
        self.skip_ws();
        if self.peek()? == b']' {
            self.pos += 1;
            return Ok(Json::Arr(items));
        }
        loop {
            self.skip_ws();
            items.push(self.value(depth + 1)?);
            self.skip_ws();
            match self.peek()? {
                b',' => self.pos += 1,
                b']' => {
                    self.pos += 1;
                    break;
                }
                c => {
                    return Err(format!(
                        "tokenizer JSON 数组里缺少 , 或 ]（位置 {}，实际 {:?}）",
                        self.pos, c as char
                    ))
                }
            }
        }
        Ok(Json::Arr(items))
    }

    fn string(&mut self) -> Result<String, String> {
        self.expect(b'"')?;
        let mut out = String::new();
        loop {
            let c = self.next_byte()?;
            match c {
                b'"' => break,
                b'\\' => {
                    let e = self.next_byte()?;
                    match e {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => out.push(self.unicode_escape()?),
                        other => {
                            return Err(format!("tokenizer JSON 里有未知转义 \\{}", other as char))
                        }
                    }
                }
                _ if c < 0x80 => out.push(c as char),
                _ => {
                    // 词表里有非 ASCII 的记号（如 Ġ），按整段 UTF-8 还原，
                    // 逐字节转 char 会把多字节字符拆成乱码
                    let start = self.pos - 1;
                    let mut end = self.pos;
                    while end < self.bytes.len() && (self.bytes[end] & 0xC0) == 0x80 {
                        end += 1;
                    }
                    let s = std::str::from_utf8(&self.bytes[start..end])
                        .map_err(|e| format!("tokenizer JSON 不是合法 UTF-8: {e}"))?;
                    out.push_str(s);
                    self.pos = end;
                }
            }
        }
        Ok(out)
    }

    fn unicode_escape(&mut self) -> Result<char, String> {
        let first = self.hex4()?;
        // 增补平面字符在 JSON 里拆成一前一后两个 \uXXXX，必须合回来
        if (0xD800..0xDC00).contains(&first) {
            self.expect(b'\\')?;
            self.expect(b'u')?;
            let second = self.hex4()?;
            if !(0xDC00..0xE000).contains(&second) {
                return Err("tokenizer JSON 里的代理对不合法".to_string());
            }
            let cp = 0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00);
            return char::from_u32(cp)
                .ok_or_else(|| "tokenizer JSON 里的代理对不是合法字符".to_string());
        }
        char::from_u32(first)
            .ok_or_else(|| format!("tokenizer JSON 里的 \\u{first:04X} 不是合法字符"))
    }

    fn hex4(&mut self) -> Result<u32, String> {
        let mut v = 0u32;
        for _ in 0..4 {
            let c = self.next_byte()?;
            let d = (c as char)
                .to_digit(16)
                .ok_or_else(|| "tokenizer JSON 里 \\u 转义不是 4 位十六进制".to_string())?;
            v = v * 16 + d;
        }
        Ok(v)
    }

    fn number(&mut self) -> Result<Json, String> {
        let start = self.pos;
        while let Some(&c) = self.bytes.get(self.pos) {
            if c.is_ascii_digit() || matches!(c, b'-' | b'+' | b'.' | b'e' | b'E') {
                self.pos += 1;
            } else {
                break;
            }
        }
        let text = std::str::from_utf8(&self.bytes[start..self.pos])
            .map_err(|e| format!("tokenizer JSON 不是合法 UTF-8: {e}"))?;
        text.parse::<f64>()
            .map(Json::Num)
            .map_err(|_| format!("tokenizer JSON 位置 {start} 处的数字无法解析: {text}"))
    }
}

fn put_token(vocab: &mut Vec<Option<String>>, id: usize, token: String) {
    if vocab.len() <= id {
        vocab.resize(id + 1, None);
    }
    vocab[id] = Some(token);
}

/// 解码要用的词表：id → token 文本，加上「哪些是特殊 token」。
struct Tokenizer {
    vocab: Vec<Option<String>>,
    /// 按 id 索引：true 表示这个 id 是特殊 token，解码时跳过
    special: Vec<bool>,
    /// 结束符 id：序列在这里截断（PaddleOCR 的 UniMERNetDecode 就是先截到第一个 `</s>`）
    eos: Option<usize>,
}

impl Tokenizer {
    fn token(&self, id: usize) -> Option<&str> {
        self.vocab.get(id).and_then(|t| t.as_deref())
    }

    fn is_special(&self, id: usize) -> bool {
        self.special.get(id).copied().unwrap_or(false)
    }
}

/// tokenizer JSON → 词表。
///
/// 形状：`{"id_to_token": {"0": "<s>", ...}, "special_ids": [0, 1, ...]}`（由
/// `main/scripts/make_formula_tokenizer.py` 生成）。为兼容手写的词表，也接受
/// `{"vocab": [...]}` 这种按顺序给 id 的形式；`special_ids` 缺失时按 token 名字判断
/// 谁是特殊 token。不假设 id 连续：缺号留 None，解码碰到就停。
fn parse_tokenizer(text: &str) -> Result<Tokenizer, String> {
    let root = JsonParser::new(text).parse()?;
    let mut vocab: Vec<Option<String>> = Vec::new();
    if let Some(Json::Obj(entries)) = root.get("id_to_token") {
        for (key, val) in entries {
            let id: usize = key
                .trim()
                .parse()
                .map_err(|_| format!("tokenizer 的 id_to_token 键不是数字: {key}"))?;
            let token = val
                .as_str()
                .ok_or_else(|| format!("tokenizer 的 id_to_token[{key}] 不是字符串"))?;
            put_token(&mut vocab, id, token.to_string());
        }
    } else if let Some(Json::Arr(items)) = root.get("vocab") {
        for (i, val) in items.iter().enumerate() {
            let token = val
                .as_str()
                .ok_or_else(|| format!("tokenizer 的 vocab[{i}] 不是字符串"))?;
            put_token(&mut vocab, i, token.to_string());
        }
    } else {
        return Err("tokenizer 格式不认识：既没有 id_to_token 也没有 vocab".to_string());
    }
    if vocab.is_empty() {
        return Err("tokenizer 词表是空的".to_string());
    }

    let mut special = vec![false; vocab.len()];
    match root.get("special_ids") {
        Some(Json::Arr(items)) => {
            for item in items {
                let id = item
                    .as_usize()
                    .ok_or_else(|| "tokenizer 的 special_ids 里有非整数".to_string())?;
                if id < special.len() {
                    special[id] = true;
                }
            }
        }
        Some(_) => return Err("tokenizer 的 special_ids 不是数组".to_string()),
        None => {
            for (id, token) in vocab.iter().enumerate() {
                if token.as_deref().and_then(special_role).is_some() {
                    special[id] = true;
                }
            }
        }
    }

    let eos = vocab
        .iter()
        .position(|t| matches!(special_role(t.as_deref().unwrap_or("")), Some(true)));
    Ok(Tokenizer {
        vocab,
        special,
        eos,
    })
}

/// id 序列 → LaTeX。
///
/// 与 UniMERNetDecode 对齐：截断到第一个结束符 → 跳过特殊 token → ByteLevel 还原字节 →
/// 整体按 UTF-8 解释。多字节字符可能被拆到相邻 token 里，所以字节必须攒在一起再转字符串。
fn decode_ids(ids: &[usize], tokenizer: &Tokenizer) -> Result<String, String> {
    let mut bytes: Vec<u8> = Vec::new();
    for &id in ids {
        if tokenizer.eos == Some(id) {
            break;
        }
        if tokenizer.is_special(id) {
            continue;
        }
        match tokenizer.token(id) {
            Some(token) => push_token_bytes(token, &mut bytes)?,
            // 越界的 id（词表里没有）已经没有可信的解释，就此打住
            None => break,
        }
    }
    Ok(String::from_utf8_lossy(&bytes).trim().to_string())
}

// ====================== 引擎 ======================

/// 公式引擎的运行态：会话 + 词表 + 输入尺寸。
struct Formula {
    session: Session,
    tokenizer: Tokenizer,
    channels: usize,
    input_h: usize,
    input_w: usize,
}

impl Formula {
    fn open(model_path: &str, tokenizer_path: &str) -> Result<Self, String> {
        // 先查文件再加载：模型没随发行包一起装是最常见的失败，
        // 直接说「哪个文件不在」比 ONNX Runtime 的报错好读
        if !Path::new(model_path).is_file() {
            return Err(format!("公式模型文件不存在: {model_path}"));
        }
        if !Path::new(tokenizer_path).is_file() {
            return Err(format!("公式 tokenizer 文件不存在: {tokenizer_path}"));
        }
        let session = Session::builder()
            .map_err(|e| e.to_string())?
            .commit_from_file(model_path)
            .map_err(|e| format!("加载公式模型失败: {e}"))?;
        let (channels, input_h, input_w) = input_size(&session);
        let text = std::fs::read_to_string(tokenizer_path)
            .map_err(|e| format!("读取 tokenizer 失败: {e}"))?;
        let tokenizer = parse_tokenizer(&text)?;
        Ok(Self {
            session,
            tokenizer,
            channels,
            input_h,
            input_w,
        })
    }

    fn recognize(&mut self, png: &[u8]) -> Result<String, String> {
        let data = preprocess(png, self.channels, self.input_h, self.input_w)?;
        self.recognize_tensor(data, self.channels, self.input_h, self.input_w)
    }

    /// 跑一次推理并解码。张量已经归一化好（CHW，行优先）。
    fn recognize_tensor(
        &mut self,
        data: Vec<f32>,
        channels: usize,
        h: usize,
        w: usize,
    ) -> Result<String, String> {
        if channels == 0 || h == 0 || w == 0 || data.len() != channels * h * w {
            return Err(format!(
                "公式模型输入张量大小不符: 数据 {} 个，形状 {channels}×{h}×{w}",
                data.len()
            ));
        }
        let tensor = Tensor::from_array(([1_i64, channels as i64, h as i64, w as i64], data))
            .map_err(|e| format!("构造函数模型输入张量失败: {e}"))?;
        // 输入不按名字喂：各版本导出的输入名不统一（x / image / input），
        // 单输入模型按位置传（ort 的 inputs! 数组形式）反而稳定
        let outputs = self
            .session
            .run(ort::inputs![tensor])
            .map_err(|e| format!("公式推理失败: {e}"))?;
        let ids = output_ids(&outputs[0])?;
        decode_ids(&ids, &self.tokenizer)
    }
}

/// PP-FormulaNet 公式识别（ONNX + tokenizer JSON）。
///
/// 每个实例自带一份模型与词表，`close()` 之后立刻释放（模型几百 MB）；
/// 与文本 `Engine` 一样不做进程级全局单例，Python 侧想开几个都行。
#[pyclass]
pub struct FormulaEngine {
    // Option 是为了让 close() 真的把模型放掉，而不是等 Python 侧回收对象
    inner: Mutex<Option<Formula>>,
}

#[pymethods]
impl FormulaEngine {
    /// 加载公式 ONNX 模型与 tokenizer JSON（两者都必须是已存在的文件）。
    #[new]
    fn new(py: Python<'_>, model_path: &str, tokenizer_path: &str) -> PyResult<Self> {
        // 加载模型是纯 IO + 计算，几百 MB 的图上秒级起步，放开 GIL
        let inner = py
            .allow_threads(|| Formula::open(model_path, tokenizer_path))
            .map_err(to_py)?;
        Ok(Self {
            inner: Mutex::new(Some(inner)),
        })
    }

    /// 识别一张 PNG 图里的公式，返回 LaTeX 文本；没识别到内容时返回空串。
    ///
    /// 收 PNG 字节而不是裸像素：公式图是 Python 侧按选区裁出来的小块，
    /// 由 Python 编码成 PNG 比再定一套裸像素协议省事，解码开销可以忽略。
    fn recognize_formula(&self, py: Python<'_>, png_bytes: &[u8]) -> PyResult<String> {
        // 推理在原生线程跑，主线程 UI 不受影响
        let latex = py
            .allow_threads(|| {
                let mut guard = self.inner.lock().map_err(|e| e.to_string())?;
                let engine = guard.as_mut().ok_or("公式引擎已关闭")?;
                engine.recognize(png_bytes)
            })
            .map_err(to_py)?;
        Ok(latex)
    }

    /// 模型是否已加载且未 close。Python 侧据此决定要不要 initialize()。
    fn is_ready(&self) -> bool {
        self.inner.lock().map(|g| g.is_some()).unwrap_or(false)
    }

    /// 释放模型。之后再调用 recognize_formula 会抛 OcrError。重复调用无副作用。
    fn close(&self) {
        if let Ok(mut guard) = self.inner.lock() {
            *guard = None;
        }
    }

    #[getter]
    fn closed(&self) -> bool {
        !self.is_ready()
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    #[pyo3(signature = (*_args))]
    fn __exit__(&self, _args: &Bound<'_, PyTuple>) -> bool {
        self.close();
        false
    }

    fn __repr__(&self) -> String {
        format!(
            "FormulaEngine({})",
            if self.is_ready() { "ready" } else { "closed" }
        )
    }
}

/// 公式模型与词表两个文件是否都在。
///
/// 只做存在性判断、不加载任何东西：Python 的 `is_available()` 会被反复调用
/// （探测、入口判定），加载一个几百 MB 的模型来回答「有没有」是不可接受的。
#[pyfunction]
pub fn formula_model_present(model_path: &str, tokenizer_path: &str) -> bool {
    Path::new(model_path).is_file() && Path::new(tokenizer_path).is_file()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn encode_png(image: &GrayImage) -> Vec<u8> {
        let mut buffer = Cursor::new(Vec::new());
        image::DynamicImage::ImageLuma8(image.clone())
            .write_to(&mut buffer, image::ImageFormat::Png)
            .expect("测试图应能编码成 PNG");
        buffer.into_inner()
    }

    #[test]
    fn reads_id_to_token_map() {
        let tokenizer =
            parse_tokenizer(r#"{"id_to_token": {"0": "<pad>", "2": "\\frac", "3": "{"}}"#)
                .expect("词表应能解析");
        assert_eq!(tokenizer.token(0), Some("<pad>"));
        assert_eq!(tokenizer.token(1), None);
        assert_eq!(tokenizer.token(2), Some("\\frac"));
        // 没有 special_ids 时按名字兜底：<pad> 是特殊 token，但不能当结束符
        assert!(tokenizer.is_special(0));
        assert_eq!(tokenizer.eos, None);
    }

    #[test]
    fn reads_vocab_array() {
        let tokenizer =
            parse_tokenizer(r#"{"vocab": ["a", "Ġ", "\u00d7"]}"#).expect("词表应能解析");
        assert_eq!(tokenizer.token(0), Some("a"));
        assert_eq!(tokenizer.token(1), Some("Ġ"));
        assert_eq!(tokenizer.token(2), Some("\u{d7}"));
    }

    #[test]
    fn uses_special_ids_and_finds_eos() {
        let tokenizer = parse_tokenizer(
            r#"{"id_to_token": {"0": "<s>", "1": "<pad>", "2": "</s>", "3": "[END_REF]", "4": "x"},
                "special_ids": [0, 1, 2]}"#,
        )
        .expect("词表应能解析");
        // special_ids 说了算：没列进去的 [END_REF] 不算特殊 token
        assert!(tokenizer.is_special(0));
        assert!(!tokenizer.is_special(3));
        assert_eq!(tokenizer.eos, Some(2));
    }

    #[test]
    fn decodes_until_eos_and_skips_specials() {
        let tok = parse_tokenizer(
            r#"{"id_to_token": {"0": "<s>", "1": "<pad>", "2": "</s>", "3": "x", "4": "Ġ", "5": "=", "6": "y", "7": "[END_REF]"},
                "special_ids": [0, 1, 2, 7]}"#,
        )
        .expect("词表应能解析");
        assert_eq!(tok.eos, Some(2));
        assert!(tok.is_special(7));
        // <s>/<pad> 跳过、Ġ 还原成空格、[END_REF] 跳过、</s> 之后的内容不输出
        let ids = [0, 3, 4, 5, 7, 2, 6];
        assert_eq!(decode_ids(&ids, &tok).expect("解码应成功"), "x =");
    }

    #[test]
    fn byte_level_restores_non_ascii() {
        // "Ã" + "©" 是 "é" 的两个 UTF-8 字节被 ByteLevel 拆开的样子：逐 token 转字符串
        // 会得到乱码，必须攒成字节再整体解释；"Ġ" 是空格（首尾空白最后会被 trim 掉）
        let tok = parse_tokenizer(r#"{"id_to_token": {"0": "Ã", "1": "©", "2": "Ġ", "3": "ab"}}"#)
            .expect("词表应能解析");
        assert_eq!(decode_ids(&[0, 1, 2, 3], &tok).expect("解码应成功"), "é ab");
        assert_eq!(decode_ids(&[2, 3], &tok).expect("解码应成功"), "ab");
    }

    #[test]
    fn unknown_shape_is_rejected() {
        assert!(parse_tokenizer(r#"{"tokens": ["a"]}"#).is_err());
        assert!(parse_tokenizer(r#"{"vocab": [[1]]}"#).is_err());
        assert!(parse_tokenizer(r#"{"id_to_token": {"x": "a"}}"#).is_err());
        assert!(parse_tokenizer(r#"{"vocab": ["a""#).is_err());
        assert!(parse_tokenizer(r#"{"vocab": ["a"], "special_ids": ["x"]}"#).is_err());
    }

    #[test]
    fn crop_margin_boxes_the_ink() {
        let mut image = GrayImage::from_pixel(100, 60, image::Luma([255u8]));
        for y in 20..40 {
            for x in 30..50 {
                image.put_pixel(x, y, image::Luma([0u8]));
            }
        }
        let cropped = crop_margin(&image);
        assert_eq!(cropped.dimensions(), (20, 20));
    }

    #[test]
    fn crop_margin_keeps_uniform_image() {
        // 纯白图（max == min）没有墨迹可裁，硬裁出来的框只会比原图更小
        let blank = GrayImage::from_pixel(32, 16, image::Luma([255u8]));
        assert_eq!(crop_margin(&blank).dimensions(), (32, 16));
    }

    #[test]
    fn gray_uses_rec601_and_white_background_for_alpha() {
        // 参考实现（PIL/cv2/torch）都是 Rec.601：纯红 ≈ 0.299*255 ≈ 76，不是 Rec.709 的 54
        assert_eq!(luma_601(255, 0, 0), 76);

        // 全透明 PNG（RGB 全 0）必须按白底合成，否则整张图会被当成全黑墨迹
        let transparent = image::RgbaImage::from_pixel(4, 4, image::Rgba([0, 0, 0, 0]));
        let gray = to_gray(&image::DynamicImage::ImageRgba8(transparent));
        assert_eq!(gray.get_pixel(0, 0)[0], 255);
    }

    #[test]
    fn preprocess_letterboxes_and_normalizes() {
        // 白底 + 中间一块墨迹（裁边后剩 20×10，宽高比 2:1）：按长边缩到 384×192，
        // 上下各补 (384-192)/2 = 96 行黑边
        let mut image = GrayImage::from_pixel(40, 20, image::Luma([255u8]));
        for y in 5..15 {
            for x in 10..30 {
                image.put_pixel(x, y, image::Luma([0u8]));
            }
        }
        let png = encode_png(&image);
        let data = preprocess(&png, 1, 384, 384).expect("前处理应成功");
        assert_eq!(data.len(), 384 * 384);
        // 左上角落在上补边里，值必须是黑的归一化结果
        assert!((data[0] - PAD_VALUE).abs() < 1e-6);
        // 中心落在内容区，值必须是归一化过的墨迹（负值）
        let center = data[192 * 384 + 192];
        assert!(center < 0.0, "中心应是墨迹（负值），实际 {center}");
    }
}
