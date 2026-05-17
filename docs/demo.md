# Demo Guide

This guide assumes the working directory is the repository root.

## Start the Stack

```bash
./scripts/start_services.sh
```

The script starts all Docker services in the background, rebuilds the
parser image when needed, waits for the parser health endpoint, and
prints the demo URLs.

Open:

- NiFi: <http://localhost:18080/nifi>
- MinIO: <http://localhost:9001>
- Parser dashboard: <http://localhost:8000>
- Parser health: <http://localhost:8000/health>
- Local mailbox UI (Inbucket): <http://localhost:9090>

Credentials:

- NiFi: `admin` / `adminadminadmin`
- MinIO: `minioadmin` / `minioadmin`

## Pick an Ingestion Path

Three NiFi process groups exist; pick whichever matches the demo
scenario. They can coexist.

### 1 — File drop (simplest)

```bash
python3 scripts/create_nifi_flow.py
```

Creates the `Invoice PDF Demo` process group (all processors disabled).
In the NiFi UI, enable + start the group, then drop a PDF into
`samples/inbox/`:

```bash
cp path/to/any/invoice.pdf samples/inbox/
```

NiFi reads the file from `/opt/nifi/inbox` (mounted from
`samples/inbox`), uploads it to `inv-input`, and calls the parser
event endpoint.

### 2 — Local mailbox (offline email demo)

```bash
python3 scripts/create_nifi_email_flow.py
```

Creates the `Invoice Email Demo` process group. Then send a test
email with a PDF attachment to the local Inbucket SMTP server:

```bash
python3 scripts/send_test_email.py path/to/any/invoice.pdf
# Defaults: To=invoices@inbucket.local, host=localhost:2500
```

Inbucket is catch-all, so any address with local-part `invoices` works.
Browse arrivals at <http://localhost:9090/m/invoices>.

### 3 — Hosted IMAPS (real internet hop)

```bash
export IMAPS_USER='your-poc-mailbox@outlook.com'   # or @icloud.com, @zohomail.eu, ...
export IMAPS_PASSWORD='your-app-password'
python3 scripts/create_nifi_imaps_flow.py
```

Creates `Invoice IMAPS Demo`. Send mail from any real account
(Gmail, work mail, …) to the configured hosted mailbox; NiFi polls
it via IMAPS every 30 seconds. Full provider setup, password
rotation, and troubleshooting are in
[email-ingestion.md](email-ingestion.md).

## Expected Result

In MinIO:

- Input PDF appears in `inv-input`
- Parsed XML appears in `inv-output` (EN 16931-compliant UBL 2.1 —
  see [output-format.md](output-format.md))
- Failed PDF copies and error reports appear in `inv-error`

In the parser dashboard:

- Successful and failed processing events appear in the history table
- Duration is shown per file
- Successful rows link to the output XML
- Failed rows link to the failed PDF, error JSON, and full error log

## Direct Parser Test (no NiFi)

Upload a PDF manually to MinIO bucket `inv-input` via the console,
then call:

```bash
curl -X POST http://localhost:8000/events/invoice-uploaded \
  -H "Content-Type: application/json" \
  -d '{"bucket":"inv-input","object_key":"your-invoice.pdf"}'
```

Use the exact object name shown in MinIO.

## Stop the Stack

```bash
./scripts/stop_services.sh
```

This stops containers and removes the Docker network. Named volumes
are preserved (MinIO data, parser job history, NiFi repositories,
Inbucket is in-memory and is wiped).
