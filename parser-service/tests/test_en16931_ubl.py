"""Tests for the EN 16931-compliant UBL 2.1 invoice serializer.

These cover structural conformance (namespaces, element ordering,
mandatory header fields) and the document-balancing logic (totals,
per-rate tax breakdown). Schematron-level semantic validation against
the EN 16931 ruleset is out of scope for these unit tests.
"""
from __future__ import annotations

from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from app.models import Invoice, InvoiceLine, Party, Totals
from app.serializers.en16931_ubl import (
    EN16931_CUSTOMIZATION_ID,
    NS_CAC,
    NS_CBC,
    NS_CREDIT_NOTE,
    NS_INVOICE,
    _to_iso_date,
    invoice_to_en16931_ubl,
)

CBC = f"{{{NS_CBC}}}"
CAC = f"{{{NS_CAC}}}"


def _invoice(
    *,
    document_type: str = "invoice",
    number: str = "INV-2026-0001",
    date: str = "2026-05-01",
    due_date: str = "2026-05-15",
    currency: str = "EUR",
    seller_name: str = "Blue River Software LTD",
    seller_vat: str | None = "LT100000000",
    buyer_name: str = "Greenfield Retail UAB",
    buyer_vat: str | None = "LT200000000",
    lines: list[InvoiceLine] | None = None,
    totals: Totals | None = None,
) -> Invoice:
    if lines is None:
        lines = [
            InvoiceLine(
                description="Monthly SaaS subscription - Pro plan",
                quantity=Decimal("3"),
                unit_price=Decimal("49.00"),
                vat_rate=Decimal("21"),
                line_total=Decimal("147.00"),
            )
        ]
    if totals is None:
        subtotal = sum((line.line_total for line in lines), Decimal("0"))
        tax = sum(
            (line.line_total * line.vat_rate / Decimal("100") for line in lines),
            Decimal("0"),
        )
        totals = Totals(subtotal=subtotal, tax=tax, total=subtotal + tax)
    return Invoice(
        type=document_type,
        number=number,
        date=date,
        due_date=due_date,
        currency=currency,
        seller=Party(name=seller_name, vat=seller_vat),
        buyer=Party(name=buyer_name, vat=buyer_vat),
        lines=lines,
        totals=totals,
    )


def _root(invoice: Invoice) -> ET.Element:
    return ET.fromstring(invoice_to_en16931_ubl(invoice))


# ---------------------------------------------------------------------------
# Root-level structure
# ---------------------------------------------------------------------------


def test_xml_declaration_and_root_is_invoice():
    raw = invoice_to_en16931_ubl(_invoice())
    assert raw.startswith(b"<?xml")
    root = ET.fromstring(raw)
    assert root.tag == f"{{{NS_INVOICE}}}Invoice"


def test_credit_note_uses_credit_note_root():
    invoice = _invoice(document_type="credit_note", number="CN-2026-0001")
    root = _root(invoice)
    assert root.tag == f"{{{NS_CREDIT_NOTE}}}CreditNote"


def test_customization_id_declares_en16931():
    root = _root(_invoice())
    assert root.findtext(f"{CBC}CustomizationID") == EN16931_CUSTOMIZATION_ID


def test_invoice_type_code_380_for_commercial_invoice():
    root = _root(_invoice())
    assert root.findtext(f"{CBC}InvoiceTypeCode") == "380"


def test_credit_note_type_code_381():
    invoice = _invoice(document_type="credit_note")
    root = _root(invoice)
    assert root.findtext(f"{CBC}CreditNoteTypeCode") == "381"


def test_header_fields_are_mapped():
    invoice = _invoice(number="INV-42", date="2026-01-15", due_date="2026-02-15",
                       currency="USD")
    root = _root(invoice)
    assert root.findtext(f"{CBC}ID") == "INV-42"
    assert root.findtext(f"{CBC}IssueDate") == "2026-01-15"
    assert root.findtext(f"{CBC}DueDate") == "2026-02-15"
    assert root.findtext(f"{CBC}DocumentCurrencyCode") == "USD"


def test_due_date_omitted_when_blank():
    invoice = _invoice(due_date="")
    root = _root(invoice)
    assert root.find(f"{CBC}DueDate") is None


def test_header_ordering_matches_ubl_schema():
    """UBL 2.1 has a strict child-order schema; the header block must appear
    before parties, which appear before TaxTotal and LegalMonetaryTotal."""
    root = _root(_invoice())
    tags = [child.tag.split("}", 1)[1] for child in root]
    # Expected canonical prefix sequence; trailing lines may follow.
    expected_prefix = [
        "CustomizationID", "ID", "IssueDate", "DueDate",
        "InvoiceTypeCode", "DocumentCurrencyCode",
        "AccountingSupplierParty", "AccountingCustomerParty",
        "TaxTotal", "LegalMonetaryTotal",
    ]
    assert tags[: len(expected_prefix)] == expected_prefix


# ---------------------------------------------------------------------------
# Parties (BG-4 supplier / BG-7 customer)
# ---------------------------------------------------------------------------


def test_supplier_block_has_name_and_legal_entity():
    root = _root(_invoice(seller_name="Acme Ltd", seller_vat="GB123456789"))
    supplier = root.find(f"{CAC}AccountingSupplierParty/{CAC}Party")
    assert supplier is not None
    assert supplier.findtext(f"{CAC}PartyName/{CBC}Name") == "Acme Ltd"
    assert supplier.findtext(
        f"{CAC}PartyTaxScheme/{CBC}CompanyID"
    ) == "GB123456789"
    assert supplier.findtext(
        f"{CAC}PartyTaxScheme/{CAC}TaxScheme/{CBC}ID"
    ) == "VAT"
    assert supplier.findtext(
        f"{CAC}PartyLegalEntity/{CBC}RegistrationName"
    ) == "Acme Ltd"


def test_customer_block_has_name_and_legal_entity():
    root = _root(_invoice(buyer_name="Beta GmbH", buyer_vat="DE111222333"))
    customer = root.find(f"{CAC}AccountingCustomerParty/{CAC}Party")
    assert customer.findtext(f"{CAC}PartyName/{CBC}Name") == "Beta GmbH"
    assert customer.findtext(
        f"{CAC}PartyTaxScheme/{CBC}CompanyID"
    ) == "DE111222333"


def test_party_without_vat_omits_party_tax_scheme():
    root = _root(_invoice(seller_vat=None))
    supplier = root.find(f"{CAC}AccountingSupplierParty/{CAC}Party")
    assert supplier.find(f"{CAC}PartyTaxScheme") is None
    # Legal entity is still mandatory.
    assert supplier.find(f"{CAC}PartyLegalEntity") is not None


def test_blank_party_name_uses_fallback():
    root = _root(_invoice(seller_name=""))
    supplier = root.find(f"{CAC}AccountingSupplierParty/{CAC}Party")
    assert supplier.findtext(f"{CAC}PartyName/{CBC}Name") == "Unknown supplier"


# ---------------------------------------------------------------------------
# Tax breakdown (BG-23)
# ---------------------------------------------------------------------------


def test_tax_total_emits_one_subtotal_per_rate():
    lines = [
        InvoiceLine(description="A", quantity=Decimal("1"),
                    unit_price=Decimal("100"), vat_rate=Decimal("21"),
                    line_total=Decimal("100")),
        InvoiceLine(description="B", quantity=Decimal("1"),
                    unit_price=Decimal("50"), vat_rate=Decimal("21"),
                    line_total=Decimal("50")),
        InvoiceLine(description="C", quantity=Decimal("1"),
                    unit_price=Decimal("200"), vat_rate=Decimal("9"),
                    line_total=Decimal("200")),
    ]
    invoice = _invoice(lines=lines)
    root = _root(invoice)
    subtotals = root.findall(f"{CAC}TaxTotal/{CAC}TaxSubtotal")
    assert len(subtotals) == 2
    rates = {
        s.findtext(f"{CAC}TaxCategory/{CBC}Percent")
        for s in subtotals
    }
    assert rates == {"21", "9"}


def test_zero_rate_uses_category_z():
    lines = [
        InvoiceLine(description="Exempt", quantity=Decimal("1"),
                    unit_price=Decimal("100"), vat_rate=Decimal("0"),
                    line_total=Decimal("100")),
    ]
    root = _root(_invoice(lines=lines, totals=Totals(
        subtotal=Decimal("100"), tax=Decimal("0"), total=Decimal("100"))))
    cat = root.findtext(
        f"{CAC}TaxTotal/{CAC}TaxSubtotal/{CAC}TaxCategory/{CBC}ID"
    )
    assert cat == "Z"


def test_nonzero_rate_uses_category_s():
    root = _root(_invoice())
    cat = root.findtext(
        f"{CAC}TaxTotal/{CAC}TaxSubtotal/{CAC}TaxCategory/{CBC}ID"
    )
    assert cat == "S"


def test_tax_total_prefers_declared_value_when_present():
    """BR-CO-15 expects TaxAmount to equal sum of TaxSubtotal amounts, but
    PDFs sometimes give a slightly different headline tax due to rounding.
    We trust the parsed value if it's non-zero so the document reflects the
    source-of-truth."""
    invoice = _invoice(
        totals=Totals(
            subtotal=Decimal("147.00"),
            tax=Decimal("30.87"),  # declared
            total=Decimal("177.87"),
        )
    )
    root = _root(invoice)
    assert root.findtext(f"{CAC}TaxTotal/{CBC}TaxAmount") == "30.87"


def test_tax_total_uses_summed_value_when_declared_is_zero():
    invoice = _invoice(
        totals=Totals(
            subtotal=Decimal("147.00"),
            tax=Decimal("0"),
            total=Decimal("147.00"),
        )
    )
    root = _root(invoice)
    # Per-line: 147 * 21% = 30.87
    assert root.findtext(f"{CAC}TaxTotal/{CBC}TaxAmount") == "30.87"


# ---------------------------------------------------------------------------
# Legal monetary total (BG-22)
# ---------------------------------------------------------------------------


def test_legal_monetary_total_is_quantized_to_two_decimals():
    invoice = _invoice(totals=Totals(
        subtotal=Decimal("647.5"),
        tax=Decimal("135.875"),
        total=Decimal("783.375"),
    ))
    root = _root(invoice)
    monetary = root.find(f"{CAC}LegalMonetaryTotal")
    assert monetary.findtext(f"{CBC}LineExtensionAmount") == "647.50"
    assert monetary.findtext(f"{CBC}TaxExclusiveAmount") == "647.50"
    assert monetary.findtext(f"{CBC}TaxInclusiveAmount") == "783.38"  # half-up
    assert monetary.findtext(f"{CBC}PayableAmount") == "783.38"


def test_monetary_amounts_carry_currency_id():
    root = _root(_invoice(currency="USD"))
    payable = root.find(f"{CAC}LegalMonetaryTotal/{CBC}PayableAmount")
    assert payable.get("currencyID") == "USD"


# ---------------------------------------------------------------------------
# Invoice / CreditNote lines (BG-25)
# ---------------------------------------------------------------------------


def test_invoice_lines_are_numbered_starting_at_one():
    lines = [
        InvoiceLine(description=f"Item {i}", quantity=Decimal("1"),
                    unit_price=Decimal("10"), vat_rate=Decimal("21"),
                    line_total=Decimal("10"))
        for i in range(1, 4)
    ]
    root = _root(_invoice(lines=lines))
    line_els = root.findall(f"{CAC}InvoiceLine")
    assert [l.findtext(f"{CBC}ID") for l in line_els] == ["1", "2", "3"]


def test_credit_note_uses_credit_note_line_and_credited_quantity():
    invoice = _invoice(document_type="credit_note")
    root = _root(invoice)
    assert root.findall(f"{CAC}InvoiceLine") == []
    line = root.find(f"{CAC}CreditNoteLine")
    assert line is not None
    assert line.find(f"{CBC}CreditedQuantity") is not None
    assert line.find(f"{CBC}InvoicedQuantity") is None


def test_line_has_item_and_price():
    root = _root(_invoice())
    line = root.find(f"{CAC}InvoiceLine")
    assert line.findtext(f"{CAC}Item/{CBC}Description") == \
        "Monthly SaaS subscription - Pro plan"
    assert line.findtext(f"{CAC}Item/{CBC}Name") == \
        "Monthly SaaS subscription - Pro plan"
    assert line.findtext(f"{CAC}Price/{CBC}PriceAmount") == "49.00"


def test_line_with_sku_emits_sellers_item_identification():
    lines = [
        InvoiceLine(
            sku="SKU-42",
            description="Thing",
            quantity=Decimal("1"),
            unit_price=Decimal("10"),
            vat_rate=Decimal("21"),
            line_total=Decimal("10"),
        )
    ]
    root = _root(_invoice(lines=lines))
    ident = root.find(
        f"{CAC}InvoiceLine/{CAC}Item/{CAC}SellersItemIdentification/{CBC}ID"
    )
    assert ident is not None
    assert ident.text == "SKU-42"


def test_line_without_sku_omits_sellers_item_identification():
    root = _root(_invoice())
    assert root.find(
        f"{CAC}InvoiceLine/{CAC}Item/{CAC}SellersItemIdentification"
    ) is None


def test_line_quantity_uses_c62_unit_code():
    root = _root(_invoice())
    qty = root.find(f"{CAC}InvoiceLine/{CBC}InvoicedQuantity")
    assert qty.get("unitCode") == "C62"


def test_line_classified_tax_category_is_populated():
    root = _root(_invoice())
    cat = root.find(
        f"{CAC}InvoiceLine/{CAC}Item/{CAC}ClassifiedTaxCategory"
    )
    assert cat.findtext(f"{CBC}ID") == "S"
    assert cat.findtext(f"{CBC}Percent") == "21"
    assert cat.findtext(f"{CAC}TaxScheme/{CBC}ID") == "VAT"


# ---------------------------------------------------------------------------
# Decimal formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (Decimal("147"), "147.00"),
        (Decimal("147.0"), "147.00"),
        (Decimal("147.005"), "147.01"),   # half-up rounding
        (Decimal("147.004"), "147.00"),
        (Decimal("0"), "0.00"),
    ],
)
def test_monetary_amounts_quantize_half_up(raw, expected):
    invoice = _invoice(totals=Totals(subtotal=raw, tax=Decimal("0"), total=raw))
    root = _root(invoice)
    assert root.findtext(
        f"{CAC}LegalMonetaryTotal/{CBC}LineExtensionAmount"
    ) == expected


# ---------------------------------------------------------------------------
# Date normalization (EN 16931 requires ISO 8601 for BT-2 / BT-9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-05-08", "2026-05-08"),
        ("2026/05/08", "2026-05-08"),
        ("May 8, 2026", "2026-05-08"),
        ("Jan 1, 2026", "2026-01-01"),
        ("8 May 2026", "2026-05-08"),
        ("08-May-2026", "2026-05-08"),
        ("", ""),                         # blank stays blank
        ("Some Friday", "Some Friday"),   # unrecognised stays as-is
    ],
)
def test_date_is_normalized_to_iso(raw, expected):
    assert _to_iso_date(raw) == expected


def test_invoice_dates_emitted_as_iso_in_xml():
    invoice = _invoice(date="May 8, 2026", due_date="8 May 2026")
    root = _root(invoice)
    assert root.findtext(f"{CBC}IssueDate") == "2026-05-08"
    assert root.findtext(f"{CBC}DueDate") == "2026-05-08"


# ---------------------------------------------------------------------------
# Regression: xml_writer.invoice_to_xml is the EN 16931 path
# ---------------------------------------------------------------------------


def test_public_xml_writer_delegates_to_en16931_ubl():
    from app.xml_writer import invoice_to_xml
    raw = invoice_to_xml(_invoice())
    root = ET.fromstring(raw)
    # If anyone reverts xml_writer.py to the legacy schema this fails fast.
    assert root.tag == f"{{{NS_INVOICE}}}Invoice"
    assert root.findtext(f"{CBC}CustomizationID") == EN16931_CUSTOMIZATION_ID
