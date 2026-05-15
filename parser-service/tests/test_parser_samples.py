from __future__ import annotations

import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app.parser import parse_invoice_pdf
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


def _node(element: ET.Element) -> tuple[str, str, list[object]]:
    return (
        element.tag,
        (element.text or "").strip(),
        [_node(child) for child in list(element)],
    )
