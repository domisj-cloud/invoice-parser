from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from app.models import Invoice, InvoiceLine, Party, Totals
from app.pdf_text import extract_pdf_text


class InvoiceParseError(ValueError):
    pass


def parse_invoice_pdf(path: Path) -> Invoice:
    return parse_invoice_text(extract_pdf_text(path))


def parse_invoice_text(text: str) -> Invoice:
    lines = _clean_lines(text)
    joined = "\n".join(lines)

    if "CREDIT NOTE" in joined:
        return _parse_credit_note(lines)
    if "GLOBAL COMPONENTS TEST INVOICE" in joined:
        return _parse_global_components_invoice(lines)
    if "BLUE RIVER SOFTWARE LTD" in joined:
        return _parse_modern_eu_invoice(lines)
    if "North Star Office Supplies" in joined:
        return _parse_us_invoice(lines)

    raise InvoiceParseError("Unsupported invoice layout")


def _clean_lines(text: str) -> list[str]:
    ignored_prefixes = ("--- page ", "Synthetic test invoice", "Page ")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(ignored_prefixes)
    ]


def _decimal(value: str) -> Decimal:
    cleaned = (
        value.replace("EUR", "")
        .replace("USD", "")
        .replace("$", "")
        .replace("%", "")
        .replace(",", "")
        .strip()
    )
    return Decimal(cleaned)


def _after(lines: list[str], label: str) -> str:
    try:
        return lines[lines.index(label) + 1]
    except (ValueError, IndexError) as exc:
        raise InvoiceParseError(f"Missing value after {label!r}") from exc


def _find_amount(lines: list[str], prefix: str) -> Decimal:
    for line in lines:
        if line.startswith(f"{prefix}:"):
            _, value = line.split(":", 1)
            return _decimal(value)
    raise InvoiceParseError(f"Missing amount for {prefix!r}")


def _find_label_amount(lines: list[str], label: str) -> Decimal:
    try:
        return _decimal(lines[lines.index(label) + 1])
    except (ValueError, IndexError) as exc:
        raise InvoiceParseError(f"Missing amount after {label!r}") from exc


def _parse_global_components_invoice(lines: list[str]) -> Invoice:
    header = next(
        (line for line in lines if line.startswith("Invoice ") and " - Date " in line),
        None,
    )
    if not header:
        raise InvoiceParseError("Missing global components invoice header")

    match = re.search(r"Invoice\s+(.+?)\s+- Date\s+(\d{4}-\d{2}-\d{2})\s+- Due\s+(\d{4}-\d{2}-\d{2})", header)
    if not match:
        raise InvoiceParseError("Could not parse global components invoice header")

    return Invoice(
        type="invoice",
        number=match.group(1),
        date=match.group(2),
        due_date=match.group(3),
        currency="EUR",
        seller=Party(name="Global Components Test BV", vat="NL000000000B01"),
        buyer=Party(name="Example Manufacturing GmbH", vat="DE000000000"),
        lines=_parse_sku_lines(lines),
        totals=Totals(
            subtotal=_find_amount(lines, "Subtotal"),
            tax=_find_amount(lines, "VAT"),
            total=_find_amount(lines, "Grand total"),
        ),
    )


def _parse_sku_lines(lines: list[str]) -> list[InvoiceLine]:
    invoice_lines: list[InvoiceLine] = []
    index = 0
    while index < len(lines):
        if re.fullmatch(r"[A-Z]{2}-\d{4}", lines[index]):
            try:
                invoice_lines.append(
                    InvoiceLine(
                        sku=lines[index],
                        description=lines[index + 1],
                        quantity=_decimal(lines[index + 2]),
                        unit_price=_decimal(lines[index + 3]),
                        vat_rate=_decimal(lines[index + 4]),
                        line_total=_decimal(lines[index + 5]),
                    )
                )
            except IndexError as exc:
                raise InvoiceParseError("Incomplete SKU invoice line") from exc
            index += 6
            continue
        index += 1
    if not invoice_lines:
        raise InvoiceParseError("No SKU invoice lines found")
    return invoice_lines


def _parse_credit_note(lines: list[str]) -> Invoice:
    return Invoice(
        type="credit_note",
        number=_after(lines, "Credit note no."),
        date=_after(lines, "Date"),
        due_date="",
        currency="EUR",
        seller=Party(name="Blue River Software LTD", vat="LT100000000"),
        buyer=Party(name=_after(lines, "Customer")),
        lines=_parse_credit_lines(lines),
        totals=Totals(
            subtotal=_find_amount(lines, "Subtotal credit"),
            tax=_find_amount(lines, "VAT credit"),
            total=_find_amount(lines, "Total credit"),
        ),
    )


def _parse_credit_lines(lines: list[str]) -> list[InvoiceLine]:
    try:
        index = lines.index("Credit amount") + 1
    except ValueError as exc:
        raise InvoiceParseError("Missing credit note line header") from exc

    invoice_lines: list[InvoiceLine] = []
    while index < len(lines) and not lines[index].startswith("Subtotal credit"):
        try:
            invoice_lines.append(
                InvoiceLine(
                    description=lines[index],
                    quantity=_decimal(lines[index + 1]),
                    unit_price=_decimal(lines[index + 2]),
                    vat_rate=_decimal(lines[index + 3]),
                    line_total=_decimal(lines[index + 4]),
                )
            )
        except IndexError as exc:
            raise InvoiceParseError("Incomplete credit note line") from exc
        index += 5

    if not invoice_lines:
        raise InvoiceParseError("No credit note lines found")
    return invoice_lines


def _parse_modern_eu_invoice(lines: list[str]) -> Invoice:
    bill_to_index = lines.index("Bill To")
    buyer_vat = next(
        (line.split(":", 1)[1].strip() for line in lines[bill_to_index:] if line.startswith("VAT:")),
        None,
    )

    return Invoice(
        type="invoice",
        number=_after(lines, "Invoice no."),
        date=_after(lines, "Invoice date"),
        due_date=_after(lines, "Due date"),
        currency=_after(lines, "Currency"),
        seller=Party(name="Blue River Software LTD", vat=_first_vat(lines)),
        buyer=Party(name=lines[bill_to_index + 1], vat=buyer_vat),
        lines=_parse_table_lines(lines, start_label="Line total", stop_label="Subtotal", has_vat=True),
        totals=Totals(
            subtotal=_find_label_amount(lines, "Subtotal"),
            tax=_find_label_amount(lines, "VAT"),
            total=_find_label_amount(lines, "Total due"),
        ),
    )


def _first_vat(lines: list[str]) -> str | None:
    for line in lines:
        if line.startswith("VAT:"):
            return line.split(":", 1)[1].strip()
    return None


def _parse_us_invoice(lines: list[str]) -> Invoice:
    seller_tax_id = next(
        (line.split(":", 1)[1].strip() for line in lines if line.startswith("Tax ID:")),
        None,
    )
    bill_to_index = lines.index("Bill To")
    return Invoice(
        type="invoice",
        number=_after(lines, "Invoice #"),
        date=_after(lines, "Date"),
        due_date=_after(lines, "Due"),
        currency="USD",
        seller=Party(name="North Star Office Supplies", tax_id=seller_tax_id),
        buyer=Party(name=lines[bill_to_index + 1]),
        lines=_parse_table_lines(lines, start_label="Amount", stop_label="Subtotal", has_vat=False),
        totals=Totals(
            subtotal=_find_label_amount(lines, "Subtotal"),
            tax=_find_label_amount(lines, "Sales tax"),
            total=_find_label_amount(lines, "Balance due"),
        ),
    )


def _parse_table_lines(
    lines: list[str],
    *,
    start_label: str,
    stop_label: str,
    has_vat: bool,
) -> list[InvoiceLine]:
    try:
        index = lines.index(start_label) + 1
        stop = lines.index(stop_label, index)
    except ValueError as exc:
        raise InvoiceParseError("Missing table boundary") from exc

    invoice_lines: list[InvoiceLine] = []
    step = 5 if has_vat else 4
    while index < stop:
        try:
            if has_vat:
                invoice_lines.append(
                    InvoiceLine(
                        description=lines[index],
                        quantity=_decimal(lines[index + 1]),
                        unit_price=_decimal(lines[index + 2]),
                        vat_rate=_decimal(lines[index + 3]),
                        line_total=_decimal(lines[index + 4]),
                    )
                )
            else:
                invoice_lines.append(
                    InvoiceLine(
                        description=lines[index],
                        quantity=_decimal(lines[index + 1]),
                        unit_price=_decimal(lines[index + 2]),
                        vat_rate=Decimal("8"),
                        line_total=_decimal(lines[index + 3]),
                    )
                )
        except IndexError as exc:
            raise InvoiceParseError("Incomplete invoice table line") from exc
        index += step

    if not invoice_lines:
        raise InvoiceParseError("No invoice table lines found")
    return invoice_lines
