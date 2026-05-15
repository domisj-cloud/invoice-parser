# Demo Guide

## Start the Stack

```bash
cd "/Users/domas/Documents/New project"
docker compose up --build
```

Open:

- NiFi: http://localhost:18080/nifi
- MinIO: http://localhost:9001
- Parser dashboard: http://localhost:8000
- Parser health: http://localhost:8000/health

Credentials:

- NiFi: `admin` / `adminadminadmin`
- MinIO: `minioadmin` / `minioadmin`

## Create the NiFi Flow

The flow can be provisioned automatically:

```bash
python3 scripts/create_nifi_flow.py
```

This creates the `Invoice PDF Demo` process group and leaves all processors disabled.

To run the flow:

1. Open `Invoice PDF Demo` in NiFi.
2. Enable the processors.
3. Start the processors.
4. Copy a PDF into `samples/inbox`.

Example:

```bash
cp "/Users/domas/Downloads/invoice_pdf_examples/invoice_multipage_many_lines.pdf" \
   "/Users/domas/Documents/New project/samples/inbox/"
```

NiFi reads the file from `/opt/nifi/inbox`, uploads it to `inv-input`, and calls the parser event endpoint.

## Expected Result

In MinIO:

- Input PDF appears in `inv-input`
- Parsed XML appears in `inv-output`
- Failed PDF copies and error reports appear in `inv-error`

In the parser dashboard:

- Successful and failed processing events appear in the history table
- Duration is shown per file
- Successful rows link to the output XML
- Failed rows link to the failed PDF, error JSON, and full error log

## Direct Parser Test

Upload a PDF manually to MinIO bucket `inv-input`, then call:

```bash
curl -X POST http://localhost:8000/events/invoice-uploaded \
  -H "Content-Type: application/json" \
  -d '{"bucket":"inv-input","object_key":"invoice_multipage_many_lines.pdf"}'
```

Use the exact object name shown in MinIO.

## Stop the Stack

```bash
docker compose down
```

This stops containers and removes the Docker network. Named volumes are preserved.
