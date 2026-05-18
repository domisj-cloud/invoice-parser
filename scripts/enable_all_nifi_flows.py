#!/usr/bin/env python3
"""Enable and start every processor in every top-level NiFi process group.

Idempotent: runs DISABLED -> STOPPED -> RUNNING transitions only where
needed. Processors that fail validation (e.g. missing credentials, no
matching credentials controller service) are left STOPPED and reported
to stderr so the operator can fix them in the NiFi UI; the script
itself exits 0 unless every group has something invalid.

Designed to be called from `scripts/start_services.sh` after the stack
boots, so the saved flow definitions become live again without manual
clicks. Safe to run on a fresh stack with no process groups (no-op).
"""
from __future__ import annotations

import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_nifi_flow import NiFiClient  # noqa: E402

BASE_URL = "http://localhost:18080/nifi-api"
USERNAME = "admin"
PASSWORD = "adminadminadmin"


def wait_for_nifi_api(timeout_seconds: int = 180) -> NiFiClient:
    """Block until NiFi's REST API is reachable and we can mint a token."""
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            client = NiFiClient(BASE_URL, USERNAME, PASSWORD)
            # Round-trip the root group to confirm the flow controller is
            # actually loaded — token-only doesn't prove that.
            client.root_process_group_id()
            return client
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError,
                RuntimeError, OSError) as exc:
            last_error = exc
            time.sleep(3)
    raise SystemExit(
        f"NiFi API did not become ready within {timeout_seconds}s. "
        f"Last error: {last_error}"
    )


def transition(client: NiFiClient, processor_id: str, target_state: str) -> None:
    processor = client.request("GET", f"/processors/{processor_id}")
    if processor["component"]["state"] == target_state:
        return
    client.request(
        "PUT",
        f"/processors/{processor_id}/run-status",
        {"revision": processor["revision"], "state": target_state},
    )


def enable_group(client: NiFiClient, group_id: str, group_name: str) -> dict:
    """Bring every processor in a group to RUNNING when possible.

    Returns a small report dict with counts for the summary line.
    """
    flow = client.request("GET", f"/flow/process-groups/{group_id}")[
        "processGroupFlow"
    ]["flow"]

    # First pass: DISABLED -> STOPPED. Required before validation runs
    # and before we can move to RUNNING.
    for p in flow["processors"]:
        if p["component"]["state"] == "DISABLED":
            transition(client, p["component"]["id"], "STOPPED")

    # Second pass: STOPPED -> RUNNING, but only if VALID. INVALID
    # processors are typically missing creds or unbound controller
    # services; surface them rather than spamming start attempts.
    started, invalid, already_running = 0, 0, 0
    invalid_names: list[str] = []
    for p in flow["processors"]:
        processor = client.request("GET", f"/processors/{p['component']['id']}")
        state = processor["component"]["state"]
        validation = processor["component"].get("validationStatus")
        name = processor["component"]["name"]
        if state == "RUNNING":
            already_running += 1
            continue
        if validation and validation != "VALID":
            invalid += 1
            invalid_names.append(f"{name} ({validation})")
            continue
        transition(client, p["component"]["id"], "RUNNING")
        started += 1

    return {
        "group": group_name,
        "started": started,
        "already_running": already_running,
        "invalid": invalid,
        "invalid_names": invalid_names,
    }


def main() -> None:
    print("Waiting for NiFi API ...", flush=True)
    client = wait_for_nifi_api()
    print("NiFi API is ready.")

    root_id = client.root_process_group_id()
    root_flow = client.request(
        "GET", f"/flow/process-groups/{root_id}"
    )["processGroupFlow"]["flow"]

    groups = root_flow["processGroups"]
    if not groups:
        print("No NiFi process groups exist yet — nothing to enable.")
        print("Create flows with scripts/create_nifi_flow.py / "
              "create_nifi_email_flow.py / create_nifi_imaps_flow.py.")
        return

    print(f"\nFound {len(groups)} top-level process group(s); enabling ...\n")
    reports = []
    for g in groups:
        gid = g["component"]["id"]
        gname = g["component"]["name"]
        reports.append(enable_group(client, gid, gname))

    print("Summary:")
    any_invalid = False
    for r in reports:
        line = (
            f"  {r['group']:30s} started={r['started']} "
            f"already_running={r['already_running']} invalid={r['invalid']}"
        )
        print(line)
        if r["invalid_names"]:
            any_invalid = True
            for n in r["invalid_names"]:
                print(f"      INVALID: {n}", file=sys.stderr)

    if any_invalid:
        print(
            "\nSome processors are INVALID and were left STOPPED. Fix them "
            "in NiFi (http://localhost:18080/nifi) — most often this means "
            "missing credentials or an unbound controller service.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
