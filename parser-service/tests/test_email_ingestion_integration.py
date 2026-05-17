"""Integration test for the Inbucket mailbox.

Verifies that an email sent via the local SMTP server with a PDF
attachment shows up in the `invoices` mailbox via Inbucket's HTTP API.

The test is skipped automatically when Inbucket is not reachable
(e.g. CI without docker-compose, or the developer hasn't run
`./scripts/start_services.sh` yet).

Run only this test:

    pytest -q tests/test_email_ingestion_integration.py
"""
from __future__ import annotations

import importlib
import json
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SMTP_HOST = "localhost"
SMTP_PORT = 2500
WEB_BASE = "http://localhost:9090"
TEST_MAILBOX = "pytest-invoices"  # Isolated from the demo `invoices` box.


def _tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


pytestmark = pytest.mark.skipif(
    not (_tcp_open(SMTP_HOST, SMTP_PORT) and _http_ok(f"{WEB_BASE}/")),
    reason="Inbucket is not running locally (docker compose up mailbox)",
)


@pytest.fixture
def send_module():
    if "send_test_email" in sys.modules:
        del sys.modules["send_test_email"]
    return importlib.import_module("send_test_email")


@pytest.fixture
def minimal_pdf(tmp_path: Path) -> Path:
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj "
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f\n"
        b"0000000009 00000 n\n"
        b"0000000052 00000 n\n"
        b"0000000101 00000 n\n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n173\n%%EOF\n"
    )
    path = tmp_path / "integration-invoice.pdf"
    path.write_bytes(pdf_bytes)
    return path


def _purge_mailbox(mailbox: str) -> None:
    """Delete every message in `mailbox`. Best-effort; ignores 404s."""
    listing = _get_mailbox(mailbox)
    for msg in listing:
        req = urllib.request.Request(
            f"{WEB_BASE}/api/v1/mailbox/{mailbox}/{msg['id']}",
            method="DELETE",
        )
        try:
            urllib.request.urlopen(req, timeout=2.0).read()
        except urllib.error.HTTPError:
            pass


def _get_mailbox(mailbox: str) -> list[dict]:
    with urllib.request.urlopen(
        f"{WEB_BASE}/api/v1/mailbox/{mailbox}", timeout=2.0
    ) as resp:
        return json.loads(resp.read().decode())


def _get_message(mailbox: str, message_id: str) -> dict:
    with urllib.request.urlopen(
        f"{WEB_BASE}/api/v1/mailbox/{mailbox}/{message_id}", timeout=2.0
    ) as resp:
        return json.loads(resp.read().decode())


def _wait_for_message(mailbox: str, subject: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for entry in _get_mailbox(mailbox):
            if entry.get("subject") == subject:
                return _get_message(mailbox, entry["id"])
        time.sleep(0.2)
    raise AssertionError(
        f"Email with subject {subject!r} did not arrive in {mailbox!r} within "
        f"{timeout}s"
    )


@pytest.fixture
def clean_mailbox():
    _purge_mailbox(TEST_MAILBOX)
    yield TEST_MAILBOX
    _purge_mailbox(TEST_MAILBOX)


def test_end_to_end_email_with_pdf_attachment(
    send_module, minimal_pdf, clean_mailbox
):
    subject = f"pytest invoice {uuid.uuid4()}"

    send_module.send(
        pdf_path=minimal_pdf,
        to_addr=f"{clean_mailbox}@inbucket.local",
        from_addr="pytest-supplier@example.com",
        host=SMTP_HOST,
        port=SMTP_PORT,
        subject=subject,
    )

    message = _wait_for_message(clean_mailbox, subject)

    # Inbucket exposes the parsed message including header + body + attachments.
    # The exact shape varies slightly between Inbucket versions, so check
    # both the conservative "header.Subject" and the flat "subject" keys.
    assert message.get("subject") == subject or message.get(
        "header", {}
    ).get("Subject", [""])[0] == subject

    # Attachment surface — Inbucket reports attachments under
    # `attachments` (3.x) — each entry has filename and content-type.
    attachments = message.get("attachments") or []
    assert any(
        att.get("filename") == minimal_pdf.name
        and att.get("content-type", "").startswith("application/pdf")
        for att in attachments
    ), f"Expected PDF attachment {minimal_pdf.name!r} not found in {attachments!r}"


def test_catch_all_routing_uses_local_part(send_module, minimal_pdf):
    """Inbucket should route mail to the mailbox named after the local-part,
    regardless of the domain in the To: address."""
    mailbox = f"pytest-routing-{uuid.uuid4().hex[:8]}"
    _purge_mailbox(mailbox)
    try:
        subject = f"routing test {uuid.uuid4()}"
        send_module.send(
            pdf_path=minimal_pdf,
            to_addr=f"{mailbox}@some-random-domain.example",
            from_addr="pytest-supplier@example.com",
            host=SMTP_HOST,
            port=SMTP_PORT,
            subject=subject,
        )
        message = _wait_for_message(mailbox, subject)
        assert message is not None
    finally:
        _purge_mailbox(mailbox)
