# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""数电发票 XML 解析（电子凭证会计数据标准，实测版式 V0.37）。

只做「结构解析 + 弱校验」，不做 SM2 验签：金额三项勾稽（不含税 + 税额 =
价税合计）与 EIid/发票号一致性是最小防篡改闸门，完整验签 Phase 3 再做。
解析结果存 attachments.parsed_json 作为唯一结构真相，import_rows 按
``invoice_service`` 导入管线消费的行形状按需派生（单一数据源）。

实测踩坑（来自 2026-11 真实样本）：
- 免税票 ``TaxRate`` 是文本「免税」、行税额是 ``***`` —— 非数值必须容忍；
- 买卖方名称全角/半角括号不一致 —— 匹配一律走 :func:`normalize_name`；
- XML 由电子税务局下载，可能重复下载同一发票号 —— 判重以发票号为主键。
"""
from __future__ import annotations

import io
import os
import re
import zipfile
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree

from core.money import MoneyError, money, money_text

# 单份 XML 上限（实测样本 ~5KB，2MB 已是宽裕余量）与 ZIP 总解压上限
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_ZIP_FILES = 100
MAX_ZIP_UNCOMPRESSED_BYTES = 30 * 1024 * 1024
INVOICE_NUMBER_PATTERN = re.compile(r"^\d{20}$")


class EInvoiceParseError(ValueError):
    """数电票 XML 解析/校验失败（带面向用户的 message）。"""


def normalize_name(value: Any) -> str:
    """名称归一化：全角括号→半角、去空白、小写。

    与 invoice_ledger_service.build_fingerprint 的括号归一口径一致；
    实测同一张票在不同下载批次里买方名称括号全半角会变。
    """
    text = str(value or "").strip()
    return text.replace("（", "(").replace("）", ")").replace(" ", "").lower()


def _text(el: ElementTree.Element | None, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _amount(value: Any) -> Decimal | None:
    """金额解析：空/掩码按缺失处理（免税票行税额是 ``***``）。"""
    text = str(value or "").strip()
    if not text or text == "***":
        return None
    try:
        return money(text)
    except MoneyError as exc:
        raise EInvoiceParseError(f"金额格式无效：{value}") from exc


def _reconcile_amounts(basic: ElementTree.Element | None) -> tuple[Decimal, Decimal, Decimal]:
    """金额三项勾稽：不含税 + 税额 == 价税合计（逐分精确，硬校验）。"""
    excl = _amount(basic.findtext("TotalAmWithoutTax") if basic is not None else None)
    tax = _amount(basic.findtext("TotalTaxAm") if basic is not None else None)
    incl = _amount(basic.findtext("TotalTax-includedAmount") if basic is not None else None)
    missing = [name for name, value in (("不含税", excl), ("税额", tax), ("价税合计", incl)) if value is None]
    if missing:
        raise EInvoiceParseError(f"缺少金额要素：{'、'.join(missing)}")
    if excl + tax != incl:  # noqa: PLR0123 —— Decimal 精确比较，不允许容差
        raise EInvoiceParseError(
            f"金额勾稽不平：不含税 {money_text(excl)} + 税额 {money_text(tax)} ≠ 价税合计 {money_text(incl)}"
        )
    return excl, tax, incl


def _parse_labels(header: ElementTree.Element) -> dict[str, Any]:
    inherent: dict[str, dict[str, str]] = {}
    inherent_el = header.find("InherentLabel")
    if inherent_el is not None:
        for child in inherent_el:
            code = _text(child, "LabelCode")
            name = _text(child, "LabelName")
            inherent[child.tag] = {"code": code, "name": name}
    labels: list[dict[str, str]] = []
    undefined_el = header.find("UndefinedLabel")
    if undefined_el is not None:
        for label in undefined_el.findall("Label"):
            label_type = _text(label, "LabelType")
            if label_type:
                labels.append({"type": label_type, "code": _text(label, "LabelCode"), "name": _text(label, "LabelName")})
    return {"inherent": inherent, "undefined": labels}


def _parse_items(data: ElementTree.Element, total_excl: Decimal) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for node in data.findall("IssuItemInformation"):
        line_amount = _amount(node.findtext("Amount"))
        items.append(
            {
                "itemName": _text(node, "ItemName"),
                "spec": _text(node, "SpecMod"),
                "unit": _text(node, "MeaUnits"),
                "quantity": _text(node, "Quantity"),
                "unitPrice": _text(node, "UnPrice"),
                "amount": money_text(line_amount) if line_amount is not None else "",
                "taxRate": _text(node, "TaxRate"),
                # 免税票行税额是 "***"，按缺失透传
                "taxAmount": _text(node, "ComTaxAm") if _text(node, "ComTaxAm") != "***" else "",
                "taxIncludedAmount": _text(node, "TotaltaxIncludedAmount"),
                "taxClassificationCode": _text(node, "TaxClassificationCode"),
            }
        )
    if items:
        line_sum = sum((_amount(item["amount"]) or Decimal("0.00")) for item in items)
        if line_sum != total_excl:
            # 明细合计与总额偶有分位级差异，做警告不拒收（总勾稽已硬校验）
            warnings.append(f"明细金额合计 {money_text(line_sum)} 与不含税总额 {money_text(total_excl)} 不一致")
    return items, warnings


def _parse_specific(data: ElementTree.Element) -> dict[str, Any]:
    specific: dict[str, Any] = {}
    node = data.find("SpecificInformation")
    if node is None:
        return specific
    for child in node:
        entry: dict[str, Any] = {"fields": {}}
        for sub in child:
            entry["fields"][sub.tag] = (sub.text or "").strip()
        specific[child.tag] = entry
    return specific


def parse_einvoice_xml(payload: bytes) -> dict[str, Any]:
    """解析单份数电票 XML（bytes），输出结构化 dict。

    校验失败抛 :class:`EInvoiceParseError`；解析结果即 attachments.parsed_json。
    """
    if len(payload) > MAX_XML_BYTES:
        raise EInvoiceParseError(f"XML 超过单文件上限 {MAX_XML_BYTES // (1024 * 1024)}MB")
    # 不引第三方依赖的 XXE 防线：真实税局 XML 不含 DOCTYPE/ENTITY，出现即拒收
    upper = payload[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise EInvoiceParseError("XML 含 DOCTYPE/ENTITY 声明，疑似非税局原件，已拒绝")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise EInvoiceParseError("XML 格式无法解析") from exc
    if root.tag != "EInvoice":
        raise EInvoiceParseError(f"根节点 {root.tag} 不是数电票 EInvoice")

    header = root.find("Header")
    data = root.find("EInvoiceData")
    supervision = root.find("TaxSupervisionInfo")
    if header is None or data is None or supervision is None:
        raise EInvoiceParseError("缺少 Header/EInvoiceData/TaxSupervisionInfo 段")

    ei_id = _text(header, "EIid")
    invoice_number = _text(supervision, "InvoiceNumber")
    if not INVOICE_NUMBER_PATTERN.match(invoice_number):
        raise EInvoiceParseError(f"发票号 {invoice_number or '缺失'} 不是 20 位数字")
    if ei_id and ei_id != invoice_number:
        raise EInvoiceParseError("EIid 与 InvoiceNumber 不一致")

    excl, tax, incl = _reconcile_amounts(data.find("BasicInformation"))
    items, warnings = _parse_items(data, excl)
    labels = _parse_labels(header)
    seller = {
        "taxId": _text(data, "SellerInformation/SellerIdNum"),
        "name": _text(data, "SellerInformation/SellerName"),
        "address": _text(data, "SellerInformation/SellerAddr"),
        "phone": _text(data, "SellerInformation/SellerTelNum"),
        "bankName": _text(data, "SellerInformation/SellerBankName"),
        "bankAccount": _text(data, "SellerInformation/SellerBankAccNum"),
    }
    buyer = {
        "taxId": _text(data, "BuyerInformation/BuyerIdNum"),
        "name": _text(data, "BuyerInformation/BuyerName"),
        "handler": _text(data, "BuyerInformation/BuyerHandlingName"),
    }
    issue_time = _text(supervision, "IssueTime")
    signature_el = root.find("TaxBureauSignature")
    remark = _text(data, "AdditionalInformation/Remark")
    # 自然人代开票的备注带代扣代缴个税提示，是产品要透出的业务信号
    withheld_notice = "代扣代缴" in remark if remark else False

    return {
        "standard": "einvoicexml",
        "version": _text(header, "Version"),
        "einvoiceTag": _text(header, "EInvoiceTag"),
        "invoiceNumber": invoice_number,
        "issueTime": issue_time,
        "taxBureauName": _text(supervision, "TaxBureauName"),
        "labels": labels,
        "generalOrSpecial": labels["inherent"].get("GeneralOrSpecialVAT", {}).get("name", ""),
        "invoiceKind": labels["inherent"].get("EInvoiceType", {}).get("name", ""),
        "isBlue": labels["inherent"].get("InIssuType", {}).get("code", "") == "Y",
        "taxpayerType": labels["inherent"].get("TaxpayerType", {}).get("name", ""),
        "seller": seller,
        "buyer": buyer,
        "amounts": {
            "excl": money_text(excl),
            "tax": money_text(tax),
            "incl": money_text(incl),
            "inclChinese": _text(data, "BasicInformation/TotalTax-includedAmountInChinese"),
        },
        "drawer": _text(data, "BasicInformation/Drawer"),
        "requestTime": _text(data, "BasicInformation/RequestTime"),
        "items": items,
        "specific": _parse_specific(data),
        "remark": remark,
        "withholdingNotice": withheld_notice,
        "hasTaxBureauSignature": bool(signature_el is not None and _text(signature_el, "SignatureValue")),
        "signatureTime": _text(signature_el, "SignatureTime") if signature_el is not None else "",
        "warnings": warnings,
    }


def iter_zip_entries(payload: bytes) -> list[dict[str, Any]]:
    """展开 ZIP 包：GBK 文件名恢复 + 防穿越 + 非 XML 跳过。

    返回 [{fileName, status: parsed|skipped|error, xml?, error?}]；
    解析在 :func:`parse_einvoice_xml` 由调用方逐份执行。
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise EInvoiceParseError("ZIP 文件损坏或不是压缩包") from exc

    entries: list[dict[str, Any]] = []
    total_uncompressed = 0
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        xml_infos = [info for info in infos if os.path.splitext(info.filename)[1].lower() == ".xml"]
        for info in infos:
            try:
                name = info.filename
                # 税局 ZIP 不设 UTF-8 标志位，cp437 解码出来的名字要经 GBK 还原
                if info.flag_bits & 0x800 == 0:
                    try:
                        name = info.filename.encode("cp437").decode("gbk")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        name = info.filename
                # 只取文件名段，杜绝 ZIP 内嵌路径穿越（zip-slip）
                name = os.path.basename(name.replace("\\", "/"))
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise EInvoiceParseError("ZIP 解压总大小超限")
                if os.path.splitext(name)[1].lower() != ".xml":
                    entries.append({"fileName": name, "status": "skipped", "error": "非 XML 文件，已跳过"})
                    continue
                if len(xml_infos) > MAX_ZIP_FILES:
                    raise EInvoiceParseError(f"ZIP 内 XML 超过 {MAX_ZIP_FILES} 份上限")
                data = archive.read(info)
                entries.append({"fileName": name, "status": "parsed", "xml": data})
            except EInvoiceParseError:
                raise
            except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
                entries.append({"fileName": info.filename, "status": "error", "error": f"读取失败：{exc}"})
    return entries


def build_import_rows(parsed: dict[str, Any], *, direction: str) -> list[dict[str, Any]]:
    """解析结果 → 导入管线消费的行形状。

    键名对齐 ``invoice_service.parse_invoice_rows``（invoice_number/total_amount/
    tax_amount）与 ``invoice_ledger_service.invoice_fields_from_entry``
    （amountExcludingTax 只认 camelCase），确认权留在预览步。
    """
    if not parsed.get("items"):
        return []
    first = parsed["items"][0]
    rows = [
        {
            "invoice_number": parsed.get("invoiceNumber", ""),
            "invoice_type": parsed.get("generalOrSpecial", ""),
            "invoice_date": parsed.get("issueTime", ""),
            "direction": direction,
            # 导入管线口径：total_amount=价税合计，tax_amount=税额，amountExcludingTax=不含税
            "total_amount": parsed["amounts"]["incl"],
            "tax_amount": parsed["amounts"]["tax"],
            "amountExcludingTax": parsed["amounts"]["excl"],
            "tax_rate": first.get("taxRate", ""),
            "item_name": first.get("itemName", ""),
            "seller_name": parsed["seller"].get("name", ""),
            "seller_tax_id": parsed["seller"].get("taxId", ""),
            "buyer_name": parsed["buyer"].get("name", ""),
            "buyer_tax_id": parsed["buyer"].get("taxId", ""),
            "remark": parsed.get("remark", ""),
        }
    ]
    return rows
