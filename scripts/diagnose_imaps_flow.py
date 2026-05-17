#!/usr/bin/env python3
"""Diagnostic for the Invoice IMAPS Demo process group.

Reports:
  - Whether the process group exists
  - State (RUNNING/STOPPED/DISABLED) of each processor
  - Queued flowfile counts on each connection
  - Latest bulletins (errors/warnings) from any processor in the group
  - The current ConsumeIMAP configuration (password redacted)

Run with the same NiFi credentials as the flow scripts.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_nifi_flow import NiFiClient  # noqa: E402

BASE_URL = "http://localhost:18080/nifi-api"
USERNAME = "admin"
PASSWORD = "adminadminadmin"
PROCESS_GROUP_NAME = "Invoice IMAPS Demo"


def find_group(client: NiFiClient, name: str) -> dict | None:
    root_id = client.root_process_group_id()
    flow = client.request("GET", f"/flow/process-groups/{root_id}")[
        "processGroupFlow"
    ]["flow"]
    for group in flow["processGroups"]:
        if group["component"]["name"] == name:
            return group
    return None


def main() -> None:
    client = NiFiClient(BASE_URL, USERNAME, PASSWORD)
    group = find_group(client, PROCESS_GROUP_NAME)
    if group is None:
        print(f"Process group {PROCESS_GROUP_NAME!r} does NOT exist.")
        print("Run: python3 scripts/create_nifi_imaps_flow.py")
        return

    group_id = group["component"]["id"]
    print(f"Process group: {PROCESS_GROUP_NAME}  (id={group_id})")
    print()

    # Processor states + queue depths
    flow = client.request("GET", f"/flow/process-groups/{group_id}")[
        "processGroupFlow"
    ]["flow"]

    print("Processors:")
    print(f"  {'NAME':45s} {'STATE':10s} {'IN':>6s} {'OUT':>6s}")
    for processor in flow["processors"]:
        comp = processor["component"]
        status = processor.get("status", {}).get("aggregateSnapshot", {})
        print(
            f"  {comp['name']:45s} "
            f"{comp.get('state','?'):10s} "
            f"{status.get('flowFilesIn', 0):6d} "
            f"{status.get('flowFilesOut', 0):6d}"
        )

    print()
    print("Connections (queued flowfiles):")
    for conn in flow["connections"]:
        comp = conn["component"]
        status = conn.get("status", {}).get("aggregateSnapshot", {})
        print(
            f"  {comp['source']['name']} -> {comp['destination']['name']}: "
            f"{status.get('flowFilesQueued', 0)} queued"
        )

    print()
    print("Recent bulletins (errors / warnings) in this group:")
    bulletins = client.request(
        "GET",
        f"/flow/process-groups/{group_id}/controller-services?includeAncestorGroups=false",
    )
    # Use the bulletin board API filtered by group id
    board = client.request(
        "GET",
        f"/flow/bulletin-board?groupId={group_id}",
    )["bulletinBoard"]["bulletins"]
    if not board:
        print("  (none)")
    else:
        for b in board[-20:]:
            bb = b["bulletin"]
            print(
                f"  [{bb.get('timestamp','?')}] "
                f"{bb.get('level','?')} "
                f"{bb.get('sourceName','?')}: "
                f"{bb.get('message','?')}"
            )

    print()
    print("ConsumeIMAP configuration (canonical NiFi 2.x keys; password redacted):")
    for processor in flow["processors"]:
        if processor["component"]["name"] == "Poll mailbox (IMAPS)":
            props = processor["component"]["config"]["properties"]
            for key in ("host", "port", "user", "folder",
                        "Use SSL", "delete.messages",
                        "Mark Messages as Read", "authorization-mode"):
                print(f"  {key:30s} = {props.get(key)}")
            print(f"  {'password':30s} = ********")
            schedule = (
                processor["component"]["config"].get("schedulingPeriod")
            )
            print(f"  {'schedulingPeriod':30s} = {schedule}")
            # Validation status surfaces missing-required-property issues
            # without needing the bulletin board.
            comp = processor["component"]
            print(f"  {'validationStatus':30s} = "
                  f"{comp.get('validationStatus', '?')}")
            for err in comp.get("validationErrors") or []:
                print(f"    validation err: {err}")
            break


if __name__ == "__main__":
    main()
