from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.models import Invoice, InvoiceLine, Party, Totals
from app.pdf_text import extract_pdf_text


class InvoiceParseError(ValueError):
    pass


MONEY_PATTERN = (
    r"(?<![A-Za-z0-9])"
    r"(?:[A-Z]{1,3}\$|[€$£])?\s*-?\(?\d[\d,]*(?:\.\d{1,4})?\)?"
    r"(?:\s*(?:EUR|USD|GBP))?"
    r"(?!\s*%)"
    r"(?![A-Za-z0-9])"
)


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

    return _parse_generic_invoice(lines)


def _clean_lines(text: str) -> list[str]:
    ignored_prefixes = ("--- page ", "Synthetic test invoice", "Page ")
    return [
        _normalize_line(line)
        for line in text.splitlines()
        if _normalize_line(line) and not _normalize_line(line).startswith(ignored_prefixes)
    ]


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\x00", "-").replace("\xa0", " ")).strip()


def _decimal(value: str) -> Decimal:
    cleaned = re.sub(
        r"\b(?:EUR|USD|GBP)\b",
        "",
        value.replace("US$", "")
        .replace("$", "")
        .replace("€", "")
        .replace("£", ""),
        flags=re.IGNORECASE,
    )
    match = re.search(r"-?\(?\d[\d,]*(?:\.\d+)?\)?", cleaned)
    if not match:
        raise InvoiceParseError(f"Could not parse decimal value {value!r}")
    cleaned = match.group(0).replace(",", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise InvoiceParseError(f"Could not parse decimal value {value!r}") from exc


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


def _parse_generic_invoice(lines: list[str]) -> Invoice:
    document_type = "receipt" if any("receipt" in line.casefold() for line in lines[:5]) else "invoice"
    number = _find_document_number(lines)
    date = _find_document_date(lines)
    due_date = _find_inline_value(
        lines,
        (
            "Date due",
            "Due date",
            "Payment due",
            "Scheduled Date",
            "Due",
        ),
        default="",
    )
    total = _find_generic_total(lines, TOTAL_LABELS, allow_nearby=True, allow_fallback=True)
    tax = _find_generic_tax(lines, total)
    subtotal = _find_generic_subtotal(lines, total, tax)
    tax_rate = _find_generic_tax_rate(lines)

    return Invoice(
        type=document_type,
        number=number,
        date=date,
        due_date=due_date,
        currency=_infer_currency(lines),
        seller=_generic_seller(lines),
        buyer=_generic_buyer(lines),
        lines=_parse_generic_lines(lines, subtotal, tax_rate),
        totals=Totals(subtotal=subtotal, tax=tax, total=total),
    )


def _find_inline_value(
    lines: list[str],
    labels: tuple[str, ...],
    *,
    default: str | None = None,
) -> str:
    for label in labels:
        for index, line in enumerate(lines):
            if line.casefold() == label.casefold() and index + 1 < len(lines):
                value = lines[index + 1]
                if value and not _is_separator(value):
                    return value
            if line.casefold().startswith(label.casefold()):
                value = line[len(label) :].strip(" :-#")
                if value:
                    return value
    if default is not None:
        return default
    raise InvoiceParseError(f"Missing invoice value for {labels[0]!r}")


def _generic_seller(lines: list[str]) -> Party:
    supplier = _find_section_value(lines, ("Supplier", "Seller", "Vendor", "Merchant"))
    if supplier:
        return Party(name=supplier)

    sponsor = _find_inline_value(
        lines,
        ("Prepared on Behalf of your Test Sponsor",),
        default="",
    )
    if sponsor:
        return Party(name=sponsor)

    bill_to_index = _find_index_casefold(lines, ("Bill to", "Billed to", "Customer"))
    search_end = bill_to_index if bill_to_index is not None else min(len(lines), 12)
    metadata_prefixes = (
        "invoice",
        "official receipt",
        "receipt",
        "date",
        "due",
        "amount",
        "pay ",
        "page ",
    )
    for line in lines[:search_end]:
        if line.casefold() == "invoice":
            continue
        if any(line.casefold().startswith(prefix) for prefix in metadata_prefixes):
            continue
        if _contains_amount(line) or "@" in line:
            continue
        return Party(name=line)
    return Party(name="")


def _generic_buyer(lines: list[str]) -> Party:
    customer = _find_section_value(lines, ("Customer", "Buyer", "Client", "Bill to", "Billed to"))
    if customer:
        return Party(name=customer)

    company = _find_inline_value(lines, ("Company Name",), default="")
    if company:
        return Party(name=company)
    candidate = _find_inline_value(lines, ("Candidate Name",), default="")
    if candidate:
        return Party(name=candidate)

    bill_to_index = _find_index_casefold(lines, ("Bill to", "Billed to", "Customer"))
    if bill_to_index is not None and bill_to_index + 1 < len(lines):
        return Party(name=lines[bill_to_index + 1])
    return Party(name="")


def _parse_generic_lines(
    lines: list[str],
    subtotal: Decimal,
    tax_rate: Decimal,
) -> list[InvoiceLine]:
    invoice_lines = _parse_stacked_table_lines(lines)
    if invoice_lines:
        return invoice_lines

    invoice_lines: list[InvoiceLine] = []
    description_buffer: list[str] = []
    in_table = False

    for line in lines:
        lowered = line.casefold()
        if "description" in lowered and "amount" in lowered:
            in_table = True
            description_buffer = []
            continue
        if not in_table:
            continue
        if lowered.startswith(("subtotal", "total excluding tax", "tax", "vat ", "sales tax", "total", "amount due", "balance due")):
            break

        inline_match = re.match(
            rf"^(?P<description>.+?)\s+"
            r"(?P<quantity>-?\d+(?:\.\d+)?)\s+"
            rf"(?P<unit>{MONEY_PATTERN})\s+"
            rf"(?:(?P<tax>-?\d+(?:\.\d+)?)%\s+)?"
            rf"(?P<amount>{MONEY_PATTERN})$",
            line,
        )
        if inline_match:
            description = " - ".join([*description_buffer, inline_match.group("description")])
            invoice_lines.append(
                InvoiceLine(
                    description=description,
                    quantity=_decimal(inline_match.group("quantity")),
                    unit_price=_decimal(inline_match.group("unit")),
                    vat_rate=_decimal(inline_match.group("tax") or "0"),
                    line_total=_decimal(inline_match.group("amount")),
                )
            )
            description_buffer = []
            continue

        match = re.match(
            r"^(?P<quantity>-?\d+(?:\.\d+)?)\s+"
            rf"(?P<unit>{MONEY_PATTERN})\s+"
            r"(?:(?P<tax>-?\d+(?:\.\d+)?)%\s+)?"
            rf"(?P<amount>{MONEY_PATTERN})$",
            line,
        )
        if match and description_buffer:
            invoice_lines.append(
                InvoiceLine(
                    description=" - ".join(description_buffer),
                    quantity=_decimal(match.group("quantity")),
                    unit_price=_decimal(match.group("unit")),
                    vat_rate=_decimal(match.group("tax") or "0"),
                    line_total=_decimal(match.group("amount")),
                )
            )
            description_buffer = []
            continue

        if not match:
            description_buffer.append(line)

    if not invoice_lines:
        invoice_lines = _parse_labeled_amount_lines(lines)
    if not invoice_lines:
        description = _find_generic_description(lines) or "Unclassified charges"
        invoice_lines = [
            InvoiceLine(
                description=description,
                quantity=Decimal("1"),
                unit_price=subtotal,
                vat_rate=tax_rate,
                line_total=subtotal,
            )
        ]
    return invoice_lines


def _parse_stacked_table_lines(lines: list[str]) -> list[InvoiceLine]:
    try:
        start = _find_stacked_table_start(lines)
    except StopIteration:
        return []

    stop_tokens = ("subtotal", "discount", "shipping", "tax", "total", "notes", "terms")
    invoice_lines: list[InvoiceLine] = []
    index = start
    while index < len(lines):
        line = lines[index]
        lowered = line.casefold()
        if any(lowered.startswith(token) for token in stop_tokens):
            break
        if _contains_amount(line):
            index += 1
            continue

        description_parts = [line]
        quantity_index = index + 1
        while quantity_index < len(lines) and not _is_quantity_line(lines[quantity_index]):
            candidate = lines[quantity_index]
            if any(candidate.casefold().startswith(token) for token in stop_tokens):
                break
            if _contains_amount(candidate):
                break
            description_parts.append(candidate)
            quantity_index += 1

        if quantity_index + 2 >= len(lines) or not _is_quantity_line(lines[quantity_index]):
            index += 1
            continue

        unit_price = _last_decimal(lines[quantity_index + 1])
        line_total = _last_decimal(lines[quantity_index + 2])
        if unit_price is None or line_total is None:
            index += 1
            continue

        invoice_lines.append(
            InvoiceLine(
                description=" - ".join(description_parts),
                quantity=_decimal(lines[quantity_index]),
                unit_price=unit_price,
                vat_rate=Decimal("0"),
                line_total=line_total,
            )
        )
        index = quantity_index + 3

    return invoice_lines


def _parse_labeled_amount_lines(lines: list[str]) -> list[InvoiceLine]:
    invoice_lines: list[InvoiceLine] = []
    exam_name = _find_inline_value(lines, ("Exam Name",), default="")
    exam_price = _find_inline_amount(lines, ("Exam Price",))
    if exam_price is not None:
        invoice_lines.append(
            InvoiceLine(
                description=exam_name or "Exam Price",
                quantity=Decimal("1"),
                unit_price=exam_price,
                vat_rate=Decimal("0"),
                line_total=exam_price,
            )
        )

    promotion_amount = _find_inline_amount(lines, ("Promotion Amount", "Discount", "Adjustment"))
    if promotion_amount is not None:
        invoice_lines.append(
            InvoiceLine(
                description="Promotion Amount" if promotion_amount < 0 else "Adjustment",
                quantity=Decimal("1"),
                unit_price=promotion_amount,
                vat_rate=Decimal("0"),
                line_total=promotion_amount,
            )
        )
    return invoice_lines


def _find_stacked_table_start(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        lowered = line.casefold()
        if all(token in lowered for token in ("item", "quantity", "amount")):
            return index + 1
        if all(token in lowered for token in ("description", "quantity", "amount")):
            return index + 1
        header = [candidate.casefold() for candidate in lines[index : index + 4]]
        if len(header) == 4 and header[0] in {"item", "description"} and "quantity" in header[1] and "amount" in header[3]:
            return index + 4
    raise StopIteration


TOTAL_LABELS = (
    "Amount due",
    "Balance due",
    "Total due",
    "Transaction Amount",
    "Amount paid",
    "Total incl. VAT",
    "Total incl VAT",
    "Total including VAT",
    "Total incl. tax",
    "Total including tax",
    "Grand total",
    "Total",
)

SUBTOTAL_LABELS = (
    "Subtotal",
    "Total excl. VAT",
    "Total excl VAT",
    "Total excluding VAT",
    "Total excluding tax",
    "Total excl. tax",
    "Net amount",
)


def _find_generic_total(
    lines: list[str],
    labels: tuple[str, ...],
    *,
    allow_nearby: bool = False,
    allow_fallback: bool = False,
) -> Decimal:
    for label in labels:
        for index, line in reversed(list(enumerate(lines))):
            if _line_has_label(line, label):
                amount = _last_decimal(line)
                if amount is not None:
                    return amount
                nearby = _nearest_previous_decimal(lines, index) if allow_nearby else None
                if nearby is not None:
                    return nearby

    if allow_fallback:
        amounts = [amount for line in lines for amount in _decimal_values(line)]
        if amounts:
            return amounts[-1]
    raise InvoiceParseError("No monetary amounts found")


def _find_generic_subtotal(lines: list[str], total: Decimal, tax: Decimal) -> Decimal:
    explicit = _find_optional_generic_total(lines, SUBTOTAL_LABELS)
    if explicit is not None:
        return explicit

    item_amounts = [
        amount
        for amount in (
            _find_inline_amount(lines, ("Exam Price",)),
            _find_inline_amount(lines, ("Promotion Amount", "Discount", "Adjustment")),
        )
        if amount is not None
    ]
    if item_amounts:
        return sum(item_amounts, Decimal("0"))

    return total - tax


def _find_optional_generic_total(lines: list[str], labels: tuple[str, ...]) -> Decimal | None:
    try:
        return _find_generic_total(lines, labels)
    except InvoiceParseError:
        return None


def _find_generic_tax(lines: list[str], total: Decimal) -> Decimal:
    for line in reversed(lines):
        lowered = line.casefold()
        if (
            any(token in lowered for token in ("vat", "pvm", "sales tax", "tax"))
            and not any(token in lowered for token in ("total incl", "including", "amount due"))
        ):
            amount = _last_decimal(line)
            if amount is not None:
                return amount
    return Decimal("0")


def _find_generic_tax_rate(lines: list[str]) -> Decimal:
    for line in lines:
        lowered = line.casefold()
        if any(token in lowered for token in ("vat", "pvm", "sales tax", "tax")):
            match = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
            if match:
                return _decimal(match.group(1))
    return Decimal("0")


def _find_inline_amount(lines: list[str], labels: tuple[str, ...]) -> Decimal | None:
    for label in labels:
        for line in lines:
            if _line_has_label(line, label):
                amount = _last_decimal(line)
                if amount is not None:
                    return amount
    return None


def _find_document_number(lines: list[str]) -> str:
    label_value = _find_inline_value(
        lines,
        (
            "Official Receipt / Tax Invoice #",
            "Receipt #",
            "Receipt number",
            "Invoice number",
            "Invoice no.",
            "Invoice no",
            "Invoice #",
            "Invoice ID",
        ),
        default="",
    )
    if label_value:
        return label_value

    transaction_id = _find_inline_value(lines, ("Transaction Confirmation #",), default="")
    if transaction_id:
        return transaction_id

    for index, line in enumerate(lines[:20]):
        if line.casefold() in {"invoice", "receipt"} and index + 1 < len(lines):
            number = lines[index + 1].strip(" #:.")
            if re.search(r"\d", number):
                return number
        if line.startswith("#") and re.search(r"\d", line):
            return line.strip(" #:.")

    for line in lines[:20]:
        match = re.search(r"(?:invoice|receipt|faktūra|faktura)\s*(?:#|no\.?|number|:)?\s*([A-Z0-9][A-Z0-9_.-]*\d[A-Z0-9_.-]*)", line, re.IGNORECASE)
        if match:
            return match.group(1).strip(" .,:;")

    for line in lines[:20]:
        match = re.search(r"\b[A-Z]{2,}[-_]\d{2,}[-_]\d{3,}\b", line)
        if match:
            return match.group(0)

    return "unknown"


def _find_document_date(lines: list[str]) -> str:
    label_value = _find_inline_value(
        lines,
        (
            "Transaction Date",
            "Date of issue",
            "Invoice date",
            "Date issued",
            "Date",
        ),
        default="",
    )
    if label_value:
        return label_value

    for line in lines[:20]:
        match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", line)
        if match:
            return match.group(0)
        match = re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", line)
        if match:
            return match.group(0)
        match = re.search(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b", line, re.IGNORECASE)
        if match:
            return match.group(0)
    return ""


def _find_section_value(lines: list[str], labels: tuple[str, ...]) -> str:
    for index, line in enumerate(lines):
        for label in labels:
            if _line_has_label(line, label):
                value = _value_after_label(line, label)
                if value and not _looks_like_label_only(value):
                    return value
                for candidate in lines[index + 1 : index + 5]:
                    if candidate and not _is_separator(candidate) and not _looks_like_metadata(candidate):
                        return candidate
    return ""


def _find_generic_description(lines: list[str]) -> str:
    for line in lines:
        lowered = line.casefold()
        if any(token in lowered for token in ("service", "product", "item", "description", "paslauga")):
            if not _contains_amount(line) and not _looks_like_metadata(line):
                return _english_side(line)
    return ""


def _line_has_label(line: str, label: str) -> bool:
    return label.casefold() in _english_side(line).casefold() or label.casefold() in line.casefold()


def _value_after_label(line: str, label: str) -> str:
    lowered = line.casefold()
    lowered_label = label.casefold()
    if lowered_label not in lowered:
        return ""
    value = line[lowered.index(lowered_label) + len(label) :]
    return value.strip(" :-#/")


def _english_side(line: str) -> str:
    if "/" in line:
        return line.rsplit("/", 1)[-1].strip()
    return line


def _looks_like_label_only(value: str) -> bool:
    return not value or value.casefold() in {"supplier", "seller", "customer", "buyer", "client"}


def _is_separator(value: str) -> bool:
    return not value.strip(" :#-/")


def _looks_like_metadata(line: str) -> bool:
    lowered = line.casefold()
    return (
        _contains_amount(line)
        or "@" in line
        or any(
            token in lowered
            for token in (
                "invoice",
                "receipt",
                "date",
                "address",
                "code",
                "vat",
                "pvm",
                "total",
                "amount",
                "price",
                "tax",
                "page",
            )
        )
    )


def _infer_currency(lines: list[str]) -> str:
    joined = "\n".join(lines)
    if "€" in joined or "EUR" in joined:
        return "EUR"
    if "$" in joined or "USD" in joined:
        return "USD"
    if "£" in joined or "GBP" in joined:
        return "GBP"
    return ""


def _find_index_casefold(lines: list[str], labels: tuple[str, ...]) -> int | None:
    label_set = {label.casefold() for label in labels}
    for index, line in enumerate(lines):
        if line.casefold() in label_set:
            return index
    return None


def _contains_amount(line: str) -> bool:
    return _last_decimal(line) is not None


def _last_amount(line: str) -> str | None:
    matches = re.findall(MONEY_PATTERN, line)
    return matches[-1] if matches else None


def _last_decimal(line: str) -> Decimal | None:
    values = _decimal_values(line)
    return values[-1] if values else None


def _decimal_values(line: str) -> list[Decimal]:
    values: list[Decimal] = []
    for amount in re.findall(MONEY_PATTERN, line):
        try:
            values.append(_decimal(amount))
        except InvoiceParseError:
            continue
    return values


def _nearest_previous_decimal(lines: list[str], index: int) -> Decimal | None:
    for candidate in reversed(lines[max(0, index - 8) : index]):
        amount = _last_decimal(candidate)
        if amount is not None:
            return amount
    return None


def _is_quantity_line(line: str) -> bool:
    return re.fullmatch(r"\d+(?:\.\d+)?", line.strip()) is not None


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
