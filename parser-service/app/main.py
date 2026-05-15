from __future__ import annotations

import uuid
import logging
import json
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, HTTPException

from app.config import Settings, get_settings
from app.minio_io import ObjectStore
from app.models import InvoiceUploadEvent, ParseResult
from app.parser import InvoiceParseError, parse_invoice_pdf
from app.xml_writer import invoice_to_xml

app = FastAPI(title="Invoice Parser Service", version="0.1.0")
logger = logging.getLogger(__name__)


def get_object_store(settings: Settings = Depends(get_settings)) -> ObjectStore:
    return ObjectStore(settings)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/events/invoice-uploaded", response_model=ParseResult)
def invoice_uploaded(
    event: InvoiceUploadEvent,
    settings: Settings = Depends(get_settings),
    store: ObjectStore = Depends(get_object_store),
) -> ParseResult:
    input_bucket = event.bucket or settings.input_bucket
    output_bucket = event.output_bucket or settings.output_bucket
    object_key = event.object_key
    output_key = _xml_output_key(object_key)

    work_root = Path(settings.work_dir) / uuid.uuid4().hex
    pdf_path = work_root / "input" / _safe_object_path(object_key)

    try:
        logger.info("Processing invoice object %s/%s", input_bucket, object_key)
        store.download(input_bucket, object_key, pdf_path)
        invoice = parse_invoice_pdf(pdf_path)
        store.upload_bytes(
            output_bucket,
            output_key,
            invoice_to_xml(invoice),
            content_type="application/xml",
        )
    except InvoiceParseError as exc:
        logger.warning(
            "Could not parse invoice object %s/%s: %s",
            input_bucket,
            object_key,
            exc,
        )
        _archive_error(store, settings, pdf_path, object_key, "parse_error", str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to process invoice object %s/%s", input_bucket, object_key)
        if pdf_path.exists():
            _archive_error(store, settings, pdf_path, object_key, "processing_error", str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ParseResult(
        input_bucket=input_bucket,
        input_object_key=object_key,
        output_bucket=output_bucket,
        output_object_key=output_key,
        invoice_number=invoice.number,
        document_type=invoice.document_type,
        line_count=len(invoice.lines),
    )


def _xml_output_key(object_key: str) -> str:
    path = PurePosixPath(object_key)
    if path.suffix:
        return str(path.with_suffix(".xml"))
    return f"{object_key}.xml"


def _archive_error(
    store: ObjectStore,
    settings: Settings,
    pdf_path: Path,
    object_key: str,
    error_type: str,
    message: str,
) -> None:
    error_pdf_key = str(PurePosixPath("failed") / _safe_posix_object_key(object_key))
    error_report_key = f"{error_pdf_key}.error.json"
    report = {
        "input_bucket": settings.input_bucket,
        "input_object_key": object_key,
        "error_type": error_type,
        "message": message,
        "policy": "Input object is left in place. A copy is archived in the error bucket when available.",
    }
    try:
        store.upload_file(
            settings.error_bucket,
            error_pdf_key,
            pdf_path,
            content_type="application/pdf",
        )
        store.upload_bytes(
            settings.error_bucket,
            error_report_key,
            json.dumps(report, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        logger.info(
            "Archived failed invoice object to %s/%s",
            settings.error_bucket,
            error_pdf_key,
        )
    except Exception:
        logger.exception(
            "Could not archive failed invoice object %s to error bucket %s",
            object_key,
            settings.error_bucket,
        )


def _safe_posix_object_key(object_key: str) -> str:
    parts = [
        part
        for part in PurePosixPath(object_key).parts
        if part not in {"", ".", "..", "/"}
    ]
    return str(PurePosixPath(*parts)) if parts else "invoice.pdf"


def _safe_object_path(object_key: str) -> Path:
    parts = [
        part
        for part in PurePosixPath(object_key).parts
        if part not in {"", ".", "..", "/"}
    ]
    if not parts:
        return Path("invoice.pdf")
    return Path(*parts)
