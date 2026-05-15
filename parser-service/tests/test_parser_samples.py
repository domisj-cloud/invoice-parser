from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app.parser import parse_invoice_pdf
from app.parser import parse_invoice_text
from app.xml_writer import invoice_to_xml


SAMPLE_DIR = Path(
    os.environ.get(
        "INVOICE_SAMPLE_DIR",
        "/Users/domas/Downloads/invoice_pdf_examples",
    )
)


@pytest.mark.parametrize(
    "name",
    [
        "credit_note_negative_amounts",
        "invoice_modern_eu_vat",
        "invoice_multipage_many_lines",
        "invoice_plain_us_letter",
    ],
)
def test_sample_pdf_matches_expected_xml(name: str) -> None:
    pdf_path = SAMPLE_DIR / f"{name}.pdf"
    expected_xml_path = SAMPLE_DIR / f"{name}.xml"

    if not pdf_path.exists() or not expected_xml_path.exists():
        pytest.skip(f"Sample files not found under {SAMPLE_DIR}")

    actual_root = ET.fromstring(invoice_to_xml(parse_invoice_pdf(pdf_path)))
    expected_root = ET.parse(expected_xml_path).getroot()

    assert _node(actual_root) == _node(expected_root)


def test_generic_invoice_layout_with_inline_labels_and_compact_table() -> None:
    invoice = parse_invoice_text(
        """
        Page 1 of 1
        Invoice
        Invoice number 88IB6AXP\x000003
        Date of issue May 8, 2026
        Date due May 8, 2026
        Anthropic, PBC
        548 Market Street
        support@example.com
        Bill to
        domisj@gmail.com's Organization
        domisj@gmail.com
        €21.78 due May 8, 2026
        Description Qty Unit price Tax Amount
        Claude Pro
        May 8\x00Jun 8, 2026
        1 €18.00 21% €18.00
        Subtotal €18.00
        Total excluding tax €18.00
        VAT - Lithuania \x0021% on €18.00\x00 €3.78
        Total €21.78
        Amount due €21.78
        """
    )

    assert invoice.number == "88IB6AXP-0003"
    assert invoice.date == "May 8, 2026"
    assert invoice.due_date == "May 8, 2026"
    assert invoice.currency == "EUR"
    assert invoice.seller.name == "Anthropic, PBC"
    assert invoice.buyer.name == "domisj@gmail.com's Organization"
    assert invoice.lines[0].description == "Claude Pro - May 8-Jun 8, 2026"
    assert invoice.lines[0].vat_rate == 21
    assert invoice.totals.subtotal == 18
    assert invoice.totals.tax == Decimal("3.78")
    assert invoice.totals.total == Decimal("21.78")


def _node(element: ET.Element) -> tuple[str, str, list[object]]:
    return (
        element.tag,
        (element.text or "").strip(),
        [_node(child) for child in list(element)],
    )
