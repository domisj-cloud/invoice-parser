#!/usr/bin/env python3
"""Create a NiFi process group that polls Inbucket via POP3, extracts PDF
attachments, uploads each PDF into MinIO's inv-input bucket, and notifies
the parser service.

Flow:
    ConsumePOP3 -> ExtractEmailAttachments -> RouteOnAttribute (pdf only)
        -> UpdateAttribute (set s3.object.key) -> PutS3Object (inv-input)
        -> ReplaceText (build parser event JSON) -> InvokeHTTP (parser)

All processors are created DISABLED so you can review and start them from
the NiFi UI. Run after `docker compose up` and after NiFi is fully booted.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Reuse the NiFi client / bundle helpers from the file-ingest flow script.
sys.path.insert(0, str(Path(__file__).parent))
from create_nifi_flow import NiFiClient, standard_bundle  # noqa: E402

BASE_URL = "http://localhost:18080/nifi-api"
USERNAME = "admin"
PASSWORD = "adminadminadmin"
PROCESS_GROUP_NAME = "Invoice Email Demo"

# POP3 mailbox NiFi will poll on the `mailbox` (Inbucket) service.
# Inbucket is a catch-all: any password works for any mailbox.
POP3_HOST = "mailbox"
POP3_PORT = "1100"
POP3_USER = "invoices"
POP3_PASSWORD = "any"  # Inbucket accepts any password


def email_bundle() -> dict[str, str]:
    return {
        "group": "org.apache.nifi",
        "artifact": "nifi-email-nar",
        "version": "2.3.0",
    }


def aws_bundle() -> dict[str, str]:
    return {
        "group": "org.apache.nifi",
        "artifact": "nifi-aws-nar",
        "version": "2.3.0",
    }


def update_attribute_bundle() -> dict[str, str]:
    return {
        "group": "org.apache.nifi",
        "artifact": "nifi-update-attribute-nar",
        "version": "2.3.0",
    }


PROCESSOR_SPECS = [
    (
        "Poll mailbox (POP3)",
        "org.apache.nifi.processors.email.ConsumePOP3",
        email_bundle,
        100, 100,
    ),
    (
        "Extract email attachments",
        "org.apache.nifi.processors.email.ExtractEmailAttachments",
        email_bundle,
        450, 100,
    ),
    (
        "Keep PDF attachments only",
        "org.apache.nifi.processors.standard.RouteOnAttribute",
        standard_bundle,
        800, 100,
    ),
    (
        "Set object key from email",
        "org.apache.nifi.processors.attributes.UpdateAttribute",
        update_attribute_bundle,
        1150, 100,
    ),
    (
        "Put email PDF to inv-input",
        "org.apache.nifi.processors.aws.s3.PutS3Object",
        aws_bundle,
        1500, 100,
    ),
    (
        "Build parser event JSON (email)",
        "org.apache.nifi.processors.standard.ReplaceText",
        standard_bundle,
        1850, 100,
    ),
    (
        "Call parser service (email)",
        "org.apache.nifi.processors.standard.InvokeHTTP",
        standard_bundle,
        2200, 100,
    ),
]


def ensure_processors(client: NiFiClient, group_id: str) -> dict[str, str]:
    flow = client.request("GET", f"/flow/process-groups/{group_id}")[
        "processGroupFlow"
    ]["flow"]
    existing = {
        processor["component"]["name"]: processor["component"]["id"]
        for processor in flow["processors"]
    }
    processor_ids: dict[str, str] = {}
    for name, processor_type, bundle_fn, x, y in PROCESSOR_SPECS:
        if name in existing:
            processor_ids[name] = existing[name]
            continue
        response = client.request(
            "POST",
            f"/process-groups/{group_id}/processors",
            {
                "revision": {"version": 0},
                "component": {
                    "name": name,
                    "type": processor_type,
                    "bundle": bundle_fn(),
                    "position": {"x": float(x), "y": float(y)},
                },
            },
        )
        processor_ids[name] = response["id"]
    return processor_ids


def configure_processors(
    client: NiFiClient,
    processor_ids: dict[str, str],
    credentials_id: str,
) -> None:
    # ConsumePOP3 — poll the local Inbucket mailbox every 30s.
    client.configure_processor(
        processor_ids["Poll mailbox (POP3)"],
        {
            "Host Name": POP3_HOST,
            "Port": POP3_PORT,
            "User Name": POP3_USER,
            "Password": POP3_PASSWORD,
            "Folder": "INBOX",
            "Use SSL": "false",
        },
    )
    # NiFi schedules ConsumePOP3 from the scheduling tab (run schedule), not properties.
    set_run_schedule(client, processor_ids["Poll mailbox (POP3)"], "30 sec")

    # ExtractEmailAttachments — auto-terminate original + failure;
    # only the "attachments" relationship flows forward.
    client.configure_processor(
        processor_ids["Extract email attachments"],
        {},
        ["original", "failure"],
    )

    # RouteOnAttribute — keep only PDFs by filename extension.
    client.configure_processor(
        processor_ids["Keep PDF attachments only"],
        {
            "Routing Strategy": "Route to Property name",
            "pdf": "${filename:toLower():endsWith('.pdf')}",
        },
        ["unmatched"],
    )

    # UpdateAttribute — set the S3 object key. Prefix with timestamp to
    # avoid collisions when the same filename comes in multiple times.
    client.configure_processor(
        processor_ids["Set object key from email"],
        {"s3.object.key": "${now():format('yyyyMMdd-HHmmss')}-${filename}"},
    )

    # PutS3Object — same MinIO target as the file-based flow.
    client.configure_processor(
        processor_ids["Put email PDF to inv-input"],
        {
            "Bucket": "inv-input",
            "Object Key": "${s3.object.key}",
            "Region": "us-east-1",
            "AWS Credentials Provider service": credentials_id,
            "Resource Transfer Source": "FLOWFILE_CONTENT",
            "Storage Class": "Standard",
            "server-side-encryption": "None",
            "Endpoint Override URL": "http://minio:9000",
            "Signer Override": "Default Signature",
            "use-path-style-access": "true",
            "use-chunked-encoding": "false",
            "Content Type": "application/pdf",
        },
        ["failure"],
    )

    client.configure_processor(
        processor_ids["Build parser event JSON (email)"],
        {
            "Replacement Strategy": "Always Replace",
            "Evaluation Mode": "Entire text",
            "Replacement Value":
                '{"bucket":"inv-input","object_key":"${s3.object.key}"}',
        },
        ["failure"],
    )

    client.configure_processor(
        processor_ids["Call parser service (email)"],
        {
            "HTTP Method": "POST",
            "HTTP URL": "http://parser-service:8000/events/invoice-uploaded",
            "Request Content-Type": "application/json",
            "Response Cookie Strategy": "DISABLED",
            "Response FlowFile Naming Strategy": "RANDOM",
        },
        ["Failure", "No Retry", "Original", "Response", "Retry"],
    )


def set_run_schedule(client: NiFiClient, processor_id: str, schedule: str) -> None:
    processor = client.request("GET", f"/processors/{processor_id}")
    client.request(
        "PUT",
        f"/processors/{processor_id}",
        {
            "revision": processor["revision"],
            "component": {
                "id": processor_id,
                "config": {"schedulingPeriod": schedule},
            },
        },
    )


def ensure_connections(
    client: NiFiClient,
    group_id: str,
    processor_ids: dict[str, str],
) -> None:
    flow = client.request("GET", f"/flow/process-groups/{group_id}")[
        "processGroupFlow"
    ]["flow"]
    existing = {
        (
            connection["component"]["source"]["id"],
            connection["component"]["destination"]["id"],
            tuple(connection["component"]["selectedRelationships"]),
        )
        for connection in flow["connections"]
    }
    links = [
        ("Poll mailbox (POP3)", "Extract email attachments", "success"),
        ("Extract email attachments", "Keep PDF attachments only", "attachments"),
        ("Keep PDF attachments only", "Set object key from email", "pdf"),
        ("Set object key from email", "Put email PDF to inv-input", "success"),
        ("Put email PDF to inv-input", "Build parser event JSON (email)", "success"),
        ("Build parser event JSON (email)", "Call parser service (email)", "success"),
    ]
    for source, destination, relationship in links:
        source_id = processor_ids[source]
        destination_id = processor_ids[destination]
        key = (source_id, destination_id, (relationship,))
        if key in existing:
            continue
        client.request(
            "POST",
            f"/process-groups/{group_id}/connections",
            {
                "revision": {"version": 0},
                "component": {
                    "name": f"{source} to {destination}",
                    "source": {
                        "id": source_id,
                        "groupId": group_id,
                        "type": "PROCESSOR",
                    },
                    "destination": {
                        "id": destination_id,
                        "groupId": group_id,
                        "type": "PROCESSOR",
                    },
                    "selectedRelationships": [relationship],
                },
            },
        )


def disable_processors(client: NiFiClient, processor_ids: dict[str, str]) -> None:
    for processor_id in processor_ids.values():
        processor = client.request("GET", f"/processors/{processor_id}")
        if processor["component"]["state"] == "DISABLED":
            continue
        client.request(
            "PUT",
            f"/processors/{processor_id}/run-status",
            {"revision": processor["revision"], "state": "DISABLED"},
        )


def stop_running_processors(
    client: NiFiClient, processor_ids: dict[str, str]
) -> None:
    """Stop any RUNNING/STARTING processors so they can be re-configured.

    NiFi returns 409 if a processor is updated while not in STOPPED state.
    """
    for processor_id in processor_ids.values():
        processor = client.request("GET", f"/processors/{processor_id}")
        state = processor["component"]["state"]
        if state in ("STOPPED", "DISABLED"):
            continue
        client.request(
            "PUT",
            f"/processors/{processor_id}/run-status",
            {"revision": processor["revision"], "state": "STOPPED"},
        )


def main() -> None:
    client = NiFiClient(BASE_URL, USERNAME, PASSWORD)
    root_id = client.root_process_group_id()
    group_id = client.ensure_process_group(root_id, PROCESS_GROUP_NAME)

    credentials_id = client.ensure_minio_credentials(group_id)
    # Only disable+reconfigure+enable the credentials service if it isn't
    # already enabled — disabling it while running processors reference it
    # returns 409.
    service = client.request("GET", f"/controller-services/{credentials_id}")
    if service["component"]["state"] != "ENABLED":
        client.disable_controller_service(credentials_id)
        client.configure_minio_credentials(credentials_id)
        client.enable_controller_service(credentials_id)

    processor_ids = ensure_processors(client, group_id)
    stop_running_processors(client, processor_ids)
    configure_processors(client, processor_ids, credentials_id)
    ensure_connections(client, group_id, processor_ids)
    disable_processors(client, processor_ids)

    print(f"Created or updated NiFi process group: {PROCESS_GROUP_NAME}")
    print(f"Process group id: {group_id}")
    print("All processors are DISABLED for review.")
    print()
    print("Mailbox config:")
    print(f"  POP3 host : {POP3_HOST}:{POP3_PORT}")
    print(f"  User      : {POP3_USER}")
    print(f"  Send to   : {POP3_USER}@inbucket.local "
          "(any domain works — Inbucket routes by local-part)")


if __name__ == "__main__":
    main()
