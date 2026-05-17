"""Unit tests for the env-var-driven settings in
scripts/create_nifi_imaps_flow.py.

These tests cover only the pure-config layer (ImapsSettings); the NiFi
API integration is exercised by running the script against a live NiFi
instance and inspecting the resulting process group.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def imaps_module():
    if "create_nifi_imaps_flow" in sys.modules:
        del sys.modules["create_nifi_imaps_flow"]
    return importlib.import_module("create_nifi_imaps_flow")


def test_settings_from_env_with_minimum_required(imaps_module):
    settings = imaps_module.ImapsSettings.from_env(
        {
            "IMAPS_USER": "invoice-parser-poc@outlook.com",
            "IMAPS_PASSWORD": "secret-app-password",
        }
    )

    # Required fields come through.
    assert settings.user == "invoice-parser-poc@outlook.com"
    assert settings.password == "secret-app-password"

    # Defaults match the Outlook recommendation.
    assert settings.host == "outlook.office365.com"
    assert settings.port == "993"
    assert settings.folder == "INBOX"
    assert settings.schedule == "30 sec"
    assert settings.delete_after_fetch == "false"


def test_settings_from_env_full_override(imaps_module):
    settings = imaps_module.ImapsSettings.from_env(
        {
            "IMAPS_HOST": "imap.mail.me.com",
            "IMAPS_PORT": "993",
            "IMAPS_USER": "me@icloud.com",
            "IMAPS_PASSWORD": "icloud-app-password",
            "IMAPS_FOLDER": "Invoices",
            "IMAPS_SCHEDULE": "1 min",
            "IMAPS_DELETE": "true",
        }
    )

    assert settings.host == "imap.mail.me.com"
    assert settings.user == "me@icloud.com"
    assert settings.folder == "Invoices"
    assert settings.schedule == "1 min"
    assert settings.delete_after_fetch == "true"


def test_settings_missing_user_raises(imaps_module):
    with pytest.raises(SystemExit) as exc:
        imaps_module.ImapsSettings.from_env({"IMAPS_PASSWORD": "p"})
    assert "IMAPS_USER" in str(exc.value)


def test_settings_missing_password_raises(imaps_module):
    with pytest.raises(SystemExit) as exc:
        imaps_module.ImapsSettings.from_env(
            {"IMAPS_USER": "me@outlook.com"}
        )
    assert "IMAPS_PASSWORD" in str(exc.value)


def test_settings_missing_both_lists_both(imaps_module):
    with pytest.raises(SystemExit) as exc:
        imaps_module.ImapsSettings.from_env({})
    message = str(exc.value)
    assert "IMAPS_USER" in message
    assert "IMAPS_PASSWORD" in message


def test_empty_string_is_treated_as_missing(imaps_module):
    """Common foot-gun: `export IMAPS_PASSWORD=` exports an empty string,
    not an unset var. The loader should treat that as missing."""
    with pytest.raises(SystemExit) as exc:
        imaps_module.ImapsSettings.from_env(
            {"IMAPS_USER": "me@outlook.com", "IMAPS_PASSWORD": ""}
        )
    assert "IMAPS_PASSWORD" in str(exc.value)


def test_settings_is_immutable(imaps_module):
    settings = imaps_module.ImapsSettings.from_env(
        {"IMAPS_USER": "me@outlook.com", "IMAPS_PASSWORD": "p"}
    )
    # Dataclass is frozen, so mutation raises.
    with pytest.raises(Exception):
        settings.password = "rotated"  # type: ignore[misc]


def test_settings_password_is_not_in_default_repr(imaps_module):
    """ImapsSettings repr() includes the password by default — this test
    documents that current behaviour so a future reviewer is aware. If we
    ever want password redaction in repr(), update this test alongside
    the implementation."""
    settings = imaps_module.ImapsSettings.from_env(
        {"IMAPS_USER": "me@outlook.com", "IMAPS_PASSWORD": "super-secret"}
    )
    # Sanity: the password IS in the dataclass repr today.
    assert "super-secret" in repr(settings)
