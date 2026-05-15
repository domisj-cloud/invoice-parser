# Architecture

## PoC Flow

```text
Mock invoice PDF
  -> Apache NiFi
  -> MinIO inv-input
  -> Parser service event API
  -> MinIO inv-output XML
```

The parser service is event-driven. It does not poll MinIO. NiFi uploads the PDF first, then calls:

```text
POST /events/invoice-uploaded
```

with:

```json
{
  "bucket": "inv-input",
  "object_key": "example.pdf"
}
```

## Buckets

- `inv-input`: original PDF invoices
- `inv-output`: generated XML files
- `inv-error`: failed PDF copies and `*.error.json` reports

The input file is left in place even when parsing fails.

## Services

- `nifi`: receives demo files and orchestrates file transfer plus event call
- `nifi-proxy`: local HTTP proxy for the NiFi UI, avoiding browser issues with NiFi's self-signed HTTPS certificate
- `minio`: S3-compatible object storage for the PoC
- `parser-service`: FastAPI service that downloads PDFs from MinIO, parses them, and writes XML

## Parser Dashboard

The parser service exposes a lightweight dashboard at:

```text
http://localhost:8000
```

Processing metadata is stored in SQLite under the parser work directory, which is backed by the `parser-work` Docker volume in the PoC.

The dashboard and `/api/jobs` expose:

- input bucket and object key
- started and completed timestamps
- processing duration
- status: `PROCESSING`, `SUCCESS`, `PARSE_ERROR`, or `ERROR`
- invoice number, document type, and line count when parsing succeeds
- output XML link when parsing succeeds
- failed PDF and error report links when error archiving succeeds
- captured error log when parsing fails

## Parser Scope

The parser uses known sample parsers first, then falls back to dynamic best-effort extraction for any readable PDF text with monetary amounts:

- EU VAT invoice
- US invoice
- multipage invoice with many line items
- credit note with negative amounts
- generic invoices with inline labels such as `Invoice number`, `Date of issue`, `Bill to`, and compact `Description Qty Unit price Tax Amount` tables
- generic receipts/tax invoices with colon labels such as receipt number, company/candidate name, item amount, promotion, tax, and transaction amount
- fallback extraction for title-based invoice numbers, bilingual labels, supplier/customer sections, subtotal/tax/total labels, and one synthesized line item when a table cannot be identified

For production usage, the parser should add supplier-specific templates, OCR fallback for scanned PDFs, confidence scoring, and richer error classification.
