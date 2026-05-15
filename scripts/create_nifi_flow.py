#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "http://localhost:18080/nifi-api"
USERNAME = "admin"
PASSWORD = "adminadminadmin"
PROCESS_GROUP_NAME = "Invoice PDF Demo"


def main() -> None:
    client = NiFiClient(BASE_URL, USERNAME, PASSWORD)
    root_id = client.root_process_group_id()
    group_id = client.ensure_process_group(root_id, PROCESS_GROUP_NAME)
    credentials_id = client.ensure_minio_credentials(group_id)
    processor_ids = client.ensure_processors(group_id)

    client.disable_controller_service(credentials_id)
    client.configure_minio_credentials(credentials_id)
    client.enable_controller_service(credentials_id)
    client.configure_processors(processor_ids, credentials_id)
    client.ensure_connections(group_id, processor_ids)
    client.disable_processors(processor_ids)

    print(f"Created or updated NiFi process group: {PROCESS_GROUP_NAME}")
    print(f"Process group id: {group_id}")
    print("All processors are DISABLED for review.")


class NiFiClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        token_body = urllib.parse.urlencode(
            {"username": username, "password": password}
        ).encode()
        token_request = urllib.request.Request(
            f"{self.base_url}/access/token",
            data=token_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(token_request) as response:
            self.token = response.read().decode()

    def request(self, method: str, path: str, data: dict | None = None) -> dict:
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                text = response.read().decode()
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            raise RuntimeError(f"NiFi API {method} {path} failed: {detail}") from exc

    def root_process_group_id(self) -> str:
        response = self.request("GET", "/flow/process-groups/root")
        return response["processGroupFlow"]["id"]

    def ensure_process_group(self, root_id: str, name: str) -> str:
        flow = self.request("GET", f"/flow/process-groups/{root_id}")[
            "processGroupFlow"
        ]["flow"]
        for group in flow["processGroups"]:
            if group["component"]["name"] == name:
                return group["component"]["id"]

        response = self.request(
            "POST",
            f"/process-groups/{root_id}/process-groups",
            {
                "revision": {"version": 0},
                "component": {
                    "name": name,
                    "position": {"x": 100.0, "y": 100.0},
                },
            },
        )
        return response["id"]

    def ensure_minio_credentials(self, group_id: str) -> str:
        services = self.request(
            "GET", f"/flow/process-groups/{group_id}/controller-services"
        )["controllerServices"]
        for service in services:
            if service["component"]["name"] == "MinIO credentials":
                return service["component"]["id"]

        response = self.request(
            "POST",
            f"/process-groups/{group_id}/controller-services",
            {
                "revision": {"version": 0},
                "component": {
                    "name": "MinIO credentials",
                    "type": "org.apache.nifi.processors.aws.credentials.provider.service.AWSCredentialsProviderControllerService",
                    "bundle": aws_bundle(),
                },
            },
        )
        return response["id"]

    def configure_minio_credentials(self, service_id: str) -> None:
        service = self.request("GET", f"/controller-services/{service_id}")
        self.request(
            "PUT",
            f"/controller-services/{service_id}",
            {
                "revision": service["revision"],
                "component": {
                    "id": service_id,
                    "properties": {
                        "default-credentials": "false",
                        "Access Key": "minioadmin",
                        "Secret Key": "minioadmin",
                        "anonymous-credentials": "false",
                    },
                },
            },
        )

    def disable_controller_service(self, service_id: str) -> None:
        service = self.request("GET", f"/controller-services/{service_id}")
        if service["component"]["state"] == "DISABLED":
            return
        self.request(
            "PUT",
            f"/controller-services/{service_id}/run-status",
            {"revision": service["revision"], "state": "DISABLED"},
        )

    def enable_controller_service(self, service_id: str) -> None:
        service = self.request("GET", f"/controller-services/{service_id}")
        if service["component"]["state"] == "ENABLED":
            return
        self.request(
            "PUT",
            f"/controller-services/{service_id}/run-status",
            {"revision": service["revision"], "state": "ENABLED"},
        )

    def ensure_processors(self, group_id: str) -> dict[str, str]:
        specs = [
            (
                "Get invoice PDFs",
                "org.apache.nifi.processors.standard.GetFile",
                standard_bundle(),
                100,
                100,
            ),
            (
                "Set object key",
                "org.apache.nifi.processors.attributes.UpdateAttribute",
                {
                    "group": "org.apache.nifi",
                    "artifact": "nifi-update-attribute-nar",
                    "version": "2.3.0",
                },
                450,
                100,
            ),
            (
                "Put PDF to inv-input",
                "org.apache.nifi.processors.aws.s3.PutS3Object",
                aws_bundle(),
                800,
                100,
            ),
            (
                "Build parser event JSON",
                "org.apache.nifi.processors.standard.ReplaceText",
                standard_bundle(),
                1150,
                100,
            ),
            (
                "Call parser service",
                "org.apache.nifi.processors.standard.InvokeHTTP",
                standard_bundle(),
                1500,
                100,
            ),
        ]
        flow = self.request("GET", f"/flow/process-groups/{group_id}")[
            "processGroupFlow"
        ]["flow"]
        existing = {
            processor["component"]["name"]: processor["component"]["id"]
            for processor in flow["processors"]
        }
        processor_ids: dict[str, str] = {}
        for name, processor_type, bundle, x, y in specs:
            if name in existing:
                processor_ids[name] = existing[name]
                continue
            response = self.request(
                "POST",
                f"/process-groups/{group_id}/processors",
                {
                    "revision": {"version": 0},
                    "component": {
                        "name": name,
                        "type": processor_type,
                        "bundle": bundle,
                        "position": {"x": x, "y": y},
                    },
                },
            )
            processor_ids[name] = response["id"]
        return processor_ids

    def configure_processors(
        self, processor_ids: dict[str, str], credentials_id: str
    ) -> None:
        self.configure_processor(
            processor_ids["Get invoice PDFs"],
            {
                "Input Directory": "/opt/nifi/inbox",
                "File Filter": ".*\\.pdf",
                "Keep Source File": "true",
                "Recurse Subdirectories": "false",
            },
        )
        self.configure_processor(
            processor_ids["Set object key"], {"s3.object.key": "${filename}"}
        )
        self.configure_processor(
            processor_ids["Put PDF to inv-input"],
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
        self.configure_processor(
            processor_ids["Build parser event JSON"],
            {
                "Replacement Strategy": "Always Replace",
                "Evaluation Mode": "Entire text",
                "Replacement Value": '{"bucket":"inv-input","object_key":"${s3.object.key}"}',
            },
            ["failure"],
        )
        self.configure_processor(
            processor_ids["Call parser service"],
            {
                "HTTP Method": "POST",
                "HTTP URL": "http://parser-service:8000/events/invoice-uploaded",
                "Request Content-Type": "application/json",
                "Response Cookie Strategy": "DISABLED",
                "Response FlowFile Naming Strategy": "RANDOM",
            },
            ["Failure", "No Retry", "Original", "Response", "Retry"],
        )

    def configure_processor(
        self,
        processor_id: str,
        properties: dict[str, str],
        auto_terminated: list[str] | None = None,
    ) -> None:
        processor = self.request("GET", f"/processors/{processor_id}")
        config: dict[str, object] = {"properties": properties}
        if auto_terminated is not None:
            config["autoTerminatedRelationships"] = auto_terminated
        self.request(
            "PUT",
            f"/processors/{processor_id}",
            {
                "revision": processor["revision"],
                "component": {"id": processor_id, "config": config},
            },
        )

    def ensure_connections(
        self, group_id: str, processor_ids: dict[str, str]
    ) -> None:
        flow = self.request("GET", f"/flow/process-groups/{group_id}")[
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
            ("Get invoice PDFs", "Set object key", "success"),
            ("Set object key", "Put PDF to inv-input", "success"),
            ("Put PDF to inv-input", "Build parser event JSON", "success"),
            ("Build parser event JSON", "Call parser service", "success"),
        ]
        for source, destination, relationship in links:
            source_id = processor_ids[source]
            destination_id = processor_ids[destination]
            key = (source_id, destination_id, (relationship,))
            if key in existing:
                continue
            self.request(
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

    def disable_processors(self, processor_ids: dict[str, str]) -> None:
        for processor_id in processor_ids.values():
            processor = self.request("GET", f"/processors/{processor_id}")
            if processor["component"]["state"] == "DISABLED":
                continue
            self.request(
                "PUT",
                f"/processors/{processor_id}/run-status",
                {"revision": processor["revision"], "state": "DISABLED"},
            )


def standard_bundle() -> dict[str, str]:
    return {
        "group": "org.apache.nifi",
        "artifact": "nifi-standard-nar",
        "version": "2.3.0",
    }


def aws_bundle() -> dict[str, str]:
    return {
        "group": "org.apache.nifi",
        "artifact": "nifi-aws-nar",
        "version": "2.3.0",
    }


if __name__ == "__main__":
    main()
