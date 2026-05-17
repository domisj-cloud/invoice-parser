"""Unit tests for scripts/send_test_email.py.

Uses an in-process stub SMTP client (monkeypatched into smtplib) so the
test runs without any real mail server.
"""
from __future__ import annotations

import email
import importlib
import sys
from pathlib import Path

import pytest

# Make `scripts/` importable from the parser-service tests.
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def send_module():
    """Reload the module fresh per test so monkeypatches don't leak."""
    if "send_test_email" in sys.modules:
        del sys.modules["send_test_email"]
    return importlib.import_module("send_test_email")


@pytest.fixture
def minimal_pdf(tmp_path: Path) -> Path:
    """A tiny but recognisable PDF file."""
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
    path = tmp_path / "fixture-invoice.pdf"
    path.write_bytes(pdf_bytes)
    return path


class StubSMTP:
    """Minimal smtplib.SMTP stand-in that records the last sent message."""

    instances: list["StubSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sent: list[email.message.Message] = []
        StubSMTP.instances.append(self)

    def send_message(self, msg: email.message.Message) -> None:
        self.sent.append(msg)

    def __enter__(self) -> "StubSMTP":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.fixture(autouse=True)
def reset_stub_smtp():
    StubSMTP.instances.clear()
    yield
    StubSMTP.instances.clear()


def test_send_targets_configured_host_and_port(send_module, monkeypatch, minimal_pdf):
    monkeypatch.setattr(send_module.smtplib, "SMTP", StubSMTP)

    send_module.send(
        pdf_path=minimal_pdf,
        to_addr="invoices@inbucket.local",
        from_addr="supplier@example.com",
        host="localhost",
        port=2500,
        subject=None,
    )

    assert len(StubSMTP.instances) == 1
    smtp = StubSMTP.instances[0]
    assert smtp.host == "localhost"
    assert smtp.port == 2500
    assert len(smtp.sent) == 1


def test_send_message_headers_and_subject(send_module, monkeypatch, minimal_pdf):
    monkeypatch.setattr(send_module.smtplib, "SMTP", StubSMTP)

    send_module.send(
        pdf_path=minimal_pdf,
        to_addr="invoices@inbucket.local",
        from_addr="supplier@example.com",
        host="localhost",
        port=2500,
        subject=None,
    )

    msg = StubSMTP.instances[0].sent[0]
    assert msg["From"] == "supplier@example.com"
    assert msg["To"] == "invoices@inbucket.local"
    # Default subject is derived from the PDF stem.
    assert msg["Subject"] == f"Invoice {minimal_pdf.stem}"
    # Message-ID is set, unique enough to not be empty.
    assert msg["Message-ID"]
    assert msg["Message-ID"].startswith("<") and msg["Message-ID"].endswith(">")


def test_send_explicit_subject(send_module, monkeypatch, minimal_pdf):
    monkeypatch.setattr(send_module.smtplib, "SMTP", StubSMTP)

    send_module.send(
        pdf_path=minimal_pdf,
        to_addr="invoices@inbucket.local",
        from_addr="supplier@example.com",
        host="localhost",
        port=2500,
        subject="Q4 invoice",
    )

    assert StubSMTP.instances[0].sent[0]["Subject"] == "Q4 invoice"


def test_send_attaches_pdf_with_filename_and_content(
    send_module, monkeypatch, minimal_pdf
):
    monkeypatch.setattr(send_module.smtplib, "SMTP", StubSMTP)

    send_module.send(
        pdf_path=minimal_pdf,
        to_addr="invoices@inbucket.local",
        from_addr="supplier@example.com",
        host="localhost",
        port=2500,
        subject=None,
    )

    msg = StubSMTP.instances[0].sent[0]
    attachments = [p for p in msg.iter_attachments()]
    assert len(attachments) == 1

    attachment = attachments[0]
    assert attachment.get_filename() == minimal_pdf.name
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_payload(decode=True) == minimal_pdf.read_bytes()


def test_send_raises_when_pdf_missing(send_module, monkeypatch, tmp_path):
    monkeypatch.setattr(send_module.smtplib, "SMTP", StubSMTP)

    missing = tmp_path / "nope.pdf"
    with pytest.raises(SystemExit):
        send_module.send(
            pdf_path=missing,
            to_addr="invoices@inbucket.local",
            from_addr="supplier@example.com",
            host="localhost",
            port=2500,
            subject=None,
        )

    # SMTP must not even be opened on failure.
    assert StubSMTP.instances == []


def test_send_warns_for_non_pdf_extension(
    send_module, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(send_module.smtplib, "SMTP", StubSMTP)

    txt = tmp_path / "invoice.txt"
    txt.write_bytes(b"not really a pdf")

    send_module.send(
        pdf_path=txt,
        to_addr="invoices@inbucket.local",
        from_addr="supplier@example.com",
        host="localhost",
        port=2500,
        subject=None,
    )

    captured = capsys.readouterr()
    assert "does not have a .pdf extension" in captured.err
    # Still sent — the script is intentionally lenient.
    assert StubSMTP.instances[0].sent[0]
