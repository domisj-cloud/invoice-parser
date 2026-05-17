"""Public entry point for invoice XML serialization.

The parser writes invoice output in the EN 16931-compliant UBL 2.1
format. See `app.serializers.en16931_ubl` for the binding mapping and
known limitations.
"""
from __future__ import annotations

from app.models import Invoice
from app.serializers.en16931_ubl import invoice_to_en16931_ubl


def invoice_to_xml(invoice: Invoice) -> bytes:
    """Serialize an Invoice to EN 16931-compliant UBL 2.1 XML."""
    return invoice_to_en16931_ubl(invoice)
