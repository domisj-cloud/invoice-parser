# Invoice PDF Parser PoC

Docker-based proof of concept for an event-driven invoice parsing flow:

```text
PDF invoice -> Apache NiFi -> MinIO inv-input -> parser event API -> MinIO inv-output XML
```

The mailbox part is intentionally skipped for this PoC. NiFi can ingest mock PDFs from the mounted `samples/inbox` directory.

## Services

| Service | URL | Notes |
| --- | --- | --- |
| MinIO API | http://localhost:9000 | S3-compatible API |
| MinIO Console | http://localhost:9001 | Login `minioadmin` / `minioadmin` |
| Parser dashboard | http://localhost:8000 | Processing history, status, object links, and error logs |
| Parser API | http://localhost:8000/health | FastAPI health endpoint |
| NiFi | http://localhost:18080/nifi | Login `admin` / `adminadminadmin`; proxied to NiFi's internal HTTPS |

MinIO buckets are created automatically:

- `inv-input`
- `inv-output`
- `inv-error`

## Start

```bash
docker compose up --build
```

Health check:

```bash
curl http://localhost:8000/health
```

Parser dashboard:

```text
http://localhost:8000
```

The dashboard shows processed files, start time, processing duration, parsing status, output XML links, error artifact links, and error logs.

More detailed operational docs:

- [Demo guide](docs/demo.md)
- [Architecture](docs/architecture.md)

## Parser Event Contract

NiFi should call the parser after placing a PDF into `inv-input`.

Endpoint:

```text
POST http://parser-service:8000/events/invoice-uploaded
```

Payload:

```json
{
  "bucket": "inv-input",
  "object_key": "invoice_multipage_many_lines.pdf"
}
```

Successful response:

```json
{
  "input_bucket": "inv-input",
  "input_object_key": "invoice_multipage_many_lines.pdf",
  "output_bucket": "inv-output",
  "output_object_key": "invoice_multipage_many_lines.xml",
  "invoice_number": "GC-2026-7781",
  "document_type": "invoice",
  "line_count": 45
}
```

Failure behavior:

- The original PDF is left in `inv-input`.
- If the parser can download the PDF but cannot parse/process it, it copies the PDF to `inv-error/failed/...`.
- The parser also writes an `*.error.json` report beside the failed PDF in `inv-error`.
- If the object does not exist in `inv-input`, there is nothing to copy; the parser returns an error and logs the missing key.

Processing history:

- `GET /api/jobs` returns recent parser jobs and counters.
- `GET /api/jobs/{job_id}` returns one job.
- `GET /objects/{job_id}/output` redirects to a temporary MinIO URL for successful XML output.
- `GET /objects/{job_id}/error` redirects to the failed PDF copy when available.
- `GET /objects/{job_id}/error-report` redirects to the JSON error report when available.

## NiFi Demo Flow

The demo flow can be created automatically:

```bash
python3 scripts/create_nifi_flow.py
```

The script creates an `Invoice PDF Demo` process group and leaves all processors disabled for review.

The flow contains these processors:

1. `GetFile`
   - Input Directory: `/opt/nifi/inbox`
   - Keep Source File: `true` for repeatable demos, or `false` if you want one-time processing

2. `UpdateAttribute`
   - Add attribute:
     - `s3.object.key` = `${filename}`

3. `PutS3Object`
   - Bucket: `inv-input`
   - Object Key: `${s3.object.key}`
   - Endpoint Override URL: `http://minio:9000`
   - Access Key ID: `minioadmin`
   - Secret Access Key: `minioadmin`
   - Region: `us-east-1`
   - Use Path Style Access: `true`

4. `ReplaceText`
   - Replacement Strategy: `Always Replace`
   - Evaluation Mode: `Entire text`
   - Replacement Value:

```json
{"bucket":"inv-input","object_key":"${s3.object.key}"}
```

5. `InvokeHTTP`
   - HTTP Method: `POST`
   - Remote URL: `http://parser-service:8000/events/invoice-uploaded`
   - Content-Type: `application/json`

To demo, copy a sample PDF into `samples/inbox/`, start the NiFi processors, and check MinIO `inv-output` for the XML.

## Direct Parser Test Without NiFi

Upload a PDF to `inv-input` in the MinIO console, then call:

```bash
curl -X POST http://localhost:8000/events/invoice-uploaded \
  -H 'Content-Type: application/json' \
  -d '{"bucket":"inv-input","object_key":"invoice_multipage_many_lines.pdf"}'
```

## XML Shape

The PoC writes a simple XML schema:

```xml
<Invoice>
  <type>invoice</type>
  <number>INV-2026-0001</number>
  <date>2026-05-01</date>
  <due_date>2026-05-15</due_date>
  <currency>EUR</currency>
  <Seller>
    <name>Blue River Software LTD</name>
    <vat>LT100000000</vat>
  </Seller>
  <Buyer>
    <name>Greenfield Retail UAB</name>
    <vat>LT200000000</vat>
  </Buyer>
  <Lines>
    <Line>
      <description>Monthly SaaS subscription - Pro plan</description>
      <quantity>3</quantity>
      <unit_price>49.0</unit_price>
      <vat_rate>21</vat_rate>
      <line_total>147.0</line_total>
    </Line>
  </Lines>
  <Totals>
    <subtotal>647.0</subtotal>
    <tax>135.87</tax>
    <total>782.87</total>
  </Totals>
</Invoice>
```

## Local Tests

The tests use the sample PDFs and expected XML files from:

```text
/Users/domas/Downloads/invoice_pdf_examples
```

Run:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r parser-service/requirements.txt
cd parser-service
pytest -q
```

Override the sample path if needed:

```bash
INVOICE_SAMPLE_DIR=/path/to/invoice_pdf_examples pytest -q
```

## Current Parser Coverage

This PoC parser is intentionally rule-based and targeted at the supplied mock invoice families:

- EU VAT invoice
- US invoice
- multipage invoice with many line items
- credit note with negative amounts

For real supplier invoices, the next step is to add supplier-specific layouts, OCR fallback for scanned PDFs, and confidence/error reporting.
