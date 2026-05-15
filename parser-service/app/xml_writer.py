from __future__ import annotations

from decimal import Decimal
from xml.etree import ElementTree as ET

from app.models import Invoice


def _format_decimal(value: Decimal, tag: str) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        if tag in {"quantity", "vat_rate"}:
            return f"{normalized:.0f}"
        return f"{normalized:.1f}"
    return format(normalized, "f")


def _child(parent: ET.Element, tag: str, text: object | None = None) -> ET.Element:
    element = ET.SubElement(parent, tag)
    if text is not None:
        if isinstance(text, Decimal):
            element.text = _format_decimal(text, tag)
        else:
            element.text = str(text)
    return element


def invoice_to_xml(invoice: Invoice) -> bytes:
    root = ET.Element("Invoice")
    _child(root, "type", invoice.document_type)
    _child(root, "number", invoice.number)
    _child(root, "date", invoice.date)
    _child(root, "due_date", invoice.due_date)
    _child(root, "currency", invoice.currency)

    seller = _child(root, "Seller")
    _child(seller, "name", invoice.seller.name)
    if invoice.seller.vat:
        _child(seller, "vat", invoice.seller.vat)
    if invoice.seller.tax_id:
        _child(seller, "tax_id", invoice.seller.tax_id)

    buyer = _child(root, "Buyer")
    _child(buyer, "name", invoice.buyer.name)
    if invoice.buyer.vat:
        _child(buyer, "vat", invoice.buyer.vat)
    if invoice.buyer.tax_id:
        _child(buyer, "tax_id", invoice.buyer.tax_id)

    lines = _child(root, "Lines")
    for invoice_line in invoice.lines:
        line = _child(lines, "Line")
        if invoice_line.sku:
            _child(line, "sku", invoice_line.sku)
        _child(line, "description", invoice_line.description)
        _child(line, "quantity", invoice_line.quantity)
        _child(line, "unit_price", invoice_line.unit_price)
        _child(line, "vat_rate", invoice_line.vat_rate)
        _child(line, "line_total", invoice_line.line_total)

    totals = _child(root, "Totals")
    _child(totals, "subtotal", invoice.totals.subtotal)
    _child(totals, "tax", invoice.totals.tax)
    _child(totals, "total", invoice.totals.total)

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
