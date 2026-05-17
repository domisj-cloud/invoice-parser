#!/usr/bin/env python3
"""Rotate the IMAPS password on the running NiFi `Invoice IMAPS Demo` flow.

Use this script after generating a new app password in your mail provider
(see docs/email-ingestion.md for the manual steps). It updates only the
`password` property on the ConsumeIMAP processor; no other configuration
is touched.

Workflow:
  1. Stop ConsumeIMAP if it is currently RUNNING/STARTING.
  2. PUT the new password as a NiFi sensitive property
     (encrypted at rest, redacted in UI and provenance).
  3. Optionally restart the processor with --start.

Sources for the new password, in order of precedence:
  - `--password-file <path>`   (recommended: read from a file, never argv)
  - `IMAPS_PASSWORD_NEW` env var
  - interactive prompt via getpass (no echo)

Examples:
    # Interactive — safest; nothing in shell history
    python3 scripts/rotate_imaps_password.py --start

    # From a file (e.g. piped from a password manager)
    op read 'op://Personal/Outlook PoC/app password' > /tmp/p && \
        python3 scripts/rotate_imaps_password.py --password-file /tmp/p --start && \
        shred -u /tmp/p
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_nifi_flow import NiFiClient  # noqa: E402

BASE_URL = "http://localhost:18080/nifi-api"
USERNAME = "admin"
PASSWORD = "adminadminadmin"
PROCESS_GROUP_NAME = "Invoice IMAPS Demo"
PROCESSOR_NAME = "Poll mailbox (IMAPS)"
PASSWORD_PROPERTY_KEY = "password"  # NiFi 2.x canonical key (sensitive)


def load_new_password(args: argparse.Namespace, env: dict[str, str]) -> str:
    if args.password_file:
        path = Path(args.password_file)
        if not path.exists():
            raise SystemExit(f"Password file not found: {path}")
        value = path.read_text().strip()
        if not value:
            raise SystemExit(f"Password file is empty: {path}")
        return value
    env_value = (env.get("IMAPS_PASSWORD_NEW") or "").strip()
    if env_value:
        return env_value
    # Interactive fallback — getpass disables terminal echo.
    value = getpass.getpass("New IMAPS app password: ").strip()
    if not value:
        raise SystemExit("No password supplied; aborting.")
    confirm = getpass.getpass("Confirm new password: ").strip()
    if value != confirm:
        raise SystemExit("Passwords do not match; aborting.")
    return value


def find_processor(client: NiFiClient, group_name: str, proc_name: str) -> str:
    root_id = client.root_process_group_id()
    root_flow = client.request(
        "GET", f"/flow/process-groups/{root_id}"
    )["processGroupFlow"]["flow"]
    group = next(
        (g for g in root_flow["processGroups"]
         if g["component"]["name"] == group_name),
        None,
    )
    if group is None:
        raise SystemExit(
            f"Process group {group_name!r} not found. Run "
            "scripts/create_nifi_imaps_flow.py first."
        )
    group_id = group["component"]["id"]
    sub_flow = client.request(
        "GET", f"/flow/process-groups/{group_id}"
    )["processGroupFlow"]["flow"]
    proc = next(
        (p for p in sub_flow["processors"]
         if p["component"]["name"] == proc_name),
        None,
    )
    if proc is None:
        raise SystemExit(
            f"Processor {proc_name!r} not found in group {group_name!r}."
        )
    return proc["component"]["id"]


def stop_processor_if_running(client: NiFiClient, processor_id: str) -> str:
    """Move a RUNNING/STARTING processor to STOPPED. Returns the original
    state so the caller can decide whether to restart afterward."""
    processor = client.request("GET", f"/processors/{processor_id}")
    state = processor["component"]["state"]
    if state in ("STOPPED", "DISABLED"):
        return state
    client.request(
        "PUT",
        f"/processors/{processor_id}/run-status",
        {"revision": processor["revision"], "state": "STOPPED"},
    )
    return state


def update_password(client: NiFiClient, processor_id: str, new_password: str) -> None:
    """Update only the password property; leave everything else untouched."""
    processor = client.request("GET", f"/processors/{processor_id}")
    client.request(
        "PUT",
        f"/processors/{processor_id}",
        {
            "revision": processor["revision"],
            "component": {
                "id": processor_id,
                "config": {
                    "properties": {PASSWORD_PROPERTY_KEY: new_password},
                },
            },
        },
    )


def start_processor(client: NiFiClient, processor_id: str) -> None:
    processor = client.request("GET", f"/processors/{processor_id}")
    client.request(
        "PUT",
        f"/processors/{processor_id}/run-status",
        {"revision": processor["revision"], "state": "RUNNING"},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--password-file",
        help="Path to a file containing the new password (one line). "
             "Trimmed and treated as-is; safer than env or argv.",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="After rotating, attempt to start the ConsumeIMAP processor. "
             "If validation still fails, the processor stays STOPPED and "
             "NiFi reports the reason in its UI/bulletins.",
    )
    args = parser.parse_args()

    new_password = load_new_password(args, os.environ)

    client = NiFiClient(BASE_URL, USERNAME, PASSWORD)
    processor_id = find_processor(client, PROCESS_GROUP_NAME, PROCESSOR_NAME)

    previous_state = stop_processor_if_running(client, processor_id)
    update_password(client, processor_id, new_password)

    print(f"Password rotated for {PROCESSOR_NAME} (previous state: {previous_state}).")

    if args.start:
        try:
            start_processor(client, processor_id)
            print("Processor started.")
        except Exception as exc:
            print(
                f"Could not start processor: {exc}\n"
                "Check NiFi bulletins (or run scripts/diagnose_imaps_flow.py) "
                "for validation errors.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        print(
            "Processor left STOPPED. Start it from NiFi UI, or re-run with "
            "--start once you're ready."
        )


if __name__ == "__main__":
    main()
