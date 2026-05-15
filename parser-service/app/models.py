from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Party(BaseModel):
    name: str = ""
    vat: Optional[str] = None
    tax_id: Optional[str] = None


class InvoiceLine(BaseModel):
    sku: Optional[str] = None
    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal
    line_total: Decimal


class Totals(BaseModel):
    subtotal: Decimal
    tax: Decimal
    total: Decimal


class Invoice(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_type: str = Field(alias="type")
    number: str
    date: str
    due_date: str = ""
    currency: str
    seller: Party
    buyer: Party
    lines: list[InvoiceLine]
    totals: Totals


class InvoiceUploadEvent(BaseModel):
    bucket: str | None = None
    object_key: str
    output_bucket: str | None = None


class ParseResult(BaseModel):
    job_id: str
    input_bucket: str
    input_object_key: str
    output_bucket: str
    output_object_key: str
    invoice_number: str
    document_type: str
    line_count: int
