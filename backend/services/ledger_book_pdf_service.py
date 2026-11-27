# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""连续电子账簿 PDF 渲染（封面 + 带页码目录 + 书签 + 账页 + 落款）。

只消费 ``build_ledger_book`` / ``LedgerBookSnapshot.payload`` 的权威 JSON，
不重算任何余额或发生额（A3 真相唯一）。分页与 ``ledger_book_service._paginate``
同口径：每页行数取 ``pagination.pageSize``，所以目录页码、页脚页码与 PDF 物理页
一一对应（目录超过一页时，其后章节整体顺延，页脚同步）。

水印语义：已签发 = 无水印；已作废 = 红色「已作废」；其余 = 灰色「预览件」。
"""
from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

import anyio.to_thread
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from core.money import ZERO, money

logger = logging.getLogger(__name__)

PAGE_W, PAGE_H = A4
LEFT = 18 * mm  # 装订边
RIGHT = 12 * mm
TOP = 15 * mm
BOTTOM = 16 * mm
CONTENT_W = PAGE_W - LEFT - RIGHT
TOC_ROWS_PER_PAGE = 38
DEFAULT_PAGE_SIZE = {"balance": 12, "detail": 10, "journal": 12, "chronological": 12}

class PdfPageCountMismatch(RuntimeError):
    """PDF 实际页数与账簿分页真值不一致。

    目录页码、页脚「第 X 页 共 Y 页」都以 ``pagination.totalPages`` 为唯一真值
    （见 ``ledger_book_service._paginate``）。PDF 一旦多一页或少一页，就等于给
    审计方一份页码自相矛盾的账簿——宁可失败，也不归档这样的文件。
    """


# (常规字体, 粗体字体)，均为 TrueType 轮廓（CFF/OTF 的 TTFont 不支持，如 Hiragino Sans GB）
_FONT_CANDIDATES: list[tuple[str, str | None]] = [
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", None),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", None),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", None),
    ("/System/Library/Fonts/STHeiti Light.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", None),
    ("/Library/Fonts/Arial Unicode.ttf", None),
    ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
    ("C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/simhei.ttf"),
    ("C:/Windows/Fonts/simhei.ttf", None),
]
_FONTS: tuple[str, str] | None = None


def _try_register(name: str, path: str) -> bool:
    if not path or not os.path.exists(path):
        return False
    try:
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
        return True
    except Exception:  # noqa: BLE001 —— 字体损坏/格式不支持就试下一个
        return False


def resolve_fonts() -> tuple[str, str]:
    """返回 (常规, 粗体) 字体名；找不到系统 TrueType 中文字体时回落到内置 CID 宋体。"""
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    candidates = list(_FONT_CANDIDATES)
    env_path = os.environ.get("SOLOBOOKS_PDF_FONT_PATH", "").strip()
    if env_path:
        candidates.insert(0, (env_path, os.environ.get("SOLOBOOKS_PDF_FONT_BOLD_PATH", "").strip() or None))
    for regular_path, bold_path in candidates:
        if not _try_register("SBook", regular_path):
            continue
        bold_name = "SBook"
        if bold_path and _try_register("SBookBold", bold_path):
            bold_name = "SBookBold"
        pdfmetrics.registerFontFamily("SBook", normal="SBook", bold=bold_name)
        _FONTS = ("SBook", bold_name)
        return _FONTS
    logger.warning("未找到系统 TrueType 中文字体，电子账簿 PDF 回落到内置 STSong-Light（不嵌入字体）")
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _FONTS = ("STSong-Light", "STSong-Light")
    return _FONTS


@dataclass
class PdfRenderResult:
    pdf_bytes: bytes
    page_count: int
    pdf_sha256: str
    outline_titles: list[str] = field(default_factory=list)


# ── 印刷体格式化（与 web/src/utils/ledgerFormat.ts 同口径） ─────────────────
def _fmt(value: Any) -> str:
    return f"{money(value if value not in (None, '') else 0):,.2f}"


def _amt(value: Any) -> str:
    """借贷列：0 留空。"""
    amount = money(value if value not in (None, "") else 0)
    return "" if amount == ZERO else f"{amount:,.2f}"


def _bal(value: Any) -> str:
    """余额列：不写负号，0 用破折号。"""
    amount = money(value if value not in (None, "") else 0)
    return "—" if amount == ZERO else f"{abs(amount):,.2f}"


def _dir(direction: Any) -> str:
    return {"debit": "借", "credit": "贷"}.get(str(direction or ""), "")


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").replace("\n", " ").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _qty(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.rstrip("0").rstrip(".") if "." in text else text


def _chunks(rows: list[Any], size: int) -> list[list[Any]]:
    if not rows:
        return [[]]
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _period_text(meta: dict[str, Any]) -> str:
    if meta.get("scope") == "year":
        return f"{meta.get('year')} 年度"
    month = str(meta.get("month") or "")
    year, _, rest = month.partition("-")
    return f"{year}年{rest}月" if rest else month


# ── 表格 ─────────────────────────────────────────────────────────────────────
_STYLE_CACHE: dict[tuple[str, str, float], ParagraphStyle] = {}


def _para_style(align: str, bold: bool, size: float) -> ParagraphStyle:
    key = (align, "b" if bold else "n", size)
    if key not in _STYLE_CACHE:
        regular, bold_font = resolve_fonts()
        _STYLE_CACHE[key] = ParagraphStyle(
            f"cell-{align}-{key[1]}-{size}",
            fontName=bold_font if bold else regular,
            fontSize=size,
            leading=size * 1.35,
            alignment={"L": TA_LEFT, "R": TA_RIGHT, "C": TA_CENTER}[align],
            wordWrap="CJK",  # 无空格中文也要能换行
        )
    return _STYLE_CACHE[key]


def _build_table(
    rows: list[list[str]],
    widths: list[float],
    aligns: str,
    size: float,
    bold_rows: set[int],
    shade_rows: set[int],
) -> Table:
    total = float(sum(widths))
    col_widths = [CONTENT_W * w / total for w in widths]
    data = []
    for r, row in enumerate(rows):
        bold = r == 0 or r in bold_rows
        data.append(
            [
                Paragraph(escape(str(cell)).replace("\n", "<br/>"), _para_style("C" if r == 0 else aligns[c], bold, size))
                for c, cell in enumerate(row)
            ]
        )
    table = Table(data, colWidths=col_widths)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#333333")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ECEEF1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for r in shade_rows:
        style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F5F6F8")))
    table.setStyle(TableStyle(style))
    return table


def _draw_table(
    c: Canvas,
    y_top: float,
    rows: list[list[str]],
    widths: list[float],
    aligns: str,
    *,
    bold_rows: set[int] | None = None,
    shade_rows: set[int] | None = None,
) -> float:
    """画表并返回表底 y。装不下时逐级缩字号，绝不压到页脚。"""
    avail = y_top - (BOTTOM + 8 * mm)
    table = None
    height = 0.0
    for size in (7.6, 6.8, 6.0, 5.4):
        table = _build_table(rows, widths, aligns, size, bold_rows or set(), shade_rows or set())
        _, height = table.wrapOn(c, CONTENT_W, avail)
        if height <= avail:
            break
    assert table is not None
    table.drawOn(c, LEFT, y_top - height)
    return y_top - height


# ── 页面框架 ─────────────────────────────────────────────────────────────────
@dataclass
class _Ctx:
    c: Canvas
    book: dict[str, Any]
    meta: dict[str, Any]
    company: str
    book_no: str
    content_hash: str
    status: str | None  # issued / void / None（预览）
    total_pages: int
    generated_by: str
    generated_at: str
    void_reason: str
    voided_at: str
    page_size: dict[str, int]
    page_no: int = 1
    outline: list[str] = field(default_factory=list)


def _footer(x: _Ctx, extra: str = "") -> None:
    c = x.c
    regular, _ = resolve_fonts()
    c.setStrokeColor(colors.HexColor("#999999"))
    c.setLineWidth(0.4)
    c.line(LEFT, BOTTOM, PAGE_W - RIGHT, BOTTOM)
    c.setFont(regular, 7.5)
    c.setFillColor(colors.HexColor("#555555"))
    left = (x.book_no or "预览件") + (f" · {extra}" if extra else "")
    c.drawString(LEFT, BOTTOM - 9, left)
    c.drawCentredString(LEFT + CONTENT_W / 2, BOTTOM - 9, _clip(x.company, 30))
    c.drawRightString(PAGE_W - RIGHT, BOTTOM - 9, f"第 {x.page_no} 页 共 {x.total_pages} 页")
    c.setFillColor(colors.black)


def _watermark(x: _Ctx) -> None:
    if x.status == "issued":
        return
    if x.status == "void":
        text, color = "已作废 · 仅存档查阅", colors.HexColor("#BD3D3D")
    else:
        text = "预览件 · 未结账" if x.meta.get("periodStatus") != "closed" else "预览件 · 未签发"
        color = colors.HexColor("#808080")
    _, bold = resolve_fonts()
    c = x.c
    c.saveState()
    c.setFillColor(color)
    c.setFillAlpha(0.13)
    c.setFont(bold, 54)
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(45)
    c.drawCentredString(0, 0, text)
    c.restoreState()


def _finish_page(x: _Ctx, extra: str = "") -> None:
    _watermark(x)
    _footer(x, extra)
    x.c.showPage()
    x.page_no += 1


def _bookmark(x: _Ctx, key: str, title: str, level: int = 0, closed: bool | None = None) -> None:
    x.c.bookmarkPage(key)
    x.c.addOutlineEntry(title, key, level=level, closed=closed)
    x.outline.append(title)


def _title(x: _Ctx, title: str, meta_line: str) -> float:
    regular, bold = resolve_fonts()
    c = x.c
    y = PAGE_H - TOP
    c.setFont(bold, 15)
    c.drawCentredString(LEFT + CONTENT_W / 2, y - 12, title)
    c.setFont(regular, 8)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawCentredString(LEFT + CONTENT_W / 2, y - 26, meta_line)
    c.setFillColor(colors.black)
    return y - 34


def _unit_line(x: _Ctx, suffix: str = "单位：元") -> str:
    return f"{x.company} · {_period_text(x.meta)} · {suffix}"


# ── 封面 / 目录 ──────────────────────────────────────────────────────────────
def _status_label(x: _Ctx) -> str:
    if x.status == "issued":
        return "正式账簿（已签发）"
    if x.status == "void":
        return "已作废（仅存档查阅）"
    return "预览件（未签发）"


def _draw_cover(x: _Ctx) -> None:
    regular, bold = resolve_fonts()
    c = x.c
    c.setStrokeColor(colors.HexColor("#333333"))
    c.setLineWidth(1.6)
    c.rect(LEFT - 4 * mm, BOTTOM + 6 * mm, CONTENT_W + 8 * mm, PAGE_H - TOP - BOTTOM - 6 * mm)
    c.setLineWidth(0.5)
    c.rect(LEFT - 2 * mm, BOTTOM + 8 * mm, CONTENT_W + 4 * mm, PAGE_H - TOP - BOTTOM - 10 * mm)
    mid = LEFT + CONTENT_W / 2
    c.setFont(regular, 15)
    c.drawCentredString(mid, PAGE_H - 70 * mm, _clip(x.company, 28))
    c.setFont(bold, 34)
    c.drawCentredString(mid, PAGE_H - 95 * mm, "电  子  账  簿")
    c.setFont(regular, 15)
    c.drawCentredString(mid, PAGE_H - 108 * mm, _period_text(x.meta))
    rows = [
        ("账套期间", _period_text(x.meta)),
        ("会计准则", str(x.meta.get("accountingStandard") or "")),
        ("凭证张数", f"{x.meta.get('entryCount', 0)} 张"),
        ("编制人", x.generated_by or "—"),
        ("制表日期", x.generated_at[:10] if x.generated_at else "—"),
        ("账簿编号", x.book_no or "（预览件，未签发）"),
        ("账簿状态", _status_label(x)),
        ("校验码", x.content_hash[:16] if x.content_hash else "—"),
    ]
    y = PAGE_H - 130 * mm
    for label, value in rows:
        c.setFont(regular, 9.5)
        c.setFillColor(colors.HexColor("#666666"))
        c.drawString(mid - 42 * mm, y, label)
        c.setFillColor(colors.black)
        c.setFont(bold, 10.5)
        c.drawString(mid - 18 * mm, y, _clip(value, 26))
        y -= 8.5 * mm
    if x.status == "void":
        c.setFillColor(colors.HexColor("#BD3D3D"))
        c.setFont(bold, 10)
        c.drawCentredString(mid, y - 4 * mm, f"作废原因：{_clip(x.void_reason, 30)}")
        c.drawCentredString(mid, y - 11 * mm, f"作废时间：{x.voided_at[:19].replace('T', ' ')}")
        c.setFillColor(colors.black)
    c.setFont(regular, 8)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawCentredString(mid, BOTTOM + 16 * mm, "本账簿由梭罗记账生成，含科目余额表、明细账、日记账与序时账")
    c.setFillColor(colors.black)
    _bookmark(x, "cover", "封面")
    _finish_page(x)


def _draw_toc_page(x: _Ctx, rows: list[tuple[str, str | None, str]], first: bool) -> None:
    """目录页：行高与字号按本页条目数收缩，保证与分页真值一致的页数内画完。"""
    regular, _ = resolve_fonts()
    c = x.c
    if first:
        _bookmark(x, "toc", "目录")
    y = _title(x, "目　录", _unit_line(x, "目录页码与页脚一致")) - 10
    bottom = BOTTOM + 8 * mm
    count = max(1, len(rows))
    row_h = min(6.2 * mm, (y - bottom) / count)
    size = min(10.0, max(6.0, row_h / mm * 2.2))
    for label, key, page_text in rows:
        label_text = _clip(label, 44)
        c.setFont(regular, size)
        c.drawString(LEFT + 4 * mm, y, label_text)
        c.drawRightString(PAGE_W - RIGHT - 4 * mm, y, page_text)
        width = pdfmetrics.stringWidth(label_text, regular, size)
        page_width = pdfmetrics.stringWidth(page_text, regular, size)
        dot_start = LEFT + 6 * mm + width
        dot_end = PAGE_W - RIGHT - 6 * mm - page_width
        if dot_end > dot_start + 4:
            c.setDash(1, 2)
            c.setStrokeColor(colors.HexColor("#AAAAAA"))
            c.line(dot_start, y + 2, dot_end, y + 2)
            c.setDash()
        if key:
            c.linkRect("", key, (LEFT, y - 3, PAGE_W - RIGHT, y + row_h - 1), relative=0, thickness=0)
        y -= row_h
    _finish_page(x)


# ── 账页 ─────────────────────────────────────────────────────────────────────
def _balance_hint(balance: dict[str, Any]) -> str:
    if not balance.get("isBalanced"):
        return "本期借方发生额与贷方发生额不相等，请先核对凭证。"
    if not balance.get("isOpeningBalanced"):
        return "期初余额借贷合计不相等，请检查期初。"
    if not balance.get("isClosingBalanced"):
        return "期末余额借贷合计不相等，请检查凭证。"
    return "期初、本期发生额、期末余额借贷合计均相等。"


def _draw_balance(x: _Ctx, chunk: list[dict[str, Any]], index: int, count: int, key: str) -> None:
    if index == 0:
        _bookmark(x, key, "科目余额表")
    y = _title(x, "科目余额表", _unit_line(x))
    head = ["科目编码", "科目名称", "期初方向", "期初余额", "借方发生", "贷方发生", "期末方向", "期末余额"]
    rows = [head]
    bold: set[int] = set()
    shade: set[int] = set()
    for item in chunk:
        name = _clip(item.get("accountName"), 26) + ("（子级小计）" if item.get("isParent") else "")
        rows.append(
            [
                str(item.get("accountCode") or ""), name, _dir(item.get("openingDirection")),
                _bal(item.get("openingBalance")), _amt(item.get("debitTotal")) or "—",
                _amt(item.get("creditTotal")) or "—", _dir(item.get("closingDirection")),
                _bal(item.get("closingBalance")),
            ]
        )
        if item.get("isParent"):
            bold.add(len(rows) - 1)
            shade.add(len(rows) - 1)
    if not chunk:
        rows.append(["", "本册期间没有可列示的科目余额", "", "", "", "", "", ""])
    balance = x.book["balance"]
    if index == count - 1 and chunk:
        rows.append(["", "合　计", "", "", _fmt(balance.get("totalDebit")), _fmt(balance.get("totalCredit")), "", ""])
        bold.add(len(rows) - 1)
        shade.add(len(rows) - 1)
    bottom = _draw_table(c=x.c, y_top=y, rows=rows, widths=[20, 42, 15, 22, 22, 22, 15, 22],
                         aligns="LLCRRRCR", bold_rows=bold, shade_rows=shade)
    if index == count - 1 and chunk:
        regular, _ = resolve_fonts()
        x.c.setFont(regular, 7.5)
        x.c.setFillColor(colors.HexColor("#555555"))
        x.c.drawString(LEFT, bottom - 10, _balance_hint(balance))
        x.c.setFillColor(colors.black)
    _finish_page(x)


def _draw_general(x: _Ctx, page: dict[str, Any], index: int, key: str) -> None:
    code = str(page.get("accountCode") or "")
    if index == 0:
        x.c.bookmarkPage(key)
        x.c.addOutlineEntry("总账", key, level=0, closed=True)
        x.outline.append("总账")
    sub = f"general:{code}"
    x.c.bookmarkPage(sub)
    x.c.addOutlineEntry(_clip(f"{code} {page.get('accountName')}", 30), sub, level=1)
    y = _title(x, "总　账", f"{code} {page.get('accountName')} · {x.company} · 单位：元")
    rows = [["月份", "借方发生", "贷方发生", "借或贷", "余额"]]
    rows.append(["年初余额", "", "", _dir(page.get("openingDirection")), _bal(page.get("opening"))])
    for m in page.get("months") or []:
        rows.append([m.get("month"), _amt(m.get("debit")), _amt(m.get("credit")),
                     _dir(m.get("balanceDirection")), _bal(m.get("balance"))])
    rows.append(["本年合计", _fmt(page.get("totalDebit")), _fmt(page.get("totalCredit")),
                 _dir(page.get("closingDirection")), _bal(page.get("closing"))])
    _draw_table(x.c, y, rows, [30, 40, 40, 22, 48], "LRRCR",
                bold_rows={len(rows) - 1}, shade_rows={1, len(rows) - 1})
    _finish_page(x, code)


def _group_bookmark(x: _Ctx, group_key: str, group_title: str, key: str, title: str, first_in_group: bool) -> None:
    if first_in_group:
        x.c.bookmarkPage(group_key)
        x.c.addOutlineEntry(group_title, group_key, level=0, closed=False)
        x.outline.append(group_title)
    x.c.bookmarkPage(key)
    x.c.addOutlineEntry(_clip(title, 34), key, level=1)
    x.outline.append(title)


def _summary_cell(item: dict[str, Any]) -> str:
    text = _clip(item.get("summary"), 42)
    if item.get("backfill"):
        text += " [补记]"
    if item.get("partnerName"):
        text += "\n往来：" + _clip(item.get("partnerName"), 20)
    return text


def _cp_cell(line: dict[str, Any]) -> str:
    names = [f"{o.get('code')} {o.get('name')}" for o in line.get("counterparties") or []]
    if len(names) <= 2:
        return _clip("、".join(names), 32)
    return _clip("、".join(names[:2]), 26) + f" 等 {len(names)} 项"


def _draw_detail(x: _Ctx, page: dict[str, Any], chunk: list[dict[str, Any]], index: int, count: int,
                 key: str, first_group: bool) -> None:
    code = str(page.get("code") or "")
    name = f"{code} {page.get('name')} 明细账"
    if index == 0:
        _group_bookmark(x, "grp-detail", "明细账", key, name, first_group)
    month = ""
    if x.meta.get("scope") == "year":
        first = next((ln.get("date") for ln in chunk if ln.get("date")), "")
        month = f"{str(first)[:7]} · " if first else ""
    y = _title(x, f"{month}{_clip(name, 40)}", _unit_line(x))
    qty = bool(page.get("hasQuantity"))
    if qty:
        head = ["日期", "凭证号", "摘要", "对方科目", "数量", "单价", "借方", "贷方", "借或贷", "余额"]
        widths, aligns = [17, 22, 31, 22, 13, 15, 16, 16, 11, 17], "LLLLRRRRCR"
    else:
        head = ["日期", "凭证号", "摘要", "对方科目", "借方", "贷方", "借或贷", "余额"]
        widths, aligns = [19, 25, 42, 28, 18, 18, 11, 19], "LLLLRRCR"

    def row(date, voucher, summary, cp, q, price, debit, credit, direction, balance):
        cells = [date, voucher, summary, cp] + ([q, price] if qty else []) + [debit, credit, direction, balance]
        return cells

    rows = [head]
    shade: set[int] = set()
    bold: set[int] = set()
    if index == 0:
        rows.append(row("", "", "期初余额", "", _qty(page.get("quantityOpening")), "", "", "",
                        _dir(page.get("openingDirection")), _bal(page.get("opening"))))
        shade.add(1)
    for line in chunk:
        rows.append(row(line.get("date") or "", _clip(line.get("voucherRef") or "—", 20), _summary_cell(line),
                        _cp_cell(line), _qty(line.get("quantity")),
                        _amt(line.get("unitPrice")) if line.get("unitPrice") else "",
                        _amt(line.get("debit")), _amt(line.get("credit")),
                        _dir(line.get("balanceDirection")), _bal(line.get("balance"))))
    if index == count - 1:
        totals = page.get("periodTotals") or {}
        years = page.get("yearTotals") or {}
        rows.append(row("", "", f"本期合计（{page.get('lineCount', len(page.get('lines') or []))} 笔）", "", "", "",
                        _fmt(totals.get("debit")), _fmt(totals.get("credit")),
                        _dir(page.get("closingDirection")), _bal(page.get("closing"))))
        rows.append(row("", "", "本年累计", "", "", "", _fmt(years.get("debit")), _fmt(years.get("credit")), "", ""))
        for r in (len(rows) - 2, len(rows) - 1):
            shade.add(r)
            bold.add(r)
    _draw_table(x.c, y, rows, widths, aligns, bold_rows=bold, shade_rows=shade)
    _finish_page(x, code)


def _draw_journal(x: _Ctx, journal: dict[str, Any], chunk: list[dict[str, Any]], index: int, count: int,
                  key: str, first_group: bool) -> None:
    title = str(journal.get("name") or "日记账")
    if index == 0:
        _group_bookmark(x, "grp-journal", "日记账", key, title, first_group)
    y = _title(x, title, _unit_line(x))
    rows = [["日期", "摘要", "凭证张数", "借方", "贷方", "借或贷", "余额"]]
    shade: set[int] = set()
    bold: set[int] = set()
    if index == 0:
        rows.append(["", "期初余额", "", "", "", _dir(journal.get("openingDirection")), _bal(journal.get("opening"))])
        shade.add(1)
    for day in chunk:
        rows.append([day.get("date"), _summary_cell(day), str(day.get("voucherCount", "")), _amt(day.get("debit")),
                     _amt(day.get("credit")), _dir(day.get("balanceDirection")), _bal(day.get("balance"))])
    if index == count - 1:
        rows.append(["", "本册合计", "", _fmt(journal.get("totalDebit")), _fmt(journal.get("totalCredit")),
                     _dir(journal.get("closingDirection")), _bal(journal.get("closing"))])
        shade.add(len(rows) - 1)
        bold.add(len(rows) - 1)
    _draw_table(x.c, y, rows, [22, 54, 18, 24, 24, 14, 24], "LLCRRCR", bold_rows=bold, shade_rows=shade)
    _finish_page(x, str(journal.get("code") or ""))


def _draw_chrono(x: _Ctx, chunk: list[dict[str, Any]], index: int, count: int, key: str) -> None:
    if index == 0:
        _bookmark(x, key, "序时账")
    chrono = x.book["chronological"]
    y = _title(x, "序时账", _unit_line(x, f"共 {chrono.get('voucherCount', 0)} 张凭证 · 单位：元"))
    rows = [["日期", "凭证号", "摘要", "会计科目", "借方", "贷方"]]
    for item in chunk:
        rows.append([item.get("date"), _clip(item.get("voucherRef") or "—", 20), _summary_cell(item),
                     _clip(f"{item.get('accountCode')} {item.get('accountName')}", 26),
                     _amt(item.get("debit")), _amt(item.get("credit"))])
    bold: set[int] = set()
    if index == count - 1:
        rows.append(["", "", "", "合　计", _fmt(chrono.get("totalDebit")), _fmt(chrono.get("totalCredit"))])
        bold.add(len(rows) - 1)
    _draw_table(x.c, y, rows, [20, 26, 52, 38, 22, 22], "LLLLRR", bold_rows=bold, shade_rows=bold)
    _finish_page(x)


_NOTES = [
    "本册按记账期间归集，不按业务发生日期；期内补记的凭证仍计入本册。",
    "明细账中标注「补记」的行，业务日期早于本册期间，金额计入本册所属期间。",
    "日记账与序时账按业务发生日期排列，便于与实际收付逐笔核对。",
    "金额单位为元、保留两位小数；余额不写负号，方向列示于「借或贷」栏。",
]


def _draw_signoff(x: _Ctx, key: str) -> None:
    regular, bold = resolve_fonts()
    c = x.c
    _bookmark(x, key, "编制说明与签章")
    y = _title(x, "编制说明", _unit_line(x, "编制说明与签章")) - 8
    c.setFont(regular, 9.5)
    for i, note in enumerate(_NOTES, 1):
        c.drawString(LEFT + 4 * mm, y, f"{i}. {note}")
        y -= 7 * mm
    y -= 12 * mm
    c.setFont(bold, 13)
    c.drawCentredString(LEFT + CONTENT_W / 2, y, "签　章")
    y -= 14 * mm
    c.setFont(regular, 10)
    for i, label in enumerate(("记账人：", "审核人：", "会计机构负责人：")):
        left = LEFT + 4 * mm + i * 58 * mm
        c.drawString(left, y, label)
        start = left + pdfmetrics.stringWidth(label, regular, 10) + 2
        c.line(start, y - 2, start + 20 * mm, y - 2)
    y -= 11 * mm
    c.drawString(LEFT + 4 * mm, y, f"制表人：{x.generated_by or '—'}")
    c.drawString(LEFT + 62 * mm, y, f"制表日期：{x.generated_at[:10] if x.generated_at else '—'}")
    c.drawString(LEFT + 120 * mm, y, f"账簿编号：{x.book_no or '—'}")
    y -= 9 * mm
    c.setFont(regular, 7.5)
    c.setFillColor(colors.HexColor("#555555"))
    if x.content_hash:
        c.drawString(LEFT + 4 * mm, y, f"校验码（SHA-256）：{x.content_hash}")
        y -= 6 * mm
    if x.status == "void":
        c.setFillColor(colors.HexColor("#BD3D3D"))
        c.drawString(LEFT + 4 * mm, y, f"已作废：{_clip(x.void_reason, 40)}（{x.voided_at[:19].replace('T', ' ')}）")
    c.setFillColor(colors.black)
    _finish_page(x)


def _split_into(rows: list[Any], pages: int) -> list[list[Any]]:
    """把目录行切成恰好 ``pages`` 页（不足补空页，保证页数与真值一致）。"""
    pages = max(1, pages)
    if pages == 1:
        return [rows]
    per = max(1, math.ceil(len(rows) / pages))
    chunks = [rows[i : i + per] for i in range(0, len(rows), per)] or [[]]
    while len(chunks) < pages:
        chunks.append([])
    return chunks[:pages]


def _plan_pages(
    book: dict[str, Any],
) -> tuple[dict[str, int], list[tuple[str, str | None, str]], int, int]:
    """章节起始页与目录条目直接取后端分页真值（``pagination.sections``）。

    目录条目与 ``web/src/pages/ledger/book.vue`` 同口径：余额表、总账、各明细账、
    日记账、序时账，末尾两条「另册」参考行；签章页只进 PDF 书签，不占目录行。
    旧版 payload 没有 ``pagination`` 时才回落到按行数估算。
    """
    pagination = book.get("pagination") or {}
    sections = pagination.get("sections") or []
    total_pages = int(pagination.get("totalPages") or 0)
    if sections and total_pages > 0:
        starts = {str(s.get("key")): int(s.get("pageNo") or 1) for s in sections}
        toc_section = next((s for s in sections if s.get("key") == "toc"), None)
        toc_pages = max(1, int((toc_section or {}).get("pageCount") or 1))
        toc_rows: list[tuple[str, str | None, str]] = [
            (str(s.get("title") or s.get("key")), str(s.get("key")), str(int(s.get("pageNo") or 1)))
            for s in sections
            if s.get("key") not in ("cover", "toc", "signoff")
        ]
        toc_rows.extend(
            [
                ("多栏账、核算项目三表", None, "账簿工作台"),
                ("研发支出辅助账、固定资产卡片", None, "另册"),
            ]
        )
        return starts, toc_rows, total_pages, toc_pages
    return _legacy_plan_pages(book)


def _legacy_plan_pages(
    book: dict[str, Any],
) -> tuple[dict[str, int], list[tuple[str, str | None, str]], int, int]:
    """兼容迁移前没有 pagination 的老 payload：按固定每页行数估算。"""
    raw_size = (book.get("pagination") or {}).get("pageSize") or {}
    ps = {k: int(raw_size.get(k) or v) for k, v in DEFAULT_PAGE_SIZE.items()}

    balance_chunks = _chunks(book.get("balance", {}).get("items") or [], ps["balance"])
    general_items = (book.get("general") or {}).get("items") or []
    details = book.get("details") or []
    journals = [j for j in (book.get("journals") or []) if j.get("rows")]
    chrono_items = (book.get("chronological") or {}).get("items") or []

    entries: list[tuple[str, str, int]] = [("科目余额表", "balance", len(balance_chunks))]
    if general_items:
        entries.append(("总账", "general", len(general_items)))
    for d in details:
        line_chunks = _chunks(d.get("lines") or [], ps["detail"])
        entries.append((f"{d['code']} {d['name']} 明细账", f"detail:{d['code']}", len(line_chunks)))
    for j in journals:
        j_chunks = _chunks(j.get("rows") or [], ps["journal"])
        entries.append((str(j.get("name") or "日记账"), f"journal:{j['kind']}", len(j_chunks)))
    if chrono_items:
        entries.append(("序时账", "chronological", len(_chunks(chrono_items, ps["chronological"]))))
    entries.append(("编制说明与签章", "signoff", 1))

    total_toc_lines = len(entries) + 2  # +2 条参考行
    toc_pages = max(1, math.ceil(total_toc_lines / TOC_ROWS_PER_PAGE))

    page_no = 2 + toc_pages
    starts: dict[str, int] = {"cover": 1, "toc": 2}
    toc_rows: list[tuple[str, str | None, str]] = []
    for label, key, pcount in entries:
        starts[key] = page_no
        if key != "signoff":
            toc_rows.append((label, key, str(page_no)))
        page_no += pcount

    toc_rows.extend(
        [
            ("多栏账、核算项目三表", None, "账簿工作台"),
            ("研发支出辅助账、固定资产卡片", None, "另册"),
        ]
    )
    return starts, toc_rows, page_no - 1, toc_pages


def render_ledger_book_pdf(
    book: dict[str, Any],
    *,
    snapshot_status: str | None = None,
    book_no: str | None = None,
    content_hash: str | None = None,
    generated_by: str | None = None,
    generated_at: str | None = None,
    void_reason: str | None = None,
    voided_at: str | None = None,
) -> PdfRenderResult:
    """将电子账簿权威 payload 渲染为连续 PDF（封面 + 可点击目录 + 书签 + 账页 + 签章）。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    meta = book.get("meta") or {}
    company = str(meta.get("companyName") or "电子账簿")
    resolved_book_no = str(book_no if book_no is not None else (meta.get("bookNo") or ""))
    resolved_hash = str(content_hash if content_hash is not None else (meta.get("contentHash") or ""))
    resolved_by = str(generated_by if generated_by is not None else (meta.get("generatedBy") or "制表人"))
    resolved_at = str(
        generated_at
        if generated_at is not None
        else (meta.get("generatedAt") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat())
    )

    raw_size = (book.get("pagination") or {}).get("pageSize") or {}
    ps = {k: int(raw_size.get(k) or v) for k, v in DEFAULT_PAGE_SIZE.items()}
    _, toc_rows, total_pages, toc_pages = _plan_pages(book)

    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(f"{company} 电子账簿（{_period_text(meta)}）")
    c.setAuthor(resolved_by or "梭罗记账 SoloBooks")
    c.setSubject(f"电子账簿 {resolved_book_no or _period_text(meta)}")
    c.setCreator("梭罗记账 SoloBooks")
    c.showOutline()

    x = _Ctx(
        c=c,
        book=book,
        meta=meta,
        company=company,
        book_no=resolved_book_no,
        content_hash=resolved_hash,
        status=snapshot_status,
        total_pages=total_pages,
        generated_by=resolved_by,
        generated_at=resolved_at,
        void_reason=str(void_reason or ""),
        voided_at=str(voided_at or ""),
        page_size=ps,
    )

    _draw_cover(x)
    for idx, toc_chunk in enumerate(_split_into(toc_rows, toc_pages)):
        _draw_toc_page(x, toc_chunk, first=(idx == 0))

    balance_chunks = _chunks(book.get("balance", {}).get("items") or [], ps["balance"])
    for idx, chunk in enumerate(balance_chunks):
        _draw_balance(x, chunk, idx, len(balance_chunks), "balance")

    general_items = (book.get("general") or {}).get("items") or []
    for idx, page in enumerate(general_items):
        _draw_general(x, page, idx, "general")

    details = book.get("details") or []
    for d_idx, page in enumerate(details):
        line_chunks = _chunks(page.get("lines") or [], ps["detail"])
        for idx, chunk in enumerate(line_chunks):
            _draw_detail(x, page, chunk, idx, len(line_chunks), f"detail:{page['code']}", first_group=(d_idx == 0))

    journals = [j for j in (book.get("journals") or []) if j.get("rows")]
    for j_idx, journal in enumerate(journals):
        j_chunks = _chunks(journal.get("rows") or [], ps["journal"])
        for idx, chunk in enumerate(j_chunks):
            _draw_journal(x, journal, chunk, idx, len(j_chunks), f"journal:{journal['kind']}", first_group=(j_idx == 0))

    chrono_items = (book.get("chronological") or {}).get("items") or []
    if chrono_items:
        c_chunks = _chunks(chrono_items, ps["chronological"])
        for idx, chunk in enumerate(c_chunks):
            _draw_chrono(x, chunk, idx, len(c_chunks), "chronological")

    _draw_signoff(x, "signoff")

    actual_pages = x.page_no - 1
    if actual_pages != total_pages:
        raise PdfPageCountMismatch(
            f"PDF 实际 {actual_pages} 页，与账簿分页真值 {total_pages} 页不一致；"
            "拒绝归档页码自相矛盾的账簿"
        )

    c.save()
    pdf_bytes = buf.getvalue()
    return PdfRenderResult(
        pdf_bytes=pdf_bytes,
        page_count=total_pages,
        pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        outline_titles=x.outline,
    )


# ── 异步渲染入口（线程池 + 并发上限） ────────────────────────────────────────
_PDF_SEMAPHORE: threading.BoundedSemaphore | None = None


def _pdf_semaphore() -> threading.BoundedSemaphore:
    global _PDF_SEMAPHORE
    if _PDF_SEMAPHORE is None:
        try:
            limit = int(os.environ.get("SOLOBOOKS_PDF_CONCURRENCY", "2"))
        except (TypeError, ValueError):
            limit = 2
        _PDF_SEMAPHORE = threading.BoundedSemaphore(max(1, limit))
    return _PDF_SEMAPHORE


async def render_ledger_book_pdf_async(book: dict[str, Any], **kwargs: Any) -> PdfRenderResult:
    """在线程池渲染，避免 CPU 密集的排版阻塞 asyncio 事件循环。

    并发上限由 ``SOLOBOOKS_PDF_CONCURRENCY``（默认 2）控制：超出的请求在
    工作线程里排队，而不是把整个 worker 卡死。
    """

    def _job() -> PdfRenderResult:
        with _pdf_semaphore():
            return render_ledger_book_pdf(book, **kwargs)

    return await anyio.to_thread.run_sync(_job)
