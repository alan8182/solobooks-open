# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""数电票 XML 解析器（电子凭证会计数据标准，实测 V0.37）。

锁定的口径：
1. 真实结构（脱敏自 2026-11 电子税务局下载样本）可解析：金额三项、明细行、
   免税票「免税」税率与 ``***`` 掩码税额、税务代开/平台代开特定信息、Remark；
2. 弱校验闸门：金额勾稽不平 / EIid≠发票号 / DOCTYPE / 非 20 位发票号 一律拒收；
3. ZIP 通道：GBK 文件名恢复、防穿越、非 XML 跳过、解压总量上限。
"""
from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

from services import einvoice_xml_service
from services.einvoice_xml_service import EInvoiceParseError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "einvoice"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parses_enterprise_blue_invoice():
    parsed = einvoice_xml_service.parse_einvoice_xml(_load("enterprise_blue.xml"))
    assert parsed["invoiceNumber"] == "26337000000000000001"
    assert parsed["issueTime"] == "2026-11-21"
    assert parsed["amounts"] == {"excl": "50.94", "tax": "3.06", "incl": "54.00",
                                 "inclChinese": "伍拾肆圆整"}
    assert parsed["generalOrSpecial"] == "普通发票"
    assert parsed["taxpayerType"] == "一般纳税人"
    assert parsed["isBlue"] is True
    assert parsed["seller"]["name"] == "测试云计算有限公司"
    assert parsed["buyer"]["taxId"] == "91330110MA00BUYER1"
    assert parsed["hasTaxBureauSignature"] is True
    assert parsed["items"][0]["taxRate"] == "0.06"


def test_parses_exempt_person_agent_invoice():
    """免税票：税率是文本「免税」、行税额是 ``***`` 掩码——必须容忍并透传。"""
    parsed = einvoice_xml_service.parse_einvoice_xml(_load("exempt_person_agent.xml"))
    assert parsed["taxpayerType"] == "自然人"
    assert parsed["amounts"]["tax"] == "0.00"
    assert parsed["items"][0]["taxRate"] == "免税"
    assert parsed["items"][0]["taxAmount"] == ""
    # 税务代开特定信息 + 代扣代缴个税提示（业务信号必须透出）
    assert "AgentIssuingInvoice" in parsed["specific"]
    assert parsed["withholdingNotice"] is True


def test_parses_multi_line_items_and_layout_sums():
    parsed = einvoice_xml_service.parse_einvoice_xml(_load("multi_items.xml"))
    assert len(parsed["items"]) == 2
    assert parsed["amounts"]["incl"] == "1060.00"
    assert parsed["generalOrSpecial"] == "专用发票"


def test_rejects_unreconciled_amounts():
    with pytest.raises(EInvoiceParseError, match="勾稽不平"):
        einvoice_xml_service.parse_einvoice_xml(_load("bad_reconcile.xml"))


def test_rejects_doctype_payloads():
    """不引第三方依赖的 XXE 防线：DOCTYPE/ENTITY 出现即拒收。"""
    with pytest.raises(EInvoiceParseError, match="DOCTYPE"):
        einvoice_xml_service.parse_einvoice_xml(_load("bad_doctype.xml"))


def test_rejects_eiid_invoice_number_mismatch():
    with pytest.raises(EInvoiceParseError, match="EIid"):
        einvoice_xml_service.parse_einvoice_xml(_load("bad_number_mismatch.xml"))


def test_rejects_bad_invoice_number_and_oversize():
    with pytest.raises(EInvoiceParseError, match="20 位"):
        einvoice_xml_service.parse_einvoice_xml(_load("enterprise_blue.xml").replace(b"26337000000000000001", b"123"))
    with pytest.raises(EInvoiceParseError, match="上限"):
        einvoice_xml_service.parse_einvoice_xml(b"<EInvoice>" + b"x" * (einvoice_xml_service.MAX_XML_BYTES + 1))


def test_normalize_name_unifies_fullwidth_brackets():
    """实测同一张票在不同批次买方名称括号全半角会变——匹配必须归一。"""
    assert einvoice_xml_service.normalize_name("示例采购（杭州）有限公司") == \
        einvoice_xml_service.normalize_name("示例采购(杭州)有限公司")
    assert einvoice_xml_service.normalize_name(" 示例 公司 ") == "示例公司"


def test_build_import_rows_matches_pipeline_shape():
    """行键必须命中 parse_invoice_rows/invoice_fields_from_entry 的读取口径。"""
    parsed = einvoice_xml_service.parse_einvoice_xml(_load("enterprise_blue.xml"))
    rows = einvoice_xml_service.build_import_rows(parsed, direction="expense")
    assert len(rows) == 1
    row = rows[0]
    assert row["invoice_number"] == "26337000000000000001"
    assert row["invoice_date"] == "2026-11-21"
    assert row["total_amount"] == "54.00"
    assert row["tax_amount"] == "3.06"
    assert row["amountExcludingTax"] == "50.94"
    assert row["direction"] == "expense"
    assert row["seller_name"] == "测试云计算有限公司"
    # 免税票透传文本税率
    exempt = einvoice_xml_service.parse_einvoice_xml(_load("exempt_person_agent.xml"))
    assert einvoice_xml_service.build_import_rows(exempt, direction="expense")[0]["tax_rate"] == "免税"


def _build_raw_zip(entries: list[tuple[str, bytes]], *, gbk: bool = False) -> bytes:
    """手工构造 ZIP：gbk=True 时不设 UTF-8 标志位并用 GBK 编码文件名（税局实况）。"""
    import zlib

    out = io.BytesIO()
    central: list[bytes] = []
    offset = 0
    for name, data in entries:
        raw_name = name.encode("gbk") if gbk else name.encode("utf-8")
        flags = 0 if gbk else 0x800
        crc = zlib.crc32(data) & 0xFFFFFFFF
        local = struct.pack(
            "<4s5H3L2H", b"PK\x03\x04", 20, flags, 0, 0, 0, crc, len(data), len(data), len(raw_name), 0
        )
        out.write(local + raw_name + data)
        central.append(
            struct.pack(
                "<4s6H3L5H2L", b"PK\x01\x02", 20, 20, flags, 0, 0, 0, crc, len(data), len(data),
                len(raw_name), 0, 0, 0, 0, 0xFFFFFFFF, offset,
            ) + raw_name
        )
        offset += 30 + len(raw_name) + len(data)
    central_dir = b"".join(central)
    eocd = struct.pack(
        "<4s4H2LH", b"PK\x05\x06", 0, 0, len(entries), len(entries), len(central_dir), offset, 0
    )
    return out.getvalue() + central_dir + eocd


def test_zip_extracts_with_gbk_names_and_skips_non_xml():
    xml = _load("enterprise_blue.xml")
    payload = _build_raw_zip(
        [("dzfp_26337000000000000001_测试公司_20261121000000.xml", xml), ("发票统计.xlsx", b"xlsx-bytes")],
        gbk=True,
    )
    entries = einvoice_xml_service.iter_zip_entries(payload)
    assert len(entries) == 2
    assert entries[0]["status"] == "parsed"
    assert entries[0]["fileName"] == "dzfp_26337000000000000001_测试公司_20261121000000.xml"
    # GBK 名单内文件可再解析
    assert einvoice_xml_service.parse_einvoice_xml(entries[0]["xml"])["invoiceNumber"] == "26337000000000000001"
    assert entries[1]["status"] == "skipped"


def test_zip_blocks_path_traversal():
    xml = _load("enterprise_blue.xml")
    payload = _build_raw_zip([("../evil/outside.xml", xml)])
    entries = einvoice_xml_service.iter_zip_entries(payload)
    assert entries[0]["fileName"] == "outside.xml"


def test_zip_rejects_oversize_and_file_count():
    xml = _load("enterprise_blue.xml")
    huge = _build_raw_zip([("a.xml", xml) for _ in range(3)])
    # 用 monkeypatch 缩上限验证守卫存在（避免测试真的构造 30MB 数据）
    original_files, original_bytes = einvoice_xml_service.MAX_ZIP_FILES, einvoice_xml_service.MAX_ZIP_UNCOMPRESSED_BYTES
    try:
        einvoice_xml_service.MAX_ZIP_FILES = 2
        with pytest.raises(EInvoiceParseError, match="上限"):
            einvoice_xml_service.iter_zip_entries(huge)
        einvoice_xml_service.MAX_ZIP_FILES = original_files
        einvoice_xml_service.MAX_ZIP_UNCOMPRESSED_BYTES = 10
        with pytest.raises(EInvoiceParseError, match="超限"):
            einvoice_xml_service.iter_zip_entries(huge)
    finally:
        einvoice_xml_service.MAX_ZIP_FILES = original_files
        einvoice_xml_service.MAX_ZIP_UNCOMPRESSED_BYTES = original_bytes


def test_plain_zipfile_std_path_also_works():
    """常规 ZIP（UTF-8 标志位）走同一通道。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("dzfp_a.xml", _load("enterprise_blue.xml"))
        archive.writestr("统计.xlsx", b"xlsx")
    entries = einvoice_xml_service.iter_zip_entries(buffer.getvalue())
    assert [entry["status"] for entry in entries] == ["parsed", "skipped"]
