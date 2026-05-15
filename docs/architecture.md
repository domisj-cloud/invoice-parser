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

## Parser Scope

The PoC parser is rule-based and targeted at the provided mock invoice families:

- EU VAT invoice
- US invoice
- multipage invoice with many line items
- credit note with negative amounts

For production usage, the parser should add supplier-specific templates, OCR fallback for scanned PDFs, confidence scoring, and richer error classification.
