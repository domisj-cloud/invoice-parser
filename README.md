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
| Mailbox (Inbucket) UI | http://localhost:9090 | Catch-all dev mail server; browse received emails per mailbox |
| Mailbox SMTP | `localhost:2500` | Send PDF invoices in (e.g. via `scripts/send_test_email.py`) |
| Mailbox POP3 | `localhost:1100` | NiFi polls this; mailbox `invoices`, any password |

MinIO buckets are created automatically:

- `inv-input`
- `inv-output`
- `inv-error`

## Start

```bash
./scripts/start_services.sh
```

Stop:

```bash
./scripts/stop_services.sh
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
- [Email ingestion](docs/email-ingestion.md)
- [Output format (EN 16931 / UBL 2.1)](docs/output-format.md)

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

## Email Ingestion Flow (Inbucket)

The stack also includes **Inbucket**, a catch-all dev mail server, so the full
"email-with-PDF -> parser" path can be demoed locally without a real mail
provider. Inbucket accepts any address and any POP3 password.

| Endpoint | URL / port | Purpose |
| --- | --- | --- |
| Web UI | http://localhost:9090 | Browse received emails per mailbox |
| SMTP | `localhost:2500` | Send mail in |
| POP3 | `localhost:1100` | NiFi `ConsumePOP3` polls this |

Create the email-ingestion NiFi process group (separate from the file-based
one, so both can coexist):

```bash
python3 scripts/create_nifi_email_flow.py
```

This creates an `Invoice Email Demo` process group with this pipeline (all
processors are left DISABLED for review):

```
ConsumePOP3 -> ExtractEmailAttachments -> RouteOnAttribute (pdf only)
    -> UpdateAttribute (s3.object.key) -> PutS3Object (inv-input)
    -> ReplaceText -> InvokeHTTP (parser-service)
```

POP3 config baked into the flow: host `mailbox`, port `1100`, user
`invoices`, any password.

Send a test email with a PDF attachment from your laptop:

```bash
python3 scripts/send_test_email.py samples/inbox/some-invoice.pdf
# Defaults: To=invoices@inbucket.local, host=localhost:2500
```

Then start the processors in NiFi. Inbucket's UI at
http://localhost:9090/m/invoices shows what arrived; MinIO `inv-output`
shows the resulting XML.

### Real Gmail -> Outlook -> NiFi (hosted IMAPS)

To run the full internet-hop scenario (send from your real Gmail and
have NiFi pick it up), point NiFi at a hosted IMAPS mailbox you
control (Outlook.com is the easiest choice):

```bash
export IMAPS_USER='invoice-parser-poc@outlook.com'
export IMAPS_PASSWORD='your-app-password'
python3 scripts/create_nifi_imaps_flow.py
```

This creates a separate `Invoice IMAPS Demo` process group with all
processors disabled. The password is stored as a NiFi sensitive
property. Full details and provider settings (iCloud, Yahoo, Zoho,
Fastmail, ...) are in [docs/email-ingestion.md](docs/email-ingestion.md).

### Why not deliver real Gmail to the local server?

Gmail delivers via your domain's public MX record on port 25, which a
laptop on a residential ISP can't host. For this PoC the recommended
pattern is to send test emails locally (script above). If real
internet-to-local delivery is required later, add a Cloudflare Tunnel or
an inbound email forwarding service (ImprovMX, ForwardEmail.net) in
front of Inbucket.

## Direct Parser Test Without NiFi

Upload a PDF to `inv-input` in the MinIO console, then call:

```bash
curl -X POST http://localhost:8000/events/invoice-uploaded \
  -H 'Content-Type: application/json' \
  -d '{"bucket":"inv-input","object_key":"invoice_multipage_many_lines.pdf"}'
```

## XML Output Format

The parser writes **EN 16931-compliant UBL 2.1** XML — the European
standard for electronic invoicing. Documents declare conformance via
`<cbc:CustomizationID>urn:cen.eu:en16931:2017</cbc:CustomizationID>`
and use the official UBL 2.1 Invoice / CreditNote schema.

Example output:

```xml
<?xml version='1.0' encoding='utf-8'?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:CustomizationID>urn:cen.eu:en16931:2017</cbc:CustomizationID>
  <cbc:ID>INV-2026-0001</cbc:ID>
  <cbc:IssueDate>2026-05-01</cbc:IssueDate>
  <cbc:DueDate>2026-05-15</cbc:DueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyName><cbc:Name>Blue River Software LTD</cbc:Name></cac:PartyName>
      <cac:PartyTaxScheme>
        <cbc:CompanyID>LT100000000</cbc:CompanyID>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:PartyTaxScheme>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>Blue River Software LTD</cbc:RegistrationName>
      </cac:PartyLegalEntity>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <!-- AccountingCustomerParty, TaxTotal, LegalMonetaryTotal, InvoiceLine ... -->
</Invoice>
```

Credit notes use the `<CreditNote>` root with `CreditNoteTypeCode 381`
and `CreditNoteLine` / `CreditedQuantity` instead of the invoice
equivalents.

Why EN 16931: mandatory for B2G across the EU, rolling out for B2B in
France (2026), Germany (2027), Belgium, Poland; foundation for PEPPOL
BIS Billing 3.0, xRechnung, and most EU national profiles.

Full field mapping (BT/BG codes → UBL elements), code-list choices
(tax category, unit of measure), decimal rules, date normalization,
known limitations, and notes on pivoting to PEPPOL / xRechnung /
Factur-X are in **[docs/output-format.md](docs/output-format.md)**.

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

This PoC parser now uses known sample parsers first, then falls back to dynamic best-effort extraction for any readable PDF text with monetary amounts.

- EU VAT invoice
- US invoice
- multipage invoice with many line items
- credit note with negative amounts
- generic invoices with inline labels such as `Invoice number`, `Date of issue`, `Bill to`, and compact `Description Qty Unit price Tax Amount` tables
- generic receipts/tax invoices with colon labels such as receipt number, company/candidate name, item amount, promotion, tax, and transaction amount
- fallback extraction for title-based invoice numbers, bilingual labels, supplier/customer sections, subtotal/tax/total labels, and one synthesized line item when a table cannot be identified
- stacked tables where PDF text extraction emits item, quantity, rate, and amount as separate vertical lines

For broader production usage, the next step is to add supplier-specific layouts, OCR fallback for scanned PDFs, and confidence/error reporting.
