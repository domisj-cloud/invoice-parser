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


def test_generic_invoice_layout_with_inline_description_row_and_usd_prefix() -> None:
    invoice = parse_invoice_text(
        """
        Page 1 of 1
        Invoice
        Invoice number NMQ6V9FT\x000001
        Date of issue April 27, 2026
        Date due April 27, 2026
        MOONSHOT AI PTE. LTD.
        91 BENCOOLEN STREET
        api-service@moonshot.ai
        SG GST 202326494K
        Bill to
        Domas
        domisj@gmail.com
        US$10.00 due April 27, 2026
        Description Qty Unit price Amount
        Account Top-up 1 US$10.00 US$10.00
        Subtotal US$10.00
        Total US$10.00
        Amount due US$10.00
        """
    )

    assert invoice.number == "NMQ6V9FT-0001"
    assert invoice.currency == "USD"
    assert invoice.seller.name == "MOONSHOT AI PTE. LTD."
    assert invoice.buyer.name == "Domas"
    assert invoice.lines[0].description == "Account Top-up"
    assert invoice.lines[0].unit_price == 10
    assert invoice.lines[0].vat_rate == 0
    assert invoice.totals.subtotal == 10
    assert invoice.totals.tax == 0
    assert invoice.totals.total == 10


def test_generic_receipt_with_colon_labels_and_no_line_table() -> None:
    invoice = parse_invoice_text(
        """
        Official Receipt / Tax Invoice #: 12043017
        Prepared on Behalf of your Test Sponsor: Google Cloud
        Date: 07 March 2023 VAT #: LT100005242513
        Company Name: Skandinaviska Enskilda Banken AB
        Candidate Name: Domas Jautakis
        Exam Name: Google Cloud Certified - Professional Cloud Architect (English)
        Scheduled Date: 28 April 2023 1000H Europe/Vilnius
        Transaction Date: 06 March 2023
        Exam Price: 120.00 USD
        Promotion Amount: -60.00 USD
        Tax: 12.60 USD
        Transaction Amount: 72.60 USD
        Transaction Confirmation #: 5ad7aa9ebb82063da5e8d223a5eca497
        """
    )

    assert invoice.document_type == "receipt"
    assert invoice.number == "12043017"
    assert invoice.date == "06 March 2023"
    assert invoice.due_date == "28 April 2023 1000H Europe/Vilnius"
    assert invoice.currency == "USD"
    assert invoice.seller.name == "Google Cloud"
    assert invoice.buyer.name == "Skandinaviska Enskilda Banken AB"
    assert invoice.lines[0].description == "Google Cloud Certified - Professional Cloud Architect (English)"
    assert invoice.lines[0].line_total == 120
    assert invoice.lines[1].description == "Promotion Amount"
    assert invoice.lines[1].line_total == -60
    assert invoice.totals.subtotal == 60
    assert invoice.totals.tax == Decimal("12.60")
    assert invoice.totals.total == Decimal("72.60")


def test_dynamic_generic_invoice_with_bilingual_labels_and_no_table() -> None:
    invoice = parse_invoice_text(
        """
        PVM sąskaita faktūra / Invoice CSI-25-0008395
        2025-06-09
        Paslaugų tiekėjas / Supplier
        Stuart Energy, UAB
        Įmonės kodas / Code: 305556655
        PVM kodas / EU VAT no.: LT100013523217
        Adresas / Address: Saulėtekio al. 15, LT-10224 Vilnius
        Paslaugų gavėjas / Customer
        Domas J
        Įkrovimo paslauga / Charging service
        Suteiktų paslaugų data / Date of service 2025-06-09
        Krovimo kiekis / Charging amount 7.366 kWh
        Iš viso be PVM / Total excl. VAT € 1.83
        21% PVM / 21% VAT € 0.38
        Iš viso su PVM / Total incl. VAT € 2.21
        """
    )

    assert invoice.number == "CSI-25-0008395"
    assert invoice.date == "2025-06-09"
    assert invoice.currency == "EUR"
    assert invoice.seller.name == "Stuart Energy, UAB"
    assert invoice.buyer.name == "Domas J"
    assert invoice.lines[0].description == "Charging service"
    assert invoice.lines[0].vat_rate == 21
    assert invoice.lines[0].line_total == Decimal("1.83")
    assert invoice.totals.subtotal == Decimal("1.83")
    assert invoice.totals.tax == Decimal("0.38")
    assert invoice.totals.total == Decimal("2.21")


def test_dynamic_generic_invoice_with_stacked_table_and_separate_labels() -> None:
    invoice = parse_invoice_text(
        """
        INVOICE
        # 31061
        SuperStore
        Bill To
        :
        Yoseph Carroll
        Ship To
        :
        Manukau City,
        Auckland, New
        Zealand
        Nov 30 2012
        Standard Class
        $2,921.43
        Date
        :
        Ship Mode
        :
        Balance Due
        :
        Item
        Quantity
        Rate
        Amount
        Bevis Computer Table, Adjustable Height
        4
        $1,181.02
        $4,724.06
        Tables, Furniture, FUR-TA-3417
        $4,724.06
        $1,889.62
        $86.99
        $2,921.43
        Subtotal
        :
        Discount (40%)
        :
        Shipping
        :
        Total
        :
        """
    )

    assert invoice.number == "31061"
    assert invoice.date == "Nov 30 2012"
    assert invoice.currency == "USD"
    assert invoice.seller.name == "SuperStore"
    assert invoice.buyer.name == "Yoseph Carroll"
    assert invoice.lines[0].description == "Bevis Computer Table, Adjustable Height"
    assert invoice.lines[0].quantity == 4
    assert invoice.lines[0].unit_price == Decimal("1181.02")
    assert invoice.lines[0].line_total == Decimal("4724.06")
    assert invoice.totals.total == Decimal("2921.43")


def _node(element: ET.Element) -> tuple[str, str, list[object]]:
    return (
        element.tag,
        (element.text or "").strip(),
        [_node(child) for child in list(element)],
    )
