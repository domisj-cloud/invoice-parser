"""Unit tests for scripts/rotate_imaps_password.py — the password-loading
precedence layer (file > env > interactive prompt)."""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def rotate_module():
    if "rotate_imaps_password" in sys.modules:
        del sys.modules["rotate_imaps_password"]
    return importlib.import_module("rotate_imaps_password")


def _args(**overrides) -> argparse.Namespace:
    defaults = {"password_file": None, "start": False}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_password_file_takes_precedence_over_env(
    rotate_module, tmp_path, monkeypatch
):
    pw_file = tmp_path / "pw"
    pw_file.write_text("from-file\n")
    # getpass should NOT be called; if it is, the test fails clearly.
    monkeypatch.setattr(
        rotate_module.getpass, "getpass",
        lambda *_: pytest.fail("getpass must not be invoked when file is set"),
    )

    value = rotate_module.load_new_password(
        _args(password_file=str(pw_file)),
        {"IMAPS_PASSWORD_NEW": "from-env"},
    )
    assert value == "from-file"


def test_password_file_is_trimmed(rotate_module, tmp_path, monkeypatch):
    pw_file = tmp_path / "pw"
    pw_file.write_text("   trimmed-secret   \n\n")
    monkeypatch.setattr(rotate_module.getpass, "getpass", lambda *_: "")

    value = rotate_module.load_new_password(
        _args(password_file=str(pw_file)),
        {},
    )
    assert value == "trimmed-secret"


def test_missing_password_file_raises(rotate_module, tmp_path):
    with pytest.raises(SystemExit) as exc:
        rotate_module.load_new_password(
            _args(password_file=str(tmp_path / "nope")),
            {},
        )
    assert "not found" in str(exc.value)


def test_empty_password_file_raises(rotate_module, tmp_path):
    pw_file = tmp_path / "pw"
    pw_file.write_text("   \n")
    with pytest.raises(SystemExit) as exc:
        rotate_module.load_new_password(
            _args(password_file=str(pw_file)),
            {},
        )
    assert "empty" in str(exc.value)


def test_env_used_when_no_file(rotate_module, monkeypatch):
    monkeypatch.setattr(
        rotate_module.getpass, "getpass",
        lambda *_: pytest.fail("getpass must not be invoked when env is set"),
    )
    value = rotate_module.load_new_password(
        _args(),
        {"IMAPS_PASSWORD_NEW": "from-env"},
    )
    assert value == "from-env"


def test_env_empty_falls_back_to_prompt(rotate_module, monkeypatch):
    prompts: list[str] = []

    def fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        # Confirmation prompt returns the same value so the rotate code
        # accepts it.
        return "interactive-secret"

    monkeypatch.setattr(rotate_module.getpass, "getpass", fake_getpass)

    value = rotate_module.load_new_password(
        _args(),
        {"IMAPS_PASSWORD_NEW": "   "},
    )
    assert value == "interactive-secret"
    # New + confirm = two prompts.
    assert len(prompts) == 2


def test_interactive_mismatch_raises(rotate_module, monkeypatch):
    answers = iter(["first", "second"])
    monkeypatch.setattr(
        rotate_module.getpass, "getpass", lambda *_: next(answers)
    )
    with pytest.raises(SystemExit) as exc:
        rotate_module.load_new_password(_args(), {})
    assert "do not match" in str(exc.value)


def test_interactive_empty_raises(rotate_module, monkeypatch):
    monkeypatch.setattr(rotate_module.getpass, "getpass", lambda *_: "")
    with pytest.raises(SystemExit) as exc:
        rotate_module.load_new_password(_args(), {})
    assert "No password" in str(exc.value)


def test_password_property_key_is_canonical_lowercase(rotate_module):
    """Regression guard: the bug we hit in production was using the legacy
    display-name keys ("Password", "Host Name") instead of the NiFi 2.x
    canonical lowercase keys. Pin the canonical key here so a refactor
    can't silently regress it."""
    assert rotate_module.PASSWORD_PROPERTY_KEY == "password"
