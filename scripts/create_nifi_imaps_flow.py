#!/usr/bin/env python3
"""Create a NiFi process group that polls a hosted IMAPS mailbox (Outlook,
iCloud, Yahoo, Zoho, etc.), extracts PDF attachments from each email, uploads
each PDF into MinIO's inv-input bucket, and notifies the parser service.

Flow:
    ConsumeIMAP -> ExtractEmailAttachments -> RouteOnAttribute (pdf only)
        -> UpdateAttribute (set s3.object.key) -> PutS3Object (inv-input)
        -> ReplaceText (build parser event JSON) -> InvokeHTTP (parser)

Credentials and host are read from environment variables so they never
land in git:

    IMAPS_HOST       (default: outlook.office365.com)
    IMAPS_PORT       (default: 993)
    IMAPS_USER       (required, e.g. invoice-parser-poc@outlook.com)
    IMAPS_PASSWORD   (required; use an app password if your account has 2FA)
    IMAPS_FOLDER     (default: INBOX)
    IMAPS_SCHEDULE   (default: "30 sec" — NiFi schedulingPeriod for ConsumeIMAP)
    IMAPS_DELETE     (default: "false" — set "true" to delete after fetch)

All processors are created DISABLED so you can review the configuration
in the NiFi UI before starting them.

Usage:
    export IMAPS_USER='invoice-parser-poc@outlook.com'
    export IMAPS_PASSWORD='your-app-password'
    python3 scripts/create_nifi_imaps_flow.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Reuse the NiFi client / bundle helpers from the file-ingest flow script.
sys.path.insert(0, str(Path(__file__).parent))
from create_nifi_flow import NiFiClient, standard_bundle  # noqa: E402

BASE_URL = "http://localhost:18080/nifi-api"
USERNAME = "admin"
PASSWORD = "adminadminadmin"
PROCESS_GROUP_NAME = "Invoice IMAPS Demo"


@dataclass(frozen=True)
class ImapsSettings:
    host: str
    port: str
    user: str
    password: str
    folder: str
    schedule: str
    delete_after_fetch: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ImapsSettings":
        env = env if env is not None else os.environ
        missing = [
            name
            for name in ("IMAPS_USER", "IMAPS_PASSWORD")
            if not env.get(name)
        ]
        if missing:
            raise SystemExit(
                "Missing required environment variable(s): "
                + ", ".join(missing)
                + "\n\nExample:\n"
                "  export IMAPS_USER='invoice-parser-poc@outlook.com'\n"
                "  export IMAPS_PASSWORD='your-app-password'\n"
                "  python3 scripts/create_nifi_imaps_flow.py"
            )
        return cls(
            host=env.get("IMAPS_HOST", "outlook.office365.com"),
            port=env.get("IMAPS_PORT", "993"),
            user=env["IMAPS_USER"],
            password=env["IMAPS_PASSWORD"],
            folder=env.get("IMAPS_FOLDER", "INBOX"),
            schedule=env.get("IMAPS_SCHEDULE", "30 sec"),
            delete_after_fetch=env.get("IMAPS_DELETE", "false"),
        )


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
        "Poll mailbox (IMAPS)",
        "org.apache.nifi.processors.email.ConsumeIMAP",
        email_bundle,
        100, 100,
    ),
    (
        "Extract email attachments (IMAPS)",
        "org.apache.nifi.processors.email.ExtractEmailAttachments",
        email_bundle,
        450, 100,
    ),
    (
        "Keep PDF attachments only (IMAPS)",
        "org.apache.nifi.processors.standard.RouteOnAttribute",
        standard_bundle,
        800, 100,
    ),
    (
        "Set object key from IMAPS email",
        "org.apache.nifi.processors.attributes.UpdateAttribute",
        update_attribute_bundle,
        1150, 100,
    ),
    (
        "Put IMAPS PDF to inv-input",
        "org.apache.nifi.processors.aws.s3.PutS3Object",
        aws_bundle,
        1500, 100,
    ),
    (
        "Build parser event JSON (IMAPS)",
        "org.apache.nifi.processors.standard.ReplaceText",
        standard_bundle,
        1850, 100,
    ),
    (
        "Call parser service (IMAPS)",
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
    settings: ImapsSettings,
) -> None:
    # ConsumeIMAP — Password is automatically encrypted by NiFi as a sensitive
    # property; it's redacted in the UI and provenance.
    #
    # IMPORTANT: NiFi 2.x uses lowercase identifier keys (host/port/user/
    # password/folder/delete.messages), not the legacy display names
    # ("Host Name", "Port", ...). Passing the display names silently creates
    # unrecognised user-defined properties while the real ones stay unset,
    # which makes the processor INVALID with no helpful bulletin. The
    # explicit None entries below clear any stale legacy keys that may have
    # been written by an older version of this script.
    client.configure_processor(
        processor_ids["Poll mailbox (IMAPS)"],
        {
            "host": settings.host,
            "port": settings.port,
            "user": settings.user,
            "password": settings.password,
            "folder": settings.folder,
            "Use SSL": "true",
            "delete.messages": settings.delete_after_fetch,
            "Mark Messages as Read": "true",
            # Clear stale legacy-named properties from earlier script runs:
            "Host Name": None,
            "Port": None,
            "User Name": None,
            "Password": None,
            "Folder": None,
            "Should Delete Messages": None,
        },
    )
    set_run_schedule(
        client, processor_ids["Poll mailbox (IMAPS)"], settings.schedule
    )

    client.configure_processor(
        processor_ids["Extract email attachments (IMAPS)"],
        {},
        ["original", "failure"],
    )

    client.configure_processor(
        processor_ids["Keep PDF attachments only (IMAPS)"],
        {
            "Routing Strategy": "Route to Property name",
            "pdf": "${filename:toLower():endsWith('.pdf')}",
        },
        ["unmatched"],
    )

    client.configure_processor(
        processor_ids["Set object key from IMAPS email"],
        {
            "s3.object.key":
                "${now():format('yyyyMMdd-HHmmss')}-${filename}",
        },
    )

    client.configure_processor(
        processor_ids["Put IMAPS PDF to inv-input"],
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
        processor_ids["Build parser event JSON (IMAPS)"],
        {
            "Replacement Strategy": "Always Replace",
            "Evaluation Mode": "Entire text",
            "Replacement Value":
                '{"bucket":"inv-input","object_key":"${s3.object.key}"}',
        },
        ["failure"],
    )

    client.configure_processor(
        processor_ids["Call parser service (IMAPS)"],
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
        ("Poll mailbox (IMAPS)", "Extract email attachments (IMAPS)", "success"),
        ("Extract email attachments (IMAPS)",
         "Keep PDF attachments only (IMAPS)", "attachments"),
        ("Keep PDF attachments only (IMAPS)",
         "Set object key from IMAPS email", "pdf"),
        ("Set object key from IMAPS email",
         "Put IMAPS PDF to inv-input", "success"),
        ("Put IMAPS PDF to inv-input",
         "Build parser event JSON (IMAPS)", "success"),
        ("Build parser event JSON (IMAPS)",
         "Call parser service (IMAPS)", "success"),
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
    """Move any RUNNING/STARTING processors to STOPPED.

    NiFi rejects configuration updates on processors that are not stopped
    (HTTP 409), so anything the user started in the UI between flow-script
    runs has to be quiesced first. We then leave them in STOPPED until the
    final disable_processors() call returns them to DISABLED for review.
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
    settings = ImapsSettings.from_env()

    client = NiFiClient(BASE_URL, USERNAME, PASSWORD)
    root_id = client.root_process_group_id()
    group_id = client.ensure_process_group(root_id, PROCESS_GROUP_NAME)

    credentials_id = client.ensure_minio_credentials(group_id)
    # Only disable+reconfigure+enable the credentials service if it isn't
    # already enabled. Doing so when it's referenced by running processors
    # would 409 (NiFi forbids disabling a service while referenced).
    service = client.request("GET", f"/controller-services/{credentials_id}")
    if service["component"]["state"] != "ENABLED":
        client.disable_controller_service(credentials_id)
        client.configure_minio_credentials(credentials_id)
        client.enable_controller_service(credentials_id)

    processor_ids = ensure_processors(client, group_id)
    # Stop anything that the user may have started in the UI between runs;
    # NiFi forbids re-configuring a RUNNING/STARTING processor (409).
    stop_running_processors(client, processor_ids)
    configure_processors(client, processor_ids, credentials_id, settings)
    ensure_connections(client, group_id, processor_ids)
    disable_processors(client, processor_ids)

    print(f"Created or updated NiFi process group: {PROCESS_GROUP_NAME}")
    print(f"Process group id: {group_id}")
    print("All processors are DISABLED for review.")
    print()
    print("IMAPS config (password is encrypted by NiFi as a sensitive property):")
    print(f"  Host    : {settings.host}:{settings.port}")
    print(f"  User    : {settings.user}")
    print(f"  Folder  : {settings.folder}")
    print(f"  Poll    : every {settings.schedule}")
    print(f"  Delete  : {settings.delete_after_fetch}")
    print()
    print("Open NiFi (http://localhost:18080/nifi) and start the processors")
    print("when you are ready.")


if __name__ == "__main__":
    main()
