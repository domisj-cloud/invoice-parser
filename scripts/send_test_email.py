#!/usr/bin/env python3
"""Send a test email with a PDF attachment to the local Inbucket SMTP server.

Usage:
    python3 scripts/send_test_email.py path/to/invoice.pdf
    python3 scripts/send_test_email.py path/to/invoice.pdf --to invoices@inbucket.local

The local mail server is the `mailbox` service from docker-compose.yml
(Inbucket on localhost:2500). Inbucket is a catch-all dev mail server, so
any address works — mail addressed to `invoices@anything` will land in the
mailbox named `invoices`, which is the same mailbox NiFi's ConsumePOP3
processor polls.
"""
from __future__ import annotations

import argparse
import mimetypes
import smtplib
import sys
import uuid
from email.message import EmailMessage
from pathlib import Path

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 2500
DEFAULT_TO = "invoices@inbucket.local"
DEFAULT_FROM = "supplier@example.com"


def send(pdf_path: Path, to_addr: str, from_addr: str, host: str, port: int,
         subject: str | None) -> None:
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        print(f"Warning: {pdf_path.name} does not have a .pdf extension", file=sys.stderr)

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject or f"Invoice {pdf_path.stem}"
    msg["Message-ID"] = f"<{uuid.uuid4()}@invoice-parser.local>"
    msg.set_content(
        "Hi,\n\nPlease find the attached invoice.\n\n"
        "-- This is a test email sent by scripts/send_test_email.py\n"
    )

    ctype, _ = mimetypes.guess_type(str(pdf_path))
    maintype, subtype = (ctype or "application/pdf").split("/", 1)
    data = pdf_path.read_bytes()
    msg.add_attachment(
        data, maintype=maintype, subtype=subtype, filename=pdf_path.name
    )

    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.send_message(msg)

    print(
        f"Sent {pdf_path.name} ({len(data)} bytes) to {to_addr} "
        f"via {host}:{port}"
    )
    mailbox = to_addr.split("@", 1)[0]
    print(
        "Inbox UI:  http://localhost:9090/monitor\n"
        f"Mailbox:   http://localhost:9090/m/{mailbox}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", type=Path, help="Path to the PDF invoice to attach")
    parser.add_argument("--to", default=DEFAULT_TO,
                        help=f"Recipient address (default: {DEFAULT_TO})")
    parser.add_argument("--from", dest="from_addr", default=DEFAULT_FROM,
                        help=f"Sender address (default: {DEFAULT_FROM})")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"SMTP host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"SMTP port (default: {DEFAULT_PORT})")
    parser.add_argument("--subject", default=None,
                        help="Email subject (default: 'Invoice <pdf-stem>')")
    args = parser.parse_args()
    send(args.pdf, args.to, args.from_addr, args.host, args.port, args.subject)


if __name__ == "__main__":
    main()
