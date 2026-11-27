# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""凭证册 PDF 渲染（Phase 2 单据整理）：凭证封面 + 连续凭证 + 原件目录 + 签章。

- A4 **landscape**（记账凭证横版，与前端 VoucherSheet 版式同构）；
- 只消费 `build_voucher_book` 的权威 payload（不重算任何数），页数必须与
  ``pagination.totalPages`` 逐页一致否则拒绝出册（与账簿同一防线）；
- 复用 ledger_book_pdf_service 的渲染基建（字体/段落样式/格式化），不改动
  已签发账簿的渲染路径；横版页面框架在本模块内自持。
"""
from __future__ import annotations

import hashlib
import io
import os
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

import anyio.to_thread
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from services.ledger_book_pdf_service import (
    PdfPageCountMismatch,
    PdfRenderResult,
    _clip,
    _fmt,
    _para_style,
    _qty,
    resolve_fonts,
)

PAGE_W, PAGE_H = landscape(A4)
LEFT = 20 * mm  # 装订边（横版稍宽）
RIGHT = 12 * mm
TOP = 14 * mm
BOTTOM = 14 * mm
CONTENT_W = PAGE_W - LEFT - RIGHT
ORIGINAL_ROWS_PER_PAGE = 12  # 与 voucher_book_service._paginate 同一公式

_STATUS_COLORS = {"void": colors.HexColor("#BD3D3D")}


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
        text, color = "已作废 · 仅存档查阅", _STATUS_COLORS["void"]
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


def _bookmark(x: _Ctx, key: str, title: str, level: int = 0) -> None:
    x.c.bookmarkPage(key)
    x.c.addOutlineEntry(title, key, level=level)
    x.outline.append(title)


def _plain(value: Any) -> str:
    """凭证版式金额：千分位两位小数（与 VoucherSheet plainMoney 同口径）。"""
    try:
        amount = Decimal(str(value if value not in (None, "") else 0))
    except Exception:  # noqa: BLE001 —— 版式格式化不抛
        amount = Decimal("0")
    return f"{amount:,.2f}"


def _build_table_l(
    rows: list[list[str]],
    widths: list[float],
    aligns: str,
    size: float,
    bold_rows: set[int],
    shade_rows: set[int],
) -> Table:
    """横版表格（横版 CONTENT_W 版式，结构对齐账簿 _build_table）。"""
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


def _draw_table_l(
    c: Canvas,
    y_top: float,
    rows: list[list[str]],
    widths: list[float],
    aligns: str,
    *,
    bold_rows: set[int] | None = None,
    shade_rows: set[int] | None = None,
    min_size: float = 6.0,
) -> float:
    """横版画表并返回表底 y；装不下逐级缩字号。"""
    avail = y_top - (BOTTOM + 8 * mm)
    table = None
    height = 0.0
    for size in (9.0, 8.0, 7.0, min_size):
        table = _build_table_l(rows, widths, aligns, size, bold_rows or set(), shade_rows or set())
        _, height = table.wrapOn(c, CONTENT_W, avail)
        if height <= avail:
            break
    assert table is not None
    table.drawOn(c, LEFT, y_top - height)
    return y_top - height


# ── 封面（对标纸质「凭证封面」要素） ─────────────────────────────────────────
def _draw_cover(x: _Ctx) -> None:
    regular, bold = resolve_fonts()
    c = x.c
    meta = x.meta
    c.setStrokeColor(colors.HexColor("#333333"))
    c.setLineWidth(1.6)
    c.rect(LEFT - 4 * mm, BOTTOM + 6 * mm, CONTENT_W + 8 * mm, PAGE_H - TOP - BOTTOM - 6 * mm)
    c.setLineWidth(0.5)
    c.rect(LEFT - 2 * mm, BOTTOM + 8 * mm, CONTENT_W + 4 * mm, PAGE_H - TOP - BOTTOM - 10 * mm)
    mid = LEFT + CONTENT_W / 2
    c.setFont(regular, 15)
    c.drawCentredString(mid, PAGE_H - 52 * mm, _clip(x.company, 28))
    c.setFont(bold, 34)
    c.drawCentredString(mid, PAGE_H - 72 * mm, "凭  证  封  面")
    # 标题下划线（纸质凭证封面惯例，与主流财务软件版式一致）
    title_w = c.stringWidth("凭  证  封  面", bold, 34)
    c.setLineWidth(1.1)
    c.line(mid - title_w / 2, PAGE_H - 76 * mm, mid + title_w / 2, PAGE_H - 76 * mm)
    c.setFont(regular, 15)
    c.drawCentredString(mid, PAGE_H - 88 * mm, f"{meta.get('month', '')} 月度凭证册")

    def _date_cn(iso: Any) -> str:
        parts = str(iso or "").split("-")
        if len(parts) == 3:
            return f"{parts[0]} 年 {int(parts[1])} 月 {int(parts[2])} 日"
        return str(iso or "")

    rows: list[tuple[str, str]] = [
        ("立 档 单 位", x.company),
        ("凭 证 类 别", "记账凭证"),
        ("起止日期", f"自 {_date_cn(meta.get('periodStart'))} 至 {_date_cn(meta.get('periodEnd'))}"),
        ("凭 证 册 数", "本月共 1 册　本册是第 1 册"),
        (
            "凭 证 号 数",
            f"本册共 {meta.get('voucherCount', 0)} 号  本册自第 {meta.get('voucherRange', {}).get('first', '—')} 号至第 {meta.get('voucherRange', {}).get('last', '—')} 号",
        ),
        (
            "附　　　注",
            f"原件 {meta.get('attachmentTotal', 0)} 份（其中 XML 原件 {meta.get('xmlOriginalTotal', 0)} 份），目录见册内",
        ),
        # 档案四格（全宗号/目录号/案卷号/保管号）：留空由归档时填写
        (
            "全 宗 号 / 目 录 号 / 案 卷 号 / 保 管 号",
            "＿＿＿＿＿ / ＿＿＿＿＿ / ＿＿＿＿＿ / ＿＿＿＿＿",
        ),
    ]
    # 版式预算（landscape A4，高 210mm，框底在 BOTTOM+6mm≈20mm）：
    # 标题区到 122mm，7 行 × 9.6mm = 67.2mm → 表格底 ≈ 42mm，
    # 签线四项 ≈ 34mm，校验码+保管依据 ≈ 24mm，全部留在框内不压线。
    y = PAGE_H - 98 * mm
    row_h = 9.6 * mm
    box_l, box_r = LEFT + 14 * mm, PAGE_W - RIGHT - 14 * mm
    label_w = 52 * mm
    c.setStrokeColor(colors.HexColor("#333333"))
    for label, value in rows:
        c.setLineWidth(0.6)
        c.rect(box_l, y - row_h + 2.5 * mm, box_r - box_l, row_h)
        c.line(box_l + label_w, y - row_h + 2.5 * mm, box_l + label_w, y + 2.5 * mm)
        c.setFont(regular, 10)
        c.drawString(box_l + 4 * mm, y - row_h / 2 - 1 * mm, label)
        c.setFont(regular, 10.5)
        c.drawString(box_l + label_w + 4 * mm, y - row_h / 2 - 1 * mm, _clip(value, 46))
        y -= row_h

    # 底部签线四项（纸质封面惯例：财务主管 / 会计 / 装订人 / 保管年限）；
    # 保管年限电子册直接填 30 年（会计凭证最低期限，财政部 国家档案局令第 79 号）
    sign_y = y - 8 * mm
    c.setFont(regular, 10)
    seg = (box_r - box_l) / 4
    for i, label in enumerate(("财务主管", "会计", "装订人", "保管年限：30 年")):
        sx = box_l + seg * i
        c.drawString(sx, sign_y, f"{label}：＿＿＿＿＿" if not label.endswith("30 年") else label)
    c.setFont(regular, 7.5)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawCentredString(
        mid,
        sign_y - 6 * mm,
        f"保管依据：财政部 国家档案局令第 79 号（会计凭证 30 年）  ·  校验码 {x.content_hash[:16]}  ·  册号 {x.book_no or '预览'}",
    )
    c.setFillColor(colors.black)


# ── 连续凭证页（版式对齐 VoucherSheet.vue） ──────────────────────────────────
def _draw_voucher_page(x: _Ctx, voucher: dict[str, Any], index: int) -> None:
    regular, bold = resolve_fonts()
    c = x.c
    y = PAGE_H - TOP
    c.setFont(regular, 9)
    c.drawCentredString(LEFT + CONTENT_W / 2, y - 7, x.company)
    c.setFont(bold, 16)
    c.drawCentredString(LEFT + CONTENT_W / 2, y - 24, "记  账  凭  证")
    c.setFont(regular, 9)
    # meta 行：左「附单据 X 张」/ 中 日期 / 右 记字第X号（与 VoucherSheet 一致）
    date_parts = str(voucher.get("date") or "").split("-")
    date_cn = f"{date_parts[0]}年{int(date_parts[1])}月{int(date_parts[2])}日" if len(date_parts) == 3 else voucher.get("date", "")
    meta_y = y - 37
    c.drawString(LEFT, meta_y, f"附单据 {voucher.get('attachmentCount', 0)} 张")
    c.drawCentredString(LEFT + CONTENT_W / 2, meta_y, date_cn)
    c.drawRightString(PAGE_W - RIGHT, meta_y, f"记字第{voucher.get('voucherSeq', '—')}号")
    if voucher.get("backfill"):
        c.setFont(regular, 7.5)
        c.setFillColor(colors.HexColor("#A05C10"))
        c.drawRightString(PAGE_W - RIGHT, meta_y - 9, "补记凭证（业务日期在前）")
        c.setFillColor(colors.black)

    lines = voucher.get("lines", [])
    rows: list[list[str]] = [["摘要", "科目", "借方金额", "贷方金额"]]
    for line in lines:
        account_cell = str(line.get("name") or "")
        if line.get("code"):
            account_cell += f"\n{line.get('code')}"
        if line.get("auxiliaryText"):
            account_cell += f"\n辅助：{line.get('auxiliaryText')}"
        rows.append(
            [
                str(line.get("summary") or ""),
                account_cell,
                _plain(line.get("amount")) if line.get("side") == "debit" else "",
                _plain(line.get("amount")) if line.get("side") == "credit" else "",
            ]
        )
    for _ in range(voucher.get("blankRows", 0)):
        rows.append(["", "", "", ""])
    rows.append(
        [
            f"合　计：{voucher.get('capitalAmount', '')}",
            "",
            _plain(voucher.get("debitTotal")),
            _plain(voucher.get("creditTotal")),
        ]
    )
    bold_rows = {len(rows) - 1}
    table_bottom = _draw_table_l(
        c,
        meta_y - 6,
        rows,
        widths=[3.2, 4.2, 2.2, 2.2],
        aligns="LCRR",
        bold_rows=bold_rows,
        shade_rows={len(rows) - 1},
    )

    # 签线区
    sign_y = max(table_bottom - 10 * mm, BOTTOM + 12 * mm)
    c.setFont(regular, 9.5)
    seg = CONTENT_W / 3
    labels = ("财务主管：＿＿＿＿＿＿", "复核：＿＿＿＿＿＿", "记账：＿＿＿＿＿＿")
    for i, label in enumerate(labels):
        c.drawString(LEFT + seg * i, sign_y, label)
    if lines:
        c.setFont(regular, 7)
        c.setFillColor(colors.HexColor("#666666"))
        qty_notes = [
            f"×{_qty(line.get('quantity'))}{line.get('unit') or ''}@{_plain(line.get('unitPrice'))}"
            for line in lines
            if line.get("quantity")
        ]
        if qty_notes:
            c.drawString(LEFT, sign_y - 6 * mm, "数量金额：" + "；".join(qty_notes[:6]))
        c.setFillColor(colors.black)


# ── 原件归档目录 ─────────────────────────────────────────────────────────────
_TYPE_LABELS = {
    "einvoice_xml": "XML 原件",
    "einvoice_pdf": "PDF 版式",
    "einvoice_ofd": "OFD 版式",
    "bank_receipt": "银行回单",
    "bank_receipt_batch": "回单合集",
    "bank_statement_pdf": "银行对账单",
    "payment_screenshot": "支付截图",
    "reimbursement_form": "报销单",
    "payroll_sheet": "工资表",
    "contract": "合同",
    "other": "其他",
}


def _draw_originals_pages(x: _Ctx, originals: list[dict[str, Any]]) -> None:
    regular, bold = resolve_fonts()
    c = x.c
    chunks = [originals[i : i + ORIGINAL_ROWS_PER_PAGE] for i in range(0, len(originals), ORIGINAL_ROWS_PER_PAGE)]
    if not chunks:
        chunks = [[]]
    for page_index, chunk in enumerate(chunks):
        y = PAGE_H - TOP
        c.setFont(regular, 9)
        c.drawCentredString(LEFT + CONTENT_W / 2, y - 7, x.company)
        c.setFont(bold, 15)
        c.drawCentredString(LEFT + CONTENT_W / 2, y - 23, "原件归档目录")
        c.setFont(regular, 8)
        meta = x.meta
        c.drawCentredString(
            LEFT + CONTENT_W / 2,
            y - 33,
            f"{meta.get('month', '')} · 本册原件 {meta.get('attachmentTotal', 0)} 份（XML 原件 {meta.get('xmlOriginalTotal', 0)} 份）· 可在系统内逐件校验下载",
        )
        rows: list[list[str]] = [["序号", "凭证号", "发票号", "原件文件名", "类型", "sha256 前16", "大小"]]
        for seq, item in enumerate(chunk, start=page_index * ORIGINAL_ROWS_PER_PAGE + 1):
            rows.append(
                [
                    str(seq),
                    str(item.get("voucherNo") or "—"),
                    str(item.get("invoiceNumber") or "—"),
                    _clip(item.get("fileName"), 40),
                    _TYPE_LABELS.get(str(item.get("documentType")), "其他"),
                    str(item.get("contentSha256") or "")[:16],
                    f"{(item.get('sizeBytes') or 0) / 1024:.1f}K",
                ]
            )
        if not chunk:
            rows.append(["—", "—", "—", "本册没有已归档原件", "—", "—", "—"])
        _draw_table_l(
            c,
            y - 38,
            rows,
            widths=[0.7, 1.5, 1.9, 3.6, 1.1, 1.6, 0.8],
            aligns="CLLLCCC",
        )
        if page_index == 0:
            _bookmark(x, "originals", "原件归档目录")
        _finish_page(x, "原件归档目录")


# ── 发票复现页（方案 A：按官方 PDF 测量坐标复原，非原件） ────────────────────
MAX_INVOICE_PAGE_ITEMS = 12
# 官方数电票 PDF 实测：595.28×396.85pt（210×140mm），主色 #800000，正文 9pt，
# 标题楷体 20.05pt，明细表无网格线（仅块级横线），购销信息带竖排正立字。
OF_W, OF_H = 595.28, 396.85
_INVOICE_RED = colors.HexColor("#800000")
_INVOICE_GRAY = colors.HexColor("#5B6470")
_KAI_FONT: str | None = None


def _kai_font() -> str:
    """标题楷体（官方用楷体；找不到就回落粗体，保证不崩）。"""
    global _KAI_FONT
    if _KAI_FONT is not None:
        return _KAI_FONT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path in (
        "C:/Windows/Fonts/simkai.ttf",
        "/System/Library/Fonts/Supplemental/Kaiti.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
    ):
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont("SKai", path, subfontIndex=0))
            _KAI_FONT = "SKai"
            return _KAI_FONT
        except Exception:  # noqa: BLE001 —— 字体损坏就试下一个
            continue
    _KAI_FONT = resolve_fonts()[1]
    return _KAI_FONT


def _vertical_label(c: Canvas, x: float, y_top: float, text: str, font: str, size: float, step: float) -> None:
    """竖排正立字（官方「购买方信息」窄带：字正立、纵向堆叠，实测 9.9pt 行距）。"""
    for index, char in enumerate(str(text or "")):
        c.drawCentredString(x, y_top - index * step, char)


def _wrap_width(text: Any, font: str, size: float, width: float) -> list[str]:
    """按渲染宽度折行（中文按字符切；官方项目名称最长行宽实测约 100pt）。"""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    value = str(text or "")
    if not value:
        return []
    lines: list[str] = []
    current = ""
    for char in value:
        candidate = current + char
        if stringWidth(candidate, font, size) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def _draw_einvoice_page(x: _Ctx, voucher: dict[str, Any], attachment: dict[str, Any]) -> None:
    """数电发票复现页：全部坐标/字号/颜色取自官方 PDF 实测（1:1 映射后整体缩放）。

    官方版式要点（实测）：外框与横线 #800000、线宽 0.71；明细区**无竖线无行线**；
    购销信息为竖排正立字窄带；标题楷体 20pt 下方双横线；价税合计行含 ⊗ 符号与
    （小写）¥；备注区竖排「备/注」；开票人在框外左下。
    不复刻税局电子签章、二维码（XML 无这些图像数据，伪造即事故），二维码位置
    用占位框；页脚保留强制复现声明。
    """
    regular, bold = resolve_fonts()
    c = x.c
    parsed = attachment.get("parsed") or {}
    items = parsed.get("items") or []
    amounts = parsed.get("amounts") or {}
    buyer = parsed.get("buyer") or {}
    seller = parsed.get("seller") or {}
    specific = parsed.get("specific") if isinstance(parsed.get("specific"), dict) else {}
    agent = (specific.get("AgentIssuingInvoice") or {}).get("fields", {}) if specific else {}

    # ---- 1:1 映射：官方 210×140mm 版式整体放大到册内 250mm 宽，居中 ----
    block_w = 250 * mm
    s = block_w / OF_W
    block_h = OF_H * s
    ox = (PAGE_W - block_w) / 2
    oy_top = (PAGE_H + block_h) / 2

    def X(px: float) -> float:
        return ox + px * s

    def Y(py: float) -> float:
        return oy_top - py * s

    def S(size: float) -> float:
        return size * s

    def base(bbox_top: float, size: float) -> float:
        """由 pymupdf 文本 bbox 顶部推 reportlab 基线（实测下行距约 0.97em）。"""
        return Y(bbox_top + size * 0.97)

    kai = _kai_font()
    c.setStrokeColor(_INVOICE_RED)
    c.setLineWidth(0.71 * s)

    # ---- 二维码占位（官方 QR 位于 18.4,17.0–75.1,73.7；不伪造码图） ----
    qx0, qy0, qx1, qy1 = 18.42, 17.01, 75.12, 73.71
    c.setDash(2, 2)
    c.setStrokeColor(_INVOICE_GRAY)
    c.rect(X(qx0), Y(qy1), (qx1 - qx0) * s, (qy1 - qy0) * s)
    c.setDash()
    c.setFont(regular, S(7))
    c.setFillColor(_INVOICE_GRAY)
    c.drawCentredString(X((qx0 + qx1) / 2), Y((qy0 + qy1) / 2), "二维码见原件")
    c.setFillColor(colors.black)
    c.setStrokeColor(_INVOICE_RED)

    # ---- 票头：楷体标题 + 双横线 + 右置票号/日期（坐标实测） ----
    c.setFillColor(_INVOICE_RED)
    c.setFont(kai, S(20.05))
    kind = str(parsed.get("generalOrSpecial") or "普通发票")
    c.drawCentredString(X(293.35), base(22.7, 20.05), f"电子发票（{kind}）")
    c.setLineWidth(0.71 * s)
    for rule_y in (51.0, 53.9):
        c.line(X(185.7), Y(rule_y), X(392.6), Y(rule_y))
    if not parsed.get("isBlue", True):
        c.setFillColor(colors.black)
        c.setFont(bold, S(9))
        c.drawString(X(163), Y(49.9), "红字发票")
        c.setFillColor(_INVOICE_RED)

    c.setFont(regular, S(9.35))
    c.drawString(X(438.0), base(31.7, 9.35), "发票号码：")
    c.drawString(X(438.0), base(48.7, 9.35), "开票日期：")
    issue_parts = str(parsed.get("issueTime") or "").split("-")
    issue_cn = (
        f"{issue_parts[0]}年{int(issue_parts[1])}月{int(issue_parts[2])}日"
        if len(issue_parts) == 3
        else str(parsed.get("issueTime") or "—")
    )
    c.setFillColor(colors.black)
    c.setFont(regular, S(9))
    c.drawString(X(481.9), base(31.8, 9), str(parsed.get("invoiceNumber") or "—"))
    c.drawString(X(481.9), base(49.1, 9), issue_cn)
    if agent.get("AgentIssuingName") or "AgentIssuingInvoice" in specific:
        c.drawString(X(104.4), base(40.9, 9), "税务代开")

    # ---- 表体框线（仅官方实测存在的线：无网格、无列线） ----
    c.setStrokeColor(_INVOICE_RED)
    c.setLineWidth(0.71 * s)
    table_l, table_r, table_t, table_b = 12.8, 582.5, 85.0, 351.5
    for yline in (85.0, 147.4, 272.1, 294.8, 351.5):
        c.line(X(table_l), Y(yline), X(table_r), Y(yline))
    for xline, y_from, y_to in (
        (12.8, 85.0, 351.5),
        (582.5, 85.0, 351.5),
        (30.3, 85.0, 147.4),   # 购买方竖排标签带
        (298.2, 85.0, 147.4),  # 购销分隔
        (315.2, 85.0, 147.4),  # 销售方竖排标签带
        (30.3, 294.8, 351.5),  # 备注竖排标签带
        (160.2, 272.1, 294.8),  # 价税合计大写左分隔
    ):
        c.line(X(xline), Y(y_from), X(xline), Y(y_to))

    # ---- 购销信息（竖排正立字 + 名称/税号） ----
    c.setFillColor(_INVOICE_RED)
    c.setFont(regular, S(8.49))
    _vertical_label(c, X(20.9), base(93.4, 8.49), "购买方信息", regular, S(8.49), 9.9 * s)
    _vertical_label(c, X(305.8), base(93.4, 8.49), "销售方信息", regular, S(8.49), 9.9 * s)
    c.setFont(regular, S(9))
    c.drawString(X(32.7), base(95.8, 9), "名称：")
    c.drawString(X(317.6), base(95.8, 9), "名称：")
    c.setFont(regular, S(8.49))
    c.drawString(X(32.5), base(126.0, 8.49), "统一社会信用代码/纳税人识别号：")
    c.drawString(X(317.4), base(126.0, 8.49), "统一社会信用代码/纳税人识别号：")
    c.setFillColor(colors.black)
    c.setFont(regular, S(9))
    c.drawString(X(56.7), base(95.8, 9), _clip(buyer.get("name"), 20))
    c.drawString(X(340.2), base(95.8, 9), _clip(seller.get("name"), 20))
    # 税号用 Courier（官方实测 CourierNew 12pt）
    c.setFont("Courier", S(12))
    c.drawString(X(156.9), base(123.5, 12), _clip(str(buyer.get("taxId") or "").upper(), 20))
    c.drawString(X(441.7), base(123.5, 12), _clip(str(seller.get("taxId") or "").upper(), 20))

    # ---- 明细表（列位置按官方实测中心/右边界，无网格线） ----
    c.setFillColor(_INVOICE_RED)
    c.setFont(regular, S(9))
    header_baseline = base(151.1, 9)
    for label, center in (
        ("项目名称", 63.4),
        ("规格型号", 137.4),
        ("单 位", 203.5),
        ("数 量", 277.2),
        ("单 价", 348.1),
        ("金 额", 420.3),
        ("税率/征收率", 471.3),
        ("税 额", 564.9),
    ):
        c.drawCentredString(X(center), header_baseline, label)
    c.setFillColor(colors.black)
    c.setFont(regular, S(9))
    row_baseline = base(160.2, 9)
    line_step = 12.8 * s  # 官方行内换行 12.8pt
    shown = items[:MAX_INVOICE_PAGE_ITEMS]
    y_cursor = row_baseline
    for item in shown:
        name_lines = _wrap_width(item.get("itemName"), regular, S(9), 99 * s) or [""]
        for line_index, line_text in enumerate(name_lines):
            c.drawString(X(12.8), y_cursor - line_index * line_step, line_text)
        unit = str(item.get("unit") or "")
        qty = _qty(item.get("quantity"))
        unit_price = str(item.get("unitPrice") or "")
        amount = str(item.get("amount") or "")
        tax_rate = str(item.get("taxRate") or "")
        tax_amount = str(item.get("taxAmount") or "")
        if not tax_amount:
            tax_amount = "***"
        c.drawCentredString(X(203.5), y_cursor, _clip(unit, 4))
        c.drawCentredString(X(277.2), y_cursor, qty)
        c.drawCentredString(X(348.1), y_cursor, unit_price)
        c.drawRightString(X(433.7), y_cursor, amount)
        c.drawCentredString(X(471.3), y_cursor, tax_rate)
        c.drawRightString(X(582.5), y_cursor, tax_amount)
        y_cursor -= line_step * max(1, len(name_lines))
    if not shown:
        c.drawString(X(12.8), y_cursor, "（XML 解析结果无明细行）")
    if len(items) > len(shown):
        c.setFont(regular, S(8))
        c.setFillColor(_INVOICE_GRAY)
        c.drawString(X(12.8), y_cursor, f"另有 {len(items) - len(shown)} 行完整明细见原件")
        c.setFillColor(colors.black)
        c.setFont(regular, S(9))

    # ---- 合计行（官方：合/计 分列 + ¥金额 + 税额右对齐） ----
    c.setFillColor(_INVOICE_RED)
    c.setFont(regular, S(9))
    c.drawCentredString(X(62.7), base(261.6, 9), "合")
    c.drawCentredString(X(108.0), base(261.6, 9), "计")
    c.setFillColor(colors.black)
    c.setFont("Courier", S(11))
    c.drawString(X(401.5), base(259.1, 11), "¥")
    c.setFont(regular, S(9))
    c.drawString(X(408.1), base(260.5, 9), str(amounts.get("excl") or ""))
    all_masked = bool(items) and all(not str(item.get("taxAmount") or "").strip() for item in items)
    if all_masked:
        c.drawRightString(X(582.5), base(260.5, 9), "***")
    else:
        # 官方应税票：税额合计带 ¥（Courier 11pt 在 553.4，金额 560.0）
        c.setFont("Courier", S(11))
        c.drawString(X(553.4), base(259.1, 11), "¥")
        c.setFont(regular, S(9))
        c.drawString(X(560.0), base(260.5, 9), _fmt(amounts.get("tax")))

    # ---- 价税合计行（⊗ 矢量绘制 + 大写 + （小写）¥） ----
    c.setFillColor(_INVOICE_RED)
    c.setFont(regular, S(9))
    c.drawString(X(48.0), base(280.0, 9), "价税合计（大写）")
    c.drawString(X(406.8), base(280.0, 9), "（小写）")
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.64 * s)
    mark_cx, mark_cy, mark_r = X(171.3), Y(282.0), 4.6 * s
    c.circle(mark_cx, mark_cy, mark_r)
    c.line(mark_cx - mark_r * 0.7, mark_cy - mark_r * 0.7, mark_cx + mark_r * 0.7, mark_cy + mark_r * 0.7)
    c.line(mark_cx - mark_r * 0.7, mark_cy + mark_r * 0.7, mark_cx + mark_r * 0.7, mark_cy - mark_r * 0.7)
    c.setFillColor(colors.black)
    c.setFont(regular, S(9))
    c.drawString(X(178.4), base(278.1, 9), str(amounts.get("inclChinese") or "—"))
    c.setFont("Courier", S(11))
    c.drawString(X(440.8), base(277.1, 11), "¥")
    c.setFont(regular, S(11))
    c.drawString(X(447.4), base(276.8, 11), _fmt(amounts.get("incl")))

    # ---- 备注区（竖排「备/注」+ 两行原文，官方行距 9.4pt） ----
    c.setFillColor(_INVOICE_RED)
    c.setFont(regular, S(9))
    c.drawCentredString(X(21.4), base(309.8, 9), "备")
    c.drawCentredString(X(21.4), base(326.8, 9), "注")
    c.setFillColor(colors.black)
    text_lines: list[str] = []
    if agent.get("AgentIssuingName"):
        text_lines.append(
            f"代开发票税务机构代码:{agent.get('AgentIssuingIdCode', '')};    代开发票税务机构名称:{agent.get('AgentIssuingName', '')}"
        )
    text_lines.extend(_wrap_width(parsed.get("remark"), regular, S(9), (582.5 - 31.5) * s))
    c.setFont(regular, S(9))
    for index, line in enumerate(text_lines[:4]):
        c.drawString(X(31.5), base(296.3 + index * 9.4, 9), line)

    # ---- 开票人（官方在框外左下 55.3,367.9） ----
    c.setFillColor(_INVOICE_RED)
    c.setFont(regular, S(9))
    c.drawString(X(55.3), base(367.9, 9), "开票人：")
    c.setFillColor(colors.black)
    c.drawString(X(90.7), base(368.2, 9), _clip(parsed.get("drawer"), 20))

    # ---- 右缘竖排「复现件 · 非原件」（官方该位置为下载次数字样；我们用它做隔离标识） ----
    c.saveState()
    c.setFillColor(_INVOICE_GRAY)
    c.setFillAlpha(0.55)
    c.setFont(regular, S(7.5))
    c.translate(X(589.0), Y(115))
    c.rotate(90)
    c.drawCentredString(0, 0, "复现件 · 非原件")
    c.restoreState()

    # ---- 页脚强制复现声明（框外下方） ----
    c.setFont(regular, S(6.6))
    c.setFillColor(_INVOICE_GRAY)
    sha16 = str(attachment.get("contentSha256") or "")[:16]
    sig_note = "· 原件含税务机关数字签名 " if parsed.get("hasTaxBureauSignature") else ""
    c.drawString(
        ox,
        Y(OF_H) - 4.2 * mm,
        f"本页由 XML 数据原件渲染，非原始凭证 · 原件 sha256 {sha16} {sig_note}· 可在系统查验下载 · 文件 {_clip(attachment.get('fileName'), 40)}",
    )
    c.setFillColor(colors.black)


def _draw_image_attachment_page(x: _Ctx, voucher: dict[str, Any], attachment: dict[str, Any]) -> None:
    """图片型原始凭证页（银行电子回单 / 支付截图 / 报销单 / 工资表等）。"""
    from pathlib import Path
    from reportlab.lib.utils import ImageReader
    from services.attachment_storage_service import ATTACHMENTS_DIR

    regular, bold = resolve_fonts()
    c = x.c
    doc_type = str(attachment.get("documentType") or "other")
    type_label = _TYPE_LABELS.get(doc_type, "原始凭证")
    voucher_no = str(voucher.get("voucherNo") or "未编号")
    parsed_meta = attachment.get("parsedMeta") if isinstance(attachment.get("parsedMeta"), dict) else {}

    top_y = PAGE_H - TOP
    c.setFont(bold, 12.5)
    c.setFillColor(colors.HexColor("#1E293B"))
    c.drawString(LEFT, top_y - 12, f"{type_label} · 关联凭证 {voucher_no}")

    sub_parts = [str(attachment.get("fileName") or "")]
    if parsed_meta.get("receiptNo"):
        sub_parts.append(f"回单号 {parsed_meta['receiptNo']}")
    if parsed_meta.get("txDate"):
        sub_parts.append(str(parsed_meta["txDate"]))
    if parsed_meta.get("amount"):
        sub_parts.append(f"金额 ¥{parsed_meta['amount']}")
    if parsed_meta.get("summary"):
        sub_parts.append(_clip(parsed_meta["summary"], 36))
    c.setFont(regular, 8.5)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawString(LEFT, top_y - 25, _clip(" · ".join([p for p in sub_parts if p]), 105))

    box_bottom = BOTTOM + 8 * mm
    box_top = top_y - 32
    avail_w = CONTENT_W
    avail_h = max(60.0, box_top - box_bottom)

    drawn = False
    stored_rel = str(attachment.get("storedPath") or "").strip()
    if stored_rel:
        base_dir = Path(ATTACHMENTS_DIR).resolve()
        candidate = (base_dir / stored_rel).resolve()
        if candidate.is_relative_to(base_dir) and candidate.is_file():
            try:
                reader = ImageReader(str(candidate))
                iw, ih = reader.getSize()
                if iw > 0 and ih > 0:
                    scale = min(avail_w / float(iw), avail_h / float(ih))
                    draw_w = float(iw) * scale
                    draw_h = float(ih) * scale
                    dx = LEFT + (avail_w - draw_w) / 2
                    dy = box_bottom + (avail_h - draw_h) / 2
                    c.setStrokeColor(colors.HexColor("#D0D7DE"))
                    c.setLineWidth(0.6)
                    c.rect(dx, dy, draw_w, draw_h)
                    c.drawImage(reader, dx, dy, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")
                    drawn = True
            except Exception:
                drawn = False

    if not drawn:
        c.saveState()
        c.setDash(3, 3)
        c.setStrokeColor(colors.HexColor("#CBD5E1"))
        c.rect(LEFT, box_bottom, avail_w, avail_h)
        c.restoreState()
        c.setFont(regular, 10)
        c.setFillColor(colors.HexColor("#64748B"))
        c.drawCentredString(
            LEFT + avail_w / 2,
            box_bottom + avail_h / 2,
            "原件图像文件暂未就绪，请在系统「原始凭证 / 附件」中校验下载",
        )

    c.setFont(regular, 7.2)
    c.setFillColor(colors.HexColor("#64748B"))
    sha16 = str(attachment.get("contentSha256") or "")[:16]
    c.drawString(
        LEFT,
        BOTTOM + 3 * mm,
        f"原始凭证归档件 · 原件 sha256 {sha16} · 文件 {_clip(attachment.get('fileName'), 48)}",
    )
    c.setFillColor(colors.black)


# ── 装订签章页 ────────────────────────────────────────────────────────────────
def _draw_signoff(x: _Ctx) -> None:
    regular, bold = resolve_fonts()
    c = x.c
    meta = x.meta
    y = PAGE_H - TOP
    c.setFont(bold, 15)
    c.drawCentredString(LEFT + CONTENT_W / 2, y - 14, "装 订 签 章")
    c.setFont(regular, 9.5)
    notes = [
        f"本册装订 {meta.get('voucherCount', 0)} 张已过账记账凭证（{meta.get('month', '')}，含红冲凭证），借贷合计 {_fmt(meta.get('debitTotal'))} 元。",
        f"随册归档原件 {meta.get('attachmentTotal', 0)} 份（XML 原件 {meta.get('xmlOriginalTotal', 0)} 份），清单见「原件归档目录」；原件为含税务机关数字签名的电子文件，",
        "按财会〔2020〕6号可仅以电子形式归档，保管期限 30 年；系统内可逐件下载并经 sha256 校验。",
    ]
    missing = x.book.get("originalsMissing", [])
    if missing:
        notes.append(f"⚠ 本册有 {len(missing)} 张凭证暂无已归档原件（见下方清单），补传原件后重新签发即可更新目录。")
    yy = y - 34
    for note in notes:
        c.drawString(LEFT, yy, note)
        yy -= 6 * mm
    if missing:
        rows: list[list[str]] = [["凭证号", "日期", "摘要"]]
        for item in missing[:18]:
            rows.append([str(item.get("voucherNo") or "—"), str(item.get("date") or ""), _clip(item.get("title"), 40)])
        if len(missing) > 18:
            rows.append(["…", "", f"另有 {len(missing) - 18} 张，详见系统「发票台账」提醒条"])
        yy = _draw_table_l(c, yy - 2 * mm, rows, widths=[1.4, 1.2, 4.4], aligns="CLL") - 8 * mm

    c.setFont(regular, 10.5)
    seg = CONTENT_W / 3
    for i, label in enumerate(("装订人：＿＿＿＿＿＿", "财务复核：＿＿＿＿＿＿", "归档日期：＿＿＿＿＿＿")):
        c.drawString(LEFT + seg * i, yy, label)
    c.setFont(regular, 8)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawString(LEFT, yy - 10 * mm, f"校验码（SHA-256）：{x.content_hash}")
    c.drawString(LEFT, yy - 15 * mm, f"编制：{x.generated_by or '—'}    编制时间：{x.generated_at[:19].replace('T', ' ') if x.generated_at else '—'}")
    if x.status == "void":
        c.setFillColor(_STATUS_COLORS["void"])
        c.setFont(bold, 10)
        c.drawString(LEFT, yy - 20 * mm, f"已作废 · 原因：{_clip(x.void_reason, 50)} · 时间：{x.voided_at[:19].replace('T', ' ') if x.voided_at else '—'}")
    c.setFillColor(colors.black)


def render_voucher_book_pdf(
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
    """渲染凭证册 PDF。参数缺省回落 book.meta 同名字段（与账簿渲染同约定）。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    meta = dict(book.get("meta") or {})
    vouchers = book.get("vouchers", [])
    if "voucherRange" not in meta:
        seqs = [str(item.get("voucherSeq") or "") for item in vouchers]
        meta["voucherRange"] = {"first": seqs[0] if seqs else "—", "last": seqs[-1] if seqs else "—"}
    company = str(meta.get("companyName") or "凭证册")
    resolved_book_no = str(book_no if book_no is not None else (meta.get("bookNo") or ""))
    resolved_hash = str(content_hash if content_hash is not None else (meta.get("contentHash") or ""))
    resolved_by = str(generated_by if generated_by is not None else (meta.get("generatedBy") or "制表人"))
    resolved_at = str(
        generated_at
        if generated_at is not None
        else (meta.get("generatedAt") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat())
    )
    book = {**book, "meta": meta}

    total_pages = int((book.get("pagination") or {}).get("totalPages") or 0)
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(PAGE_W, PAGE_H), invariant=1)
    c.setTitle(f"{company} 凭证册（{meta.get('month', '')}）")
    c.setAuthor(resolved_by or "梭罗记账 SoloBooks")
    c.setSubject(f"月度记账凭证装订册 {resolved_book_no or meta.get('month', '')}")
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
    )

    _bookmark(x, "cover", "凭证册封面")
    _draw_cover(x)
    _finish_page(x, "封面")

    for index, voucher in enumerate(vouchers):
        _bookmark(x, f"voucher-{index}", f"{voucher.get('voucherNo') or '未编号'}")
        _draw_voucher_page(x, voucher, index)
        _finish_page(x, "记账凭证")
        # 方案 A：凭证后逐份渲染发票 XML 复现页或图片原始凭证页
        for att_index, attachment in enumerate(voucher.get("attachments") or []):
            doc_type = str(attachment.get("documentType") or "")
            if doc_type == "einvoice_xml" and attachment.get("parsed"):
                invoice_no = str(attachment.get("invoiceNumber") or "发票")
                _bookmark(x, f"voucher-{index}-inv-{att_index}", f"　发票 {invoice_no}", level=1)
                _draw_einvoice_page(x, voucher, attachment)
                _finish_page(x, "发票复现")
            else:
                type_label = _TYPE_LABELS.get(doc_type, "原始凭证")
                _bookmark(x, f"voucher-{index}-att-{att_index}", f"　{type_label}", level=1)
                _draw_image_attachment_page(x, voucher, attachment)
                _finish_page(x, type_label)

    _draw_originals_pages(x, book.get("originals", []))

    _bookmark(x, "signoff", "装订签章")
    _draw_signoff(x)
    _finish_page(x, "装订签章")

    page_count = x.page_no - 1
    if total_pages and page_count != total_pages:
        raise PdfPageCountMismatch(
            f"凭证册实际 {page_count} 页与分页真值 {total_pages} 页不一致，拒绝出册"
        )
    pdf_bytes = c.getpdfdata()
    return PdfRenderResult(
        pdf_bytes=pdf_bytes,
        page_count=page_count,
        pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        outline_titles=x.outline,
    )


# ── 异步渲染入口（与账簿同一限流策略） ────────────────────────────────────────
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


async def render_voucher_book_pdf_async(book: dict[str, Any], **kwargs: Any) -> PdfRenderResult:
    """在线程池渲染（SOLOBOOKS_PDF_CONCURRENCY 限流），避免阻塞事件循环。"""

    def _job() -> PdfRenderResult:
        with _pdf_semaphore():
            return render_voucher_book_pdf(book, **kwargs)

    return await anyio.to_thread.run_sync(_job)
