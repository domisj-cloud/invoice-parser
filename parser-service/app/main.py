from __future__ import annotations

import uuid
import logging
import json
import traceback
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.jobs import JobRepository
from app.minio_io import ObjectStore
from app.models import InvoiceUploadEvent, ParseResult
from app.parser import InvoiceParseError, parse_invoice_pdf
from app.xml_writer import invoice_to_xml

app = FastAPI(title="Invoice Parser Service", version="0.1.0")
logger = logging.getLogger(__name__)


def get_object_store(settings: Settings = Depends(get_settings)) -> ObjectStore:
    return ObjectStore(settings)


def get_job_repository(settings: Settings = Depends(get_settings)) -> JobRepository:
    return JobRepository.from_settings(settings)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML


@app.get("/api/jobs")
def list_jobs(
    limit: int = 100,
    jobs: JobRepository = Depends(get_job_repository),
) -> dict[str, object]:
    limited = max(1, min(limit, 500))
    return {
        "counts": jobs.counts(),
        "jobs": [_with_links(job) for job in jobs.list(limited)],
    }


@app.get("/api/jobs/{job_id}")
def get_job(
    job_id: str,
    jobs: JobRepository = Depends(get_job_repository),
) -> dict[str, object]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _with_links(job)


@app.get("/objects/{job_id}/{kind}")
def object_link(
    job_id: str,
    kind: str,
    settings: Settings = Depends(get_settings),
    store: ObjectStore = Depends(get_object_store),
    jobs: JobRepository = Depends(get_job_repository),
) -> RedirectResponse:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    mapping = {
        "output": ("output_bucket", "output_object_key"),
        "error": ("error_bucket", "error_object_key"),
        "error-report": ("error_bucket", "error_report_object_key"),
        "input": ("input_bucket", "input_object_key"),
    }
    if kind not in mapping:
        raise HTTPException(status_code=404, detail="Unknown object kind")

    bucket_field, key_field = mapping[kind]
    bucket = job.get(bucket_field)
    object_key = job.get(key_field)
    if not bucket or not object_key:
        raise HTTPException(status_code=404, detail="Object link not available")
    return RedirectResponse(store.presigned_get_url(bucket, object_key))


@app.post("/events/invoice-uploaded", response_model=ParseResult)
def invoice_uploaded(
    event: InvoiceUploadEvent,
    settings: Settings = Depends(get_settings),
    store: ObjectStore = Depends(get_object_store),
    jobs: JobRepository = Depends(get_job_repository),
) -> ParseResult:
    job_id = uuid.uuid4().hex
    started_at = datetime.now(UTC)
    input_bucket = event.bucket or settings.input_bucket
    output_bucket = event.output_bucket or settings.output_bucket
    object_key = event.object_key
    output_key = _xml_output_key(object_key)

    work_root = Path(settings.work_dir) / uuid.uuid4().hex
    pdf_path = work_root / "input" / _safe_object_path(object_key)
    jobs.create(job_id=job_id, input_bucket=input_bucket, input_object_key=object_key)

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
        jobs.complete(
            job_id,
            status="SUCCESS",
            started_at=started_at,
            output_bucket=output_bucket,
            output_object_key=output_key,
            invoice_number=invoice.number,
            document_type=invoice.document_type,
            line_count=len(invoice.lines),
        )
    except InvoiceParseError as exc:
        error_log = traceback.format_exc()
        logger.warning(
            "Could not parse invoice object %s/%s: %s",
            input_bucket,
            object_key,
            exc,
        )
        archived = _archive_error(
            store,
            settings,
            pdf_path,
            input_bucket,
            object_key,
            "parse_error",
            str(exc),
        )
        jobs.complete(
            job_id,
            status="PARSE_ERROR",
            started_at=started_at,
            error_bucket=settings.error_bucket if archived else None,
            error_object_key=archived.error_object_key if archived else None,
            error_report_object_key=archived.error_report_object_key if archived else None,
            error_type="parse_error",
            error_message=str(exc),
            error_log=error_log,
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        error_log = traceback.format_exc()
        logger.exception("Failed to process invoice object %s/%s", input_bucket, object_key)
        archived = None
        if pdf_path.exists():
            archived = _archive_error(
                store,
                settings,
                pdf_path,
                input_bucket,
                object_key,
                "processing_error",
                str(exc),
            )
        jobs.complete(
            job_id,
            status="ERROR",
            started_at=started_at,
            error_bucket=settings.error_bucket if archived else None,
            error_object_key=archived.error_object_key if archived else None,
            error_report_object_key=archived.error_report_object_key if archived else None,
            error_type="processing_error",
            error_message=str(exc),
            error_log=error_log,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ParseResult(
        job_id=job_id,
        input_bucket=input_bucket,
        input_object_key=object_key,
        output_bucket=output_bucket,
        output_object_key=output_key,
        invoice_number=invoice.number,
        document_type=invoice.document_type,
        line_count=len(invoice.lines),
    )


class ArchivedError(BaseModel):
    error_object_key: str
    error_report_object_key: str


def _xml_output_key(object_key: str) -> str:
    path = PurePosixPath(object_key)
    if path.suffix:
        return str(path.with_suffix(".xml"))
    return f"{object_key}.xml"


def _archive_error(
    store: ObjectStore,
    settings: Settings,
    pdf_path: Path,
    input_bucket: str,
    object_key: str,
    error_type: str,
    message: str,
) -> ArchivedError | None:
    error_pdf_key = str(PurePosixPath("failed") / _safe_posix_object_key(object_key))
    error_report_key = f"{error_pdf_key}.error.json"
    report = {
        "input_bucket": input_bucket,
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
        return ArchivedError(
            error_object_key=error_pdf_key,
            error_report_object_key=error_report_key,
        )
    except Exception:
        logger.exception(
            "Could not archive failed invoice object %s to error bucket %s",
            object_key,
            settings.error_bucket,
        )
        return None


def _with_links(job: dict[str, object]) -> dict[str, object]:
    linked = dict(job)
    if linked.get("output_bucket") and linked.get("output_object_key"):
        linked["output_url"] = f"/objects/{linked['id']}/output"
    if linked.get("error_bucket") and linked.get("error_object_key"):
        linked["error_url"] = f"/objects/{linked['id']}/error"
    if linked.get("error_bucket") and linked.get("error_report_object_key"):
        linked["error_report_url"] = f"/objects/{linked['id']}/error-report"
    if linked.get("input_bucket") and linked.get("input_object_key"):
        linked["input_url"] = f"/objects/{linked['id']}/input"
    return linked


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


DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Invoice Parser</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --line: #d8dde6;
      --muted: #657083;
      --text: #18202f;
      --success: #0f7a4d;
      --success-bg: #e8f6ef;
      --error: #b42318;
      --error-bg: #fdeceb;
      --processing: #7a5400;
      --processing-bg: #fff4d8;
      --link: #1456cc;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
    }
    header {
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 {
      font-size: 18px;
      line-height: 24px;
      margin: 0;
      font-weight: 650;
    }
    main {
      padding: 20px 24px 32px;
      max-width: 1440px;
      margin: 0 auto;
    }
    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 16px;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(130px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .stat {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
    }
    .stat span {
      color: var(--muted);
      display: block;
      font-size: 12px;
      line-height: 16px;
      margin-bottom: 4px;
    }
    .stat strong {
      font-size: 22px;
      line-height: 28px;
    }
    button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      height: 34px;
      padding: 0 12px;
      cursor: pointer;
      font: inherit;
    }
    button:hover { border-color: #aab4c3; }
    .table-wrap {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th {
      background: #f1f4f8;
      color: #394458;
      font-size: 12px;
      font-weight: 650;
      line-height: 16px;
    }
    tr:last-child td { border-bottom: 0; }
    .status {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      padding: 2px 9px;
      font-size: 12px;
      font-weight: 650;
      white-space: nowrap;
    }
    .SUCCESS { color: var(--success); background: var(--success-bg); }
    .ERROR, .PARSE_ERROR { color: var(--error); background: var(--error-bg); }
    .PROCESSING { color: var(--processing); background: var(--processing-bg); }
    .muted { color: var(--muted); }
    .links {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    a {
      color: var(--link);
      text-decoration: none;
      font-weight: 550;
    }
    a:hover { text-decoration: underline; }
    .empty {
      padding: 32px;
      color: var(--muted);
      text-align: center;
    }
    dialog {
      width: min(980px, calc(100vw - 40px));
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0;
      box-shadow: 0 20px 60px rgba(17, 24, 39, 0.18);
    }
    dialog::backdrop { background: rgba(15, 23, 42, 0.32); }
    .dialog-head {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
    }
    .dialog-body { padding: 16px; }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: #111827;
      color: #f8fafc;
      border-radius: 6px;
      padding: 12px;
      max-height: 520px;
      overflow: auto;
      font-size: 12px;
      line-height: 18px;
    }
    @media (max-width: 900px) {
      main { padding: 16px; }
      header { padding: 14px 16px; }
      .stats { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      table { min-width: 980px; }
      .table-wrap { overflow-x: auto; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Invoice Parser</h1>
    <button id="refresh" type="button">Refresh</button>
  </header>
  <main>
    <section class="stats" aria-label="Processing counters">
      <div class="stat"><span>Total</span><strong id="count-total">0</strong></div>
      <div class="stat"><span>Success</span><strong id="count-success">0</strong></div>
      <div class="stat"><span>Failed</span><strong id="count-failed">0</strong></div>
      <div class="stat"><span>Processing</span><strong id="count-processing">0</strong></div>
    </section>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th style="width: 16%">Started</th>
            <th style="width: 11%">Status</th>
            <th style="width: 20%">Input</th>
            <th style="width: 12%">Duration</th>
            <th style="width: 14%">Invoice</th>
            <th style="width: 15%">Objects</th>
            <th style="width: 12%">Error</th>
          </tr>
        </thead>
        <tbody id="jobs"></tbody>
      </table>
      <div id="empty" class="empty" hidden>No processed invoices yet.</div>
    </div>
  </main>

  <dialog id="error-dialog">
    <div class="dialog-head">
      <strong id="dialog-title">Error log</strong>
      <button id="close-dialog" type="button">Close</button>
    </div>
    <div class="dialog-body">
      <p id="dialog-message" class="muted"></p>
      <pre id="dialog-log"></pre>
    </div>
  </dialog>

  <script>
    const jobsBody = document.querySelector("#jobs");
    const empty = document.querySelector("#empty");
    const dialog = document.querySelector("#error-dialog");
    const dialogTitle = document.querySelector("#dialog-title");
    const dialogMessage = document.querySelector("#dialog-message");
    const dialogLog = document.querySelector("#dialog-log");
    let latestJobs = [];

    function fmtDate(value) {
      if (!value) return "";
      return new Date(value).toLocaleString();
    }

    function fmtDuration(value) {
      if (value === null || value === undefined) return "";
      if (value < 1000) return `${value} ms`;
      return `${(value / 1000).toFixed(2)} s`;
    }

    function objectLinks(job) {
      const links = [];
      if (job.input_url) links.push(`<a href="${job.input_url}" target="_blank">input</a>`);
      if (job.output_url) links.push(`<a href="${job.output_url}" target="_blank">output XML</a>`);
      if (job.error_url) links.push(`<a href="${job.error_url}" target="_blank">error PDF</a>`);
      if (job.error_report_url) links.push(`<a href="${job.error_report_url}" target="_blank">error JSON</a>`);
      return links.length ? `<div class="links">${links.join("")}</div>` : `<span class="muted">None</span>`;
    }

    function render(data) {
      latestJobs = data.jobs || [];
      const counts = data.counts || {};
      const success = counts.SUCCESS || 0;
      const processing = counts.PROCESSING || 0;
      const failed = (counts.ERROR || 0) + (counts.PARSE_ERROR || 0);
      const total = latestJobs.length;
      document.querySelector("#count-total").textContent = total;
      document.querySelector("#count-success").textContent = success;
      document.querySelector("#count-failed").textContent = failed;
      document.querySelector("#count-processing").textContent = processing;

      empty.hidden = latestJobs.length !== 0;
      jobsBody.innerHTML = latestJobs.map((job) => {
        const invoice = job.invoice_number
          ? `${job.invoice_number}<br><span class="muted">${job.document_type || ""} · ${job.line_count || 0} lines</span>`
          : `<span class="muted">-</span>`;
        const errorButton = job.error_log
          ? `<button type="button" data-job="${job.id}">View log</button>`
          : `<span class="muted">-</span>`;
        return `
          <tr>
            <td>${fmtDate(job.started_at)}</td>
            <td><span class="status ${job.status}">${job.status}</span></td>
            <td>${job.input_bucket}/${job.input_object_key}</td>
            <td>${fmtDuration(job.duration_ms)}</td>
            <td>${invoice}</td>
            <td>${objectLinks(job)}</td>
            <td>${errorButton}</td>
          </tr>
        `;
      }).join("");

      jobsBody.querySelectorAll("button[data-job]").forEach((button) => {
        button.addEventListener("click", () => showError(button.dataset.job));
      });
    }

    function showError(jobId) {
      const job = latestJobs.find((item) => item.id === jobId);
      if (!job) return;
      dialogTitle.textContent = `${job.status}: ${job.input_object_key}`;
      dialogMessage.textContent = job.error_message || "";
      dialogLog.textContent = job.error_log || "No error log recorded.";
      dialog.showModal();
    }

    async function refresh() {
      const response = await fetch("/api/jobs?limit=200", { cache: "no-store" });
      if (!response.ok) throw new Error(`Dashboard request failed: ${response.status}`);
      render(await response.json());
    }

    document.querySelector("#refresh").addEventListener("click", refresh);
    document.querySelector("#close-dialog").addEventListener("click", () => dialog.close());
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""
