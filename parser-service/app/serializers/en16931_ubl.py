"""EN 16931-compliant UBL 2.1 invoice serializer.

EN 16931 is the European semantic standard for electronic invoicing. It
defines what an invoice means but not how to encode it on the wire; two
syntax bindings are officially permitted:

    - OASIS UBL 2.1
    - UN/CEFACT Cross Industry Invoice (CII)

This module implements the UBL 2.1 binding. Documents declare conformance
to EN 16931 via the `cbc:CustomizationID = urn:cen.eu:en16931:2017`.

Mapping summary (UBL element <- EN 16931 BT code <- internal model):

    cbc:ID                        BT-1   Invoice.number
    cbc:IssueDate                 BT-2   Invoice.date
    cbc:DueDate                   BT-9   Invoice.due_date (if non-empty)
    cbc:InvoiceTypeCode           BT-3   380 (commercial invoice)
    cbc:CreditNoteTypeCode        BT-3   381 (credit note) — uses
                                          <CreditNote> root instead
    cbc:DocumentCurrencyCode      BT-5   Invoice.currency

    AccountingSupplierParty/...   BG-4   Invoice.seller
        PartyName/Name                BT-28  trading name
        PartyTaxScheme/CompanyID      BT-31  VAT id
        PartyLegalEntity/RegName      BT-27  legal name (same as BT-28)
    AccountingCustomerParty/...   BG-7   Invoice.buyer

    TaxTotal                      BG-23  per-rate VAT breakdown
    LegalMonetaryTotal            BG-22  document totals

    InvoiceLine / CreditNoteLine  BG-25  Invoice.lines[*]
        ID                            BT-126 1-based index
        InvoicedQuantity              BT-129
        LineExtensionAmount           BT-131
        Item/Name + Description       BT-153 / BT-154
        SellersItemIdentification     BT-155 sku (if present)
        ClassifiedTaxCategory         BG-30  per-line VAT category
        Price/PriceAmount             BT-146

Limitations of this PoC serializer:

    - We don't extract seller/buyer postal addresses (BT-35/BT-50/etc.),
      country codes, payment terms, payment means, or bank details.
      Documents are therefore XSD-valid UBL 2.1 with EN 16931
      CustomizationID, but full EN 16931 *semantic* (Schematron) rule
      conformance depends on data we can't recover from the PDF.
    - VAT category is always 'S' (Standard rate) for non-zero rates and
      'Z' (Zero rated) for 0% lines. Other categories (reverse charge,
      exempt, etc.) require business context we don't infer.
    - Unit-of-measure code is fixed to C62 (UN/ECE Rec 20: "one",
      dimensionless count) since line items are quantity-of-units.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable
from xml.etree import ElementTree as ET

from app.models import Invoice, InvoiceLine, Party, Totals

# Date formats commonly seen in invoice PDFs that we attempt to normalize
# to EN 16931 BT-2's required ISO-8601 (YYYY-MM-DD) form. Ordering matters:
# more specific patterns must come first so they win against shorter ones
# that would match a substring. Ambiguous DMY/MDY formats are intentionally
# excluded — guessing them silently is worse than passing the original
# text through unchanged.
_DATE_FORMATS = (
    "%Y-%m-%d",     # 2026-05-08 (already ISO)
    "%Y/%m/%d",     # 2026/05/08
    "%B %d, %Y",    # May 8, 2026
    "%b %d, %Y",    # May 8, 2026  (Jan/Feb/…)
    "%d %B %Y",     # 8 May 2026
    "%d %b %Y",     # 8 May 2026
    "%d-%b-%Y",     # 08-May-2026
    "%d-%B-%Y",     # 08-May-2026
)


def _to_iso_date(raw: str) -> str:
    """Best-effort normalization to ISO 8601 (YYYY-MM-DD).

    Returns the original string unchanged if no known pattern matches.
    EN 16931 requires ISO format for BT-2 / BT-9 / BT-72; downstream
    validators (e.g. Schematron) will flag any value that fails to
    normalize here, which is the desired loud failure mode.
    """
    text = (raw or "").strip()
    if not text:
        return text
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text

# UBL 2.1 namespaces.
NS_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_CREDIT_NOTE = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

# EN 16931 conformance identifier (BT-24).
EN16931_CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017"

# UN/ECE Recommendation 20 unit code: "one" (dimensionless count). Used as
# a safe default when we don't know the actual unit of measure.
DEFAULT_UNIT_CODE = "C62"

# UNCL5305 tax category codes.
TAX_CATEGORY_STANDARD = "S"
TAX_CATEGORY_ZERO_RATED = "Z"


def _q2(value: Decimal) -> str:
    """Quantize a monetary amount to 2 decimal places per EN 16931 BR-DEC-* rules."""
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _qty(value: Decimal) -> str:
    """Format a quantity. Integers stay integer; fractions keep their natural precision."""
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return f"{normalized:.0f}"
    return format(normalized, "f")


def _percent(value: Decimal) -> str:
    """Format a percentage. Strip trailing zeros for readability."""
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return f"{normalized:.0f}"
    return format(normalized, "f")


def _tax_category_for(rate: Decimal) -> str:
    return TAX_CATEGORY_STANDARD if rate > 0 else TAX_CATEGORY_ZERO_RATED


def _is_credit_note(invoice: Invoice) -> bool:
    return invoice.document_type == "credit_note"


def _register_namespaces() -> None:
    """Register the standard prefix bindings ElementTree should emit."""
    ET.register_namespace("", NS_INVOICE)
    ET.register_namespace("cac", NS_CAC)
    ET.register_namespace("cbc", NS_CBC)


def _qn(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def _sub(parent: ET.Element, ns: str, local: str, text: str | None = None) -> ET.Element:
    element = ET.SubElement(parent, _qn(ns, local))
    if text is not None:
        element.text = text
    return element


def _cbc(parent: ET.Element, local: str, text: str | None = None) -> ET.Element:
    return _sub(parent, NS_CBC, local, text)


def _cac(parent: ET.Element, local: str) -> ET.Element:
    return _sub(parent, NS_CAC, local)


def _amount(parent: ET.Element, local: str, value: Decimal, currency: str) -> ET.Element:
    element = _cbc(parent, local, _q2(value))
    element.set("currencyID", currency)
    return element


def _add_party(
    parent: ET.Element, role_tag: str, party: Party, *, fallback_name: str
) -> None:
    """Render an AccountingSupplierParty / AccountingCustomerParty subtree."""
    role = _cac(parent, role_tag)
    party_el = _cac(role, "Party")

    name = party.name or fallback_name
    name_el = _cac(party_el, "PartyName")
    _cbc(name_el, "Name", name)

    # PartyTaxScheme only emitted when a VAT identifier is present (BG-4/BT-31).
    if party.vat:
        tax_scheme = _cac(party_el, "PartyTaxScheme")
        _cbc(tax_scheme, "CompanyID", party.vat)
        scheme = _cac(tax_scheme, "TaxScheme")
        _cbc(scheme, "ID", "VAT")

    # PartyLegalEntity is mandatory in EN 16931 (BG-4 requires BT-27 Seller
    # name / BG-7 requires BT-44 Buyer name as legal name).
    legal = _cac(party_el, "PartyLegalEntity")
    _cbc(legal, "RegistrationName", name)
    if party.tax_id:
        _cbc(legal, "CompanyID", party.tax_id)


def _add_tax_subtotals(
    parent: ET.Element, lines: Iterable[InvoiceLine], currency: str
) -> Decimal:
    """Emit per-rate TaxSubtotal entries; return total tax amount.

    EN 16931 BG-23 requires one TaxSubtotal per (category, rate) pair
    actually used by the invoice lines.
    """
    by_rate: dict[Decimal, dict[str, Decimal]] = {}
    for line in lines:
        rate = line.vat_rate
        bucket = by_rate.setdefault(
            rate, {"taxable": Decimal("0"), "tax": Decimal("0")}
        )
        bucket["taxable"] += line.line_total
        bucket["tax"] += (line.line_total * rate / Decimal("100"))

    total_tax = Decimal("0")
    for rate, bucket in sorted(by_rate.items()):
        taxable = bucket["taxable"]
        tax = bucket["tax"]
        total_tax += tax

        subtotal = _cac(parent, "TaxSubtotal")
        _amount(subtotal, "TaxableAmount", taxable, currency)
        _amount(subtotal, "TaxAmount", tax, currency)
        category = _cac(subtotal, "TaxCategory")
        _cbc(category, "ID", _tax_category_for(rate))
        _cbc(category, "Percent", _percent(rate))
        scheme = _cac(category, "TaxScheme")
        _cbc(scheme, "ID", "VAT")
    return total_tax


def _add_invoice_lines(
    parent: ET.Element,
    lines: Iterable[InvoiceLine],
    currency: str,
    *,
    credit_note: bool,
) -> None:
    line_tag = "CreditNoteLine" if credit_note else "InvoiceLine"
    qty_tag = "CreditedQuantity" if credit_note else "InvoicedQuantity"
    for index, line in enumerate(lines, start=1):
        line_el = _cac(parent, line_tag)
        _cbc(line_el, "ID", str(index))

        qty_el = _cbc(line_el, qty_tag, _qty(line.quantity))
        qty_el.set("unitCode", DEFAULT_UNIT_CODE)

        _amount(line_el, "LineExtensionAmount", line.line_total, currency)

        item = _cac(line_el, "Item")
        _cbc(item, "Description", line.description)
        _cbc(item, "Name", line.description)
        if line.sku:
            seller_id = _cac(item, "SellersItemIdentification")
            _cbc(seller_id, "ID", line.sku)
        classified = _cac(item, "ClassifiedTaxCategory")
        _cbc(classified, "ID", _tax_category_for(line.vat_rate))
        _cbc(classified, "Percent", _percent(line.vat_rate))
        scheme = _cac(classified, "TaxScheme")
        _cbc(scheme, "ID", "VAT")

        price = _cac(line_el, "Price")
        _amount(price, "PriceAmount", line.unit_price, currency)


def _add_legal_monetary_total(
    parent: ET.Element, totals: Totals, currency: str
) -> None:
    monetary = _cac(parent, "LegalMonetaryTotal")
    # BT-106: sum of line net amounts.
    _amount(monetary, "LineExtensionAmount", totals.subtotal, currency)
    # BT-109: invoice total without VAT.
    _amount(monetary, "TaxExclusiveAmount", totals.subtotal, currency)
    # BT-112: invoice total with VAT.
    _amount(monetary, "TaxInclusiveAmount", totals.total, currency)
    # BT-115: amount due for payment.
    _amount(monetary, "PayableAmount", totals.total, currency)


def invoice_to_en16931_ubl(invoice: Invoice) -> bytes:
    """Serialize an Invoice into EN 16931-compliant UBL 2.1 XML.

    The root element is `<Invoice>` for commercial invoices/receipts and
    `<CreditNote>` for credit notes, per EN 16931 routing rules.
    """
    _register_namespaces()

    credit_note = _is_credit_note(invoice)
    if credit_note:
        root_ns = NS_CREDIT_NOTE
        root_tag = "CreditNote"
        type_code_tag = "CreditNoteTypeCode"
        type_code_value = "381"
        # Re-register so the default namespace is CreditNote-2 for this doc.
        ET.register_namespace("", NS_CREDIT_NOTE)
    else:
        root_ns = NS_INVOICE
        root_tag = "Invoice"
        type_code_tag = "InvoiceTypeCode"
        type_code_value = "380"

    root = ET.Element(_qn(root_ns, root_tag))

    # Header — BT-24 conformance, BT-1 number, BT-2 issue date, BT-9 due,
    # BT-3 type code, BT-5 currency.
    _cbc(root, "CustomizationID", EN16931_CUSTOMIZATION_ID)
    _cbc(root, "ID", invoice.number)
    _cbc(root, "IssueDate", _to_iso_date(invoice.date))
    if invoice.due_date:
        _cbc(root, "DueDate", _to_iso_date(invoice.due_date))
    _cbc(root, type_code_tag, type_code_value)
    _cbc(root, "DocumentCurrencyCode", invoice.currency)

    _add_party(
        root, "AccountingSupplierParty", invoice.seller,
        fallback_name="Unknown supplier",
    )
    _add_party(
        root, "AccountingCustomerParty", invoice.buyer,
        fallback_name="Unknown customer",
    )

    # BG-22: TaxTotal must come before LegalMonetaryTotal in UBL.
    tax_total = _cac(root, "TaxTotal")
    # Reserve the TaxAmount placeholder; we fill it after summing subtotals.
    tax_amount_el = _amount(tax_total, "TaxAmount", Decimal("0"), invoice.currency)
    summed_tax = _add_tax_subtotals(tax_total, invoice.lines, invoice.currency)
    # Prefer the explicitly-parsed tax total if present and non-zero; fall
    # back to the per-rate sum so the document always balances.
    declared_tax = invoice.totals.tax
    tax_amount_el.text = _q2(declared_tax if declared_tax != 0 else summed_tax)

    _add_legal_monetary_total(root, invoice.totals, invoice.currency)

    _add_invoice_lines(
        root, invoice.lines, invoice.currency, credit_note=credit_note
    )

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
