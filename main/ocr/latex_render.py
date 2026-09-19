# -*- coding: utf-8 -*-
"""LaTeX → 排版盒子：公式识别的结果不能只丢一串源码给用户看。

设计取舍：**自己排版，不装渲染引擎**。Qt 没有 LaTeX 排版能力，常见做法是塞
QtWebEngine + MathJax 或 matplotlib——前者给发行包加一百多 MB，后者要新增依赖，两者都和
这个项目「onefile、常驻内存十几 MB」的取向冲突。所以这里用 Qt 自己的字体度量做一个
小而够用的排版器：把公式拆成盒子（字形 / 横向排列 / 上下堆叠 / 上下标 / 根号 / 矩阵网格），
每个盒子自己报告宽高基线、自己画。

覆盖的是公式识别结果里真正会出现的东西：希腊字母与常见运算符、``\\frac``、``\\sqrt``、
上下标、``\\text``/``\\mathbf`` 一类字体切换、``\\left``/``\\right``、矩阵与 cases 环境。
**不追求完整 TeX**：没见过的命令去掉反斜杠按字面画出来，宁可露出源码也不吞掉内容——
用户至少能看出模型识别出了什么。
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

#: 公式字体候选。按平台常见度排：Cambria Math 在 Windows 上必装，STIX 在 macOS 上
#: 比较常见；都没有时退到系统默认字体（字形覆盖差一些，但要能用）。
MATH_FONTS = ("STIX Two Math", "Cambria Math", "Latin Modern Math", "DejaVu Serif")

#: 命令 → Unicode（不带参数的符号）
SYMBOLS = {
    # 小写希腊字母
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "sigma": "σ", "varsigma": "ς", "tau": "τ",
    "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    # 大写希腊字母
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # 运算符
    "times": "×", "div": "÷", "pm": "±", "mp": "∓", "cdot": "·", "ast": "∗",
    "star": "⋆", "circ": "∘", "bullet": "•", "oplus": "⊕", "otimes": "⊗",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "propto": "∝",
    "ll": "≪", "gg": "≫", "doteq": "≐",
    # 箭头与关系
    "to": "→", "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "mapsto": "↦",
    "uparrow": "↑", "downarrow": "↓", "implies": "⟹", "iff": "⟺",
    # 集合与逻辑
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "supset": "⊃",
    "supseteq": "⊇", "cup": "∪", "cap": "∩", "setminus": "∖", "emptyset": "∅",
    "varnothing": "∅", "forall": "∀", "exists": "∃", "nexists": "∄", "neg": "¬",
    "lnot": "¬", "land": "∧", "wedge": "∧", "lor": "∨", "vee": "∨", "therefore": "∴",
    "because": "∵",
    # 大运算符与微积分
    "infty": "∞", "partial": "∂", "nabla": "∇", "sum": "∑", "prod": "∏",
    "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "triangle": "△", "square": "□",
    "degree": "°", "prime": "′", "ldots": "…", "cdots": "⋯", "dots": "…",
    "vdots": "⋮", "ddots": "⋱", "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ", "Im": "ℑ",
    "aleph": "ℵ", "wp": "℘", "top": "⊤", "bot": "⊥", "vdash": "⊢", "models": "⊨",
}

#: 以名字当正常字体画的函数名（``\sin x`` 里的 ``sin`` 是正体）
UPRIGHT_FUNCTIONS = (
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "log", "ln", "lg", "exp", "lim", "limsup", "liminf",
    "max", "min", "sup", "inf", "det", "dim", "ker", "deg", "gcd", "arg", "mod",
)

#: 定界符命令 → 字符（``\left``/``\right`` 与 ``\big`` 系列共用）
DELIMITERS = {
    "(": "(", ")": ")", "[": "[", "]": "]", "\\{": "{", "\\}": "}",
    "lbrace": "{", "rbrace": "}", "langle": "⟨", "rangle": "⟩", "lvert": "|",
    "rvert": "|", "vert": "|", "Vert": "‖", "|": "‖", ".": "",
}

#: 变音命令 → Unicode 组合记号（字体支持不好时看上去会淡一些，但比丢掉整个字母好）
ACCENTS = {
    "hat": "\u0302", "widehat": "\u0302", "bar": "\u0304", "overline": "\u0304",
    "vec": "\u20d7", "tilde": "\u0303", "widetilde": "\u0303", "dot": "\u0307",
    "ddot": "\u0308", "acute": "\u0301", "grave": "\u0300", "check": "\u030c",
}

#: 字体切换命令 → (是否粗体, 是否斜体)
STYLES = {
    "mathbf": (True, False), "boldsymbol": (True, False), "bm": (True, False),
    "mathit": (False, True), "mathrm": (False, False), "text": (False, False),
    "textrm": (False, False), "operatorname": (False, False), "mathsf": (False, False),
    "mathtt": (False, False), "mathcal": (False, True), "mathscr": (False, True),
    "mathfrak": (True, False), "mathbb": (True, False), "textbf": (True, False),
    "textit": (False, True), "textnormal": (False, False), "textup": (False, False),
}

#: 只影响间距的命令 → 空白宽度（以字号为单位）
SPACES = {"\\,": 0.17, "\\:": 0.22, "\\;": 0.28, "\\!": -0.17, "\\ ": 0.33,
          "\\quad": 1.0, "\\qquad": 2.0, "~": 0.33}

#: 矩阵类环境：内容按 ``&`` 分列、``\\`` 分行
MATRIX_ENVIRONMENTS = ("matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix",
                       "cases", "array", "aligned", "align", "gathered")

#: 环境名 → 两侧的括号
_ENV_BRACKETS = {"pmatrix": ("(", ")"), "bmatrix": ("[", "]"),
                 "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"),
                 "cases": ("{", "")}

#: 解析时要停下来的记号（原文形式，命令与括号统一成值比较）
_GROUP_END = ("}",)
_BRACKET_END = ("]",)
_ENV_END = ("\\end",)

_TOKEN_RE = re.compile(r"""
    (?P<command>\\[A-Za-z]+|\\[^A-Za-z])
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<letter>[A-Za-z])
  | (?P<space>\s+)
  | (?P<other>.)
""", re.VERBOSE)


# ══════════════════════════════════════════════════════════
# 词法与语法
# ══════════════════════════════════════════════════════════

@dataclass
class Node:
    """解析出来的一小段：``kind`` 决定后面哪些字段有意义。"""

    kind: str                       # group/text/symbol/styled/accent/space/frac/sqrt/script/grid
    children: List["Node"] = field(default_factory=list)
    text: str = ""
    base: Optional["Node"] = None   # sqrt 的根指数
    sup: Optional["Node"] = None
    sub: Optional["Node"] = None
    rows: List[List["Node"]] = field(default_factory=list)
    bold: bool = False
    italic: bool = False
    width: float = 0.0              # 仅 space 用：以字号为单位的空白


def _tokenize(latex: str) -> List[Tuple[str, str]]:
    return [(match.lastgroup, match.group()) for match in _TOKEN_RE.finditer(latex or "")]


class _Parser:
    """递归下降：只认公式识别结果里会出现的那点结构。"""

    def __init__(self, tokens):
        self._tokens = tokens
        self._index = 0

    def _peek(self) -> Tuple[Optional[str], str]:
        return self._tokens[self._index] if self._index < len(self._tokens) else (None, "")

    def _next(self) -> Tuple[Optional[str], str]:
        kind, value = self._peek()
        self._index += 1
        return kind, value

    def _consume_if(self, value: str) -> bool:
        if self._peek()[1] == value:
            self._next()
            return True
        return False

    def parse(self, stop: Sequence[str] = ()) -> Node:
        """解析到「下一个记号的值在 stop 里」为止（不消费那个记号）。"""
        children: List[Node] = []
        while True:
            kind, value = self._peek()
            if kind is None or value in stop:
                break
            children.append(self._parse_atom())
        return Node("group", children=children)

    # ── 单元素 ──

    def _parse_atom(self) -> Node:
        kind, value = self._next()
        if kind == "command":
            return self._parse_command(value)
        if kind == "space":
            # 数学模式里空格本该忽略，但这里的输入是 OCR 结果——原图里 `E = mc^2` 的空隙
            # 是真实存在的，全挤掉会读成 `E=mc^2`。给一点点宽度，够看出分组就行。
            return Node("space", width=0.25)
        if kind == "number":
            return Node("text", text=value)
        if kind == "letter":
            return Node("text", text=value, italic=True)
        if kind == "other":
            return self._parse_other(value)
        return Node("text", text=value)

    def _parse_other(self, value: str) -> Node:
        if value == "{":
            return self._parse_group_body()
        if value == "^":
            return Node("script", sup=self._parse_argument())
        if value == "_":
            return Node("script", sub=self._parse_argument())
        if value == "'":
            return Node("script", sup=Node("symbol", text="′"))
        if value == "&":
            return Node("space", width=0.6)
        return Node("text", text=value)

    def _parse_group_body(self) -> Node:
        """已经吃掉了 ``{``：解析到 ``}`` 并把它吃掉。"""
        node = self.parse(stop=_GROUP_END)
        self._consume_if("}")
        return node

    def _parse_argument(self) -> Node:
        """取一个参数：``{...}`` 一组，或紧跟的一个原子。"""
        kind, value = self._peek()
        if kind is None:
            return Node("group", children=[])
        if value == "{":
            self._next()
            return self._parse_group_body()
        return self._parse_atom()

    def _parse_command(self, command: str) -> Node:
        name = command[1:]

        if command == "\\\\":
            # 公式里的换行（矩阵里另有处理）：当一段空白，不画反斜杠
            return Node("space", width=1.5)
        if command in SPACES:
            return Node("space", width=SPACES[command])
        if name in ("left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr",
                    "Bigl", "Bigr", "biggl", "biggr", "Biggl", "Biggr", "middle"):
            return self._parse_delimiter()
        if name in STYLES:
            return self._parse_styled(name)
        if name in ACCENTS:
            return Node("accent", children=[self._parse_argument()], text=ACCENTS[name])
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            numerator = self._parse_argument()
            denominator = self._parse_argument()
            return Node("frac", rows=[[numerator], [denominator]])
        if name == "sqrt":
            return self._parse_sqrt()
        if name == "begin":
            return self._parse_environment()
        if name in ("end", "displaystyle", "textstyle", "limits", "nolimits", "notag",
                    "label", "tag", "left_ignored"):
            return Node("group", children=[])
        if name in UPRIGHT_FUNCTIONS:
            return Node("text", text=name)
        if name in SYMBOLS:
            return Node("symbol", text=SYMBOLS[name])
        # 不认识的命令：去掉反斜杠留字面——宁可露出源码也不吞内容
        return Node("text", text=name)

    def _parse_delimiter(self) -> Node:
        kind, value = self._peek()
        if kind == "command":
            self._next()
            return Node("symbol", text=DELIMITERS.get(value[1:], value[1:]))
        if kind == "other":
            self._next()
            return Node("symbol", text=DELIMITERS.get(value, value))
        return Node("group", children=[])

    def _parse_sqrt(self) -> Node:
        index = None
        if self._peek()[1] == "[":
            self._next()
            index = self.parse(stop=_BRACKET_END)
            self._consume_if("]")
        return Node("sqrt", children=[self._parse_argument()], base=index)

    def _parse_styled(self, name: str) -> Node:
        bold, italic = STYLES[name]
        return Node("styled", children=[self._parse_argument()], bold=bold, italic=italic)

    def _parse_environment(self) -> Node:
        """``\\begin{matrix}…\\end{matrix}`` → 网格；环境名不认识时按 matrix 处理。"""
        self._consume_if("{")
        name_node = self.parse(stop=_GROUP_END)
        self._consume_if("}")
        name = "".join(child.text for child in name_node.children).strip()
        if name not in MATRIX_ENVIRONMENTS:
            name = "matrix"

        rows: List[List[List[Node]]] = []      # 行 → 格 → 格里的节点
        cells: List[List[Node]] = []            # 当前行的格子
        current: List[Node] = []                # 当前格子
        while True:
            kind, value = self._peek()
            if kind is None:
                break
            if value in _ENV_END:
                self._next()
                self._consume_if("{")
                self.parse(stop=_GROUP_END)
                self._consume_if("}")
                break
            if value and value.strip("\\") == "":
                # 行分隔：``\\``（矩阵与 cases 里的换行）
                self._next()
                cells.append(current)
                rows.append(cells)
                cells, current = [], []
                continue
            if value == "&":
                # 列分隔：切一格（格子之间的间距由 _GridBox 统一给）
                self._next()
                cells.append(current)
                current = []
                continue
            current.append(self._parse_atom())
        cells.append(current)
        rows.append(cells)

        # 末尾的空行/空格子只可能来自结尾处的分隔符，丢掉它们
        if rows and all(not cell for cell in rows[-1]):
            rows.pop()
        grid_rows = [[Node("group", children=cell) for cell in row] for row in rows]

        opening, closing = _ENV_BRACKETS.get(name, ("", ""))
        grid = Node("grid", rows=grid_rows)
        if not opening and not closing:
            return Node("group", children=[grid])
        # 括号要跟着内容长高（矩阵两行时用正常字号的括号会显得小气），所以左右括号与
        # 内容包在同一个节点里，排版时一起量。
        return Node("group", children=[
            Node("delim", children=[grid], text=opening,
                 base=Node("symbol", text=closing) if closing else None),
        ])


def parse_latex(latex: str) -> Node:
    """把 LaTeX 源码解析成节点树（对任何输入都返回一棵树，不抛异常）。"""
    return _attach_scripts(_Parser(_tokenize(latex)).parse())


def _attach_scripts(node: Node) -> Node:
    """把 ``^``/``_`` 挂到它前面的原子上（``x^2`` 解析出来是两个同级节点）。

    上标紧跟下标（``x_i^2``）时合并成同一个 script 节点，否则渲染出来会错位。
    """
    if node.kind == "grid":
        node.rows = [[_attach_scripts(cell) for cell in row] for row in node.rows]
        return node
    if node.kind not in ("group", "styled", "accent"):
        return node

    result: List[Node] = []
    for child in node.children:
        child = _attach_scripts(child)
        if child.kind == "script" and result and result[-1].kind != "space":
            base = result.pop()
            if base.kind == "script":
                base.sup = child.sup or base.sup
                base.sub = child.sub or base.sub
            else:
                base = Node("script", base=base, sup=child.sup, sub=child.sub)
            result.append(base)
            continue
        result.append(child)
    node.children = result
    return node


# ══════════════════════════════════════════════════════════
# 排版盒子
# ══════════════════════════════════════════════════════════

def make_font(pixel_size: int = 22) -> QFont:
    font = QFont()
    if hasattr(font, "setFamilies"):
        font.setFamilies(list(MATH_FONTS))
    else:  # pragma: no cover - 本项目锁 PySide6，这里只是不假设 Qt6 一定在
        font.setFamily(MATH_FONTS[0])
    font.setPixelSize(pixel_size)
    return font


class _Box:
    """盒子基类：宽、基线上方高度、基线下方高度，以及在给定基线与左边界处画自己。"""

    def __init__(self) -> None:
        self.width = 0.0
        self.ascent = 0.0
        self.descent = 0.0

    @property
    def height(self) -> float:
        return self.ascent + self.descent

    def paint(self, painter: QPainter, x: float, baseline: float) -> None:
        raise NotImplementedError


class _Glyph(_Box):
    """一坨文字：一个字符，或者一个函数名。"""

    def __init__(self, text: str, font: QFont, color: QColor, *, italic: bool = False) -> None:
        super().__init__()
        self.text = text
        self.color = color
        font = QFont(font)
        font.setItalic(italic)
        self.font = font
        metrics = QFontMetricsF(font)
        self.width = metrics.horizontalAdvance(text)
        self.ascent = metrics.ascent()
        self.descent = metrics.descent()

    def paint(self, painter: QPainter, x: float, baseline: float) -> None:
        if not self.text:
            return
        painter.setFont(self.font)
        painter.setPen(QPen(self.color))
        painter.drawText(QPointF(x, baseline), self.text)


class _HSpace(_Box):
    def __init__(self, width: float) -> None:
        super().__init__()
        self.width = max(0.0, width)

    def paint(self, painter: QPainter, x: float, baseline: float) -> None:
        return None


class _HBox(_Box):
    """横向排列。"""

    def __init__(self, children: List[_Box], spacing: float = 0.0) -> None:
        super().__init__()
        self.children = children
        self.spacing = spacing
        for index, child in enumerate(children):
            if index:
                self.width += spacing
            self.ascent = max(self.ascent, child.ascent)
            self.descent = max(self.descent, child.descent)
            self.width += child.width

    def paint(self, painter: QPainter, x: float, baseline: float) -> None:
        cursor = x
        for index, child in enumerate(self.children):
            if index:
                cursor += self.spacing
            child.paint(painter, cursor, baseline)
            cursor += child.width


class _FractionBox(_Box):
    """分数：分子分母上下堆叠，中间一条分数线。"""

    def __init__(self, numerator: _Box, denominator: _Box, font: QFont, color: QColor) -> None:
        super().__init__()
        self.numerator = numerator
        self.denominator = denominator
        self.color = color
        self.gap = max(2.0, font.pixelSize() * 0.18)
        self.width = max(numerator.width, denominator.width) + font.pixelSize() * 0.2
        self.ascent = numerator.height + self.gap
        self.descent = denominator.height + self.gap

    def paint(self, painter: QPainter, x: float, baseline: float) -> None:
        rule_y = baseline - self.gap
        bar_left = x + (self.width - max(self.numerator.width, self.denominator.width)) / 2
        painter.setPen(QPen(self.color, 1.2))
        painter.drawLine(QPointF(bar_left, rule_y),
                         QPointF(bar_left + max(self.numerator.width, self.denominator.width),
                                 rule_y))
        self.numerator.paint(painter,
                             x + (self.width - self.numerator.width) / 2,
                             rule_y - self.gap - self.numerator.descent)
        self.denominator.paint(painter,
                               x + (self.width - self.denominator.width) / 2,
                               baseline + self.gap + self.denominator.ascent)


class _ScriptBox(_Box):
    """上下标：上标抬到基线以上、下标压到基线以下（字号已由调用方缩小）。"""

    def __init__(self, base: Optional[_Box], sup: Optional[_Box], sub: Optional[_Box],
                 font: QFont) -> None:
        super().__init__()
        self.base = base
        self.sup = sup
        self.sub = sub
        shift = font.pixelSize() * 0.40
        self.shift = shift
        base_width = base.width if base is not None else 0.0
        base_ascent = base.ascent if base is not None else 0.0
        base_descent = base.descent if base is not None else 0.0
        self.width = base_width + max(sup.width if sup else 0.0, sub.width if sub else 0.0)
        self.ascent = max(base_ascent, shift + (sup.ascent if sup is not None else 0.0))
        self.descent = max(base_descent, shift + (sub.descent if sub is not None else 0.0))

    def paint(self, painter: QPainter, x: float, baseline: float) -> None:
        if self.base is not None:
            self.base.paint(painter, x, baseline)
        script_x = x + (self.base.width if self.base is not None else 0.0)
        if self.sup is not None:
            self.sup.paint(painter, script_x, baseline - self.shift)
        if self.sub is not None:
            self.sub.paint(painter, script_x, baseline + self.shift)


class _SqrtBox(_Box):
    """根号：一条折线画出勾与上横线，被开方内容放在里面，根指数放在左上角。"""

    def __init__(self, content: _Box, index: Optional[_Box], font: QFont, color: QColor) -> None:
        super().__init__()
        self.content = content
        self.index = index
        self.color = color
        self.pad = max(2.0, font.pixelSize() * 0.14)
        self.radical_width = font.pixelSize() * 0.55
        # 根指数摆在横线上方：它的底边贴住横线，整个高度都算进 ascent
        self.index_offset = index.descent if index is not None else 0.0
        index_height = index.height if index is not None else 0.0
        self.width = self.radical_width + content.width + self.pad + 2.0
        self.ascent = index_height + content.height + self.pad
        self.descent = self.pad

    def paint(self, painter: QPainter, x: float, baseline: float) -> None:
        content_top = baseline - self.content.ascent
        rule_y = content_top - self.pad
        path = QPainterPath(QPointF(x, content_top + self.content.height * 0.45))
        path.lineTo(QPointF(x + self.radical_width * 0.4, baseline + self.pad))
        path.lineTo(QPointF(x + self.radical_width, rule_y))
        path.lineTo(QPointF(x + self.width, rule_y))
        painter.setPen(QPen(self.color, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        if self.index is not None:
            self.index.paint(painter, x, rule_y - self.index_offset)
        self.content.paint(painter, x + self.radical_width + self.pad, baseline)


class _GridBox(_Box):
    """矩阵 / cases：先量出每列最大宽度、每行最大高度，再按网格摆。"""

    def __init__(self, cells: List[List[_Box]], font: QFont, color: QColor) -> None:
        super().__init__()
        self.cells = cells
        self.color = color
        self.column_gap = font.pixelSize() * 0.9
        self.row_gap = font.pixelSize() * 0.35
        columns = max((len(row) for row in cells), default=0)
        self.column_widths = [
            max((row[index].width for row in cells if index < len(row)), default=0.0)
            for index in range(columns)
        ]
        self.row_heights = [
            max((cell.height for cell in row), default=0.0) for row in cells
        ]
        self.width = sum(self.column_widths) + self.column_gap * max(0, columns - 1)
        total_height = sum(self.row_heights) + self.row_gap * max(0, len(cells) - 1)
        self.ascent = total_height / 2
        self.descent = total_height - self.ascent

    def paint(self, painter: QPainter, x: float, baseline: float) -> None:
        top = baseline - self.ascent
        for row_index, row in enumerate(self.cells):
            row_top = top + sum(self.row_heights[:row_index]) + self.row_gap * row_index
            row_height = self.row_heights[row_index]
            cursor = x
            for column_index, cell in enumerate(row):
                cell_baseline = row_top + (row_height - cell.height) / 2 + cell.ascent
                cell.paint(painter,
                           cursor + (self.column_widths[column_index] - cell.width) / 2,
                           cell_baseline)
                cursor += self.column_widths[column_index] + self.column_gap


class _Builder:
    """节点树 → 盒子树。字号随层级缩小（上下标、分母），到底线就不再缩。"""

    def __init__(self, font: QFont, color: QColor) -> None:
        self.font = font
        self.color = color
        self.base_size = font.pixelSize()

    def _scaled(self, factor: float) -> QFont:
        font = QFont(self.font)
        font.setPixelSize(max(8, int(round(self.base_size * factor))))
        return font

    def build(self, node: Node, font: Optional[QFont] = None) -> _Box:
        font = font or self.font
        if node.kind == "group":
            return _HBox([self.build(child, font) for child in node.children])
        if node.kind == "text":
            return _Glyph(node.text, font, self.color, italic=node.italic)
        if node.kind == "symbol":
            return _Glyph(node.text, font, self.color)
        if node.kind == "space":
            return _HSpace(node.width * font.pixelSize())
        if node.kind == "styled":
            styled = QFont(font)
            styled.setBold(node.bold)
            styled.setItalic(node.italic)
            return _HBox([self.build(child, styled) for child in node.children])
        if node.kind == "accent":
            inner = _HBox([self.build(child, font) for child in node.children])
            return _HBox([inner, _Glyph(node.text, font, self.color)])
        if node.kind == "frac":
            numerator = self.build(node.rows[0][0], self._scaled(0.92))
            denominator = self.build(node.rows[1][0], self._scaled(0.92))
            return _FractionBox(numerator, denominator, font, self.color)
        if node.kind == "sqrt":
            content = _HBox([self.build(child, font) for child in node.children])
            index = self.build(node.base, self._scaled(0.7)) if node.base is not None else None
            return _SqrtBox(content, index, font, self.color)
        if node.kind == "script":
            base = self.build(node.base, font) if node.base is not None else None
            sup = self.build(node.sup, self._scaled(0.72)) if node.sup is not None else None
            sub = self.build(node.sub, self._scaled(0.72)) if node.sub is not None else None
            return _ScriptBox(base, sup, sub, font)
        if node.kind == "grid":
            cells = [[self.build(cell, font) for cell in row] for row in node.rows]
            return _GridBox(cells, font, self.color)
        if node.kind == "delim":
            return self._build_delimiter(node, font)
        return _HSpace(0.0)

    def _build_delimiter(self, node: Node, font: QFont) -> _Box:
        """左右括号 + 内容：括号字号跟着内容高度放大，最多放大到 2.4 倍。

        上限是刻意的：OCR 出的公式偶尔会把内容排得很高，括号无限放大就变成两根奇怪的竖线。
        """
        inner = _HBox([self.build(child, font) for child in node.children])
        closing_char = node.base.text if node.base is not None else ""
        reference = _Glyph("(", font, self.color)
        factor = 1.0
        if reference.height > 0:
            factor = min(2.4, max(1.0, inner.height / reference.height))
        delimiter_font = font if factor <= 1.05 else self._scaled(factor)
        boxes: List[_Box] = []
        if node.text:
            boxes.append(_Glyph(node.text, delimiter_font, self.color))
        boxes.append(inner)
        if closing_char:
            boxes.append(_Glyph(closing_char, delimiter_font, self.color))
        return _HBox(boxes)


def build_layout(latex: str, *, pixel_size: int = 22, color: Optional[QColor] = None) -> _Box:
    """把 LaTeX 排成盒子（调用方一般直接用 ``paint_latex`` / ``render_latex_image``）。"""
    return _Builder(make_font(pixel_size), QColor(color or "#000000")).build(parse_latex(latex))


def layout_size(latex: str, *, pixel_size: int = 22) -> Tuple[float, float]:
    """排好之后的 (宽, 高) 浮点数，用来给控件定尺寸。"""
    box = build_layout(latex, pixel_size=pixel_size)
    return box.width, box.height


def paint_latex(painter: QPainter, latex: str, rect: QRectF, *,
                pixel_size: int = 22, color: Optional[QColor] = None) -> None:
    """在 ``rect`` 里居中画出公式（rect 小于公式时按公式尺寸画，不裁切）。"""
    box = build_layout(latex, pixel_size=pixel_size, color=color)
    x = rect.x() + max(0.0, (rect.width() - box.width) / 2)
    baseline = rect.y() + max(box.ascent, (rect.height() - box.height) / 2 + box.ascent)
    box.paint(painter, x, baseline)


def render_latex_image(latex: str, *, pixel_size: int = 22, color: Optional[QColor] = None,
                       padding: int = 12, device_pixel_ratio: float = 1.0):
    """把公式渲染成 QImage（导出、贴图一类不需要控件的场合用）。"""
    from PySide6.QtGui import QImage

    box = build_layout(latex, pixel_size=pixel_size, color=color)
    ratio = max(1.0, float(device_pixel_ratio or 1.0))
    width = max(1, int(box.width + padding * 2))
    height = max(1, int(box.height + padding * 2))
    image = QImage(max(1, int(width * ratio)), max(1, int(height * ratio)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.scale(ratio, ratio)
    paint_latex(painter, latex, QRectF(0, 0, width, height), pixel_size=pixel_size, color=color)
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image


def looks_like_latex(text: str) -> bool:
    """粗略判断一段文本是不是 LaTeX：有反斜杠命令，或者有配平的成对花括号。

    只在「让不让预览渲染」这一处用；判错了两边都有退路（源码照样显示），所以不追求准。
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    if re.search(r"\\[A-Za-z]+", stripped):
        return True
    return "{" in stripped and stripped.count("{") == stripped.count("}")
