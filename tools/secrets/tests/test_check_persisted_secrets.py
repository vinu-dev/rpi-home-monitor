# REQ: SWR-101-B, SWR-101-C; RISK: RISK-101-1; SEC: SC-101; TEST: TC-101-AC-2, TC-101-AC-10, TC-101-AC-12, TC-101-AC-14
"""Unit tests for the persisted-secret inventory guard."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "check_persisted_secrets.py"
SPEC = importlib.util.spec_from_file_location("check_persisted_secrets", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules.setdefault("check_persisted_secrets", MODULE)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_looks_secret_like_respects_extra_names_and_exclusions():
    assert MODULE.looks_secret_like("tailscale_auth_key") is True
    assert MODULE.looks_secret_like("recovery_code_hashes") is True
    assert MODULE.looks_secret_like("server_public_key") is False


def test_discover_model_secret_fields_maps_known_dataclasses(tmp_path):
    models_path = tmp_path / "models.py"
    models_path.write_text(
        """
from dataclasses import dataclass

@dataclass
class Camera:
    pairing_secret: str = ""
    keyframe_interval: int = 30

@dataclass
class User:
    password_hash: str = ""
    recovery_code_hashes: list[str] = None

@dataclass
class Settings:
    tailscale_auth_key: str = ""
    offsite_backup_access_key_id: str = ""

@dataclass
class WebhookDestination:
    secret: str = ""
""",
        encoding="utf-8",
    )

    assert MODULE.discover_model_secret_fields(models_path) == {
        "cameras.json:keyframe_interval",
        "cameras.json:pairing_secret",
        "users.json:password_hash",
        "users.json:recovery_code_hashes",
        "settings.json:tailscale_auth_key",
        "settings.json:offsite_backup_access_key_id",
        "settings.json:webhook_destinations[].secret",
    }


def test_lint_repo_accepts_inventory_and_known_non_secret_allowlist(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "discover_model_secret_fields",
        lambda _path: {
            "cameras.json:pairing_secret",
            "settings.json:offsite_backup_access_key_id",
        },
    )
    monkeypatch.setattr(
        MODULE,
        "load_settings_secret_fields",
        lambda: {"settings.json:tailscale_auth_key"},
    )

    errors = MODULE.lint_repo(
        {
            "cameras.json:pairing_secret",
            "settings.json:tailscale_auth_key",
        }
    )

    assert errors == []


def test_lint_repo_reports_undeclared_fields(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "discover_model_secret_fields",
        lambda _path: {"users.json:totp_secret"},
    )
    monkeypatch.setattr(MODULE, "load_settings_secret_fields", lambda: set())

    errors = MODULE.lint_repo(set())

    assert "Undeclared persisted-secret fields:" in errors[0]
    assert "users.json:totp_secret" in "\n".join(errors)


def test_lint_runtime_accepts_documented_fields_and_empty_nested_collections(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps(
            {
                "tailscale_auth_key": "",
                "webhook_destinations": [],
                "offsite_backup_secret_access_key": "",
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "users.json").write_text(
        json.dumps(
            [
                {
                    "password_hash": "hash",
                    "recovery_code_hashes": [],
                    "totp_secret": "",
                }
            ]
        ),
        encoding="utf-8",
    )
    (config_dir / "cameras.json").write_text(
        json.dumps([{"pairing_secret": ""}]),
        encoding="utf-8",
    )

    errors = MODULE.lint_runtime(
        {
            "settings.json:tailscale_auth_key",
            "settings.json:webhook_destinations[].secret",
            "settings.json:offsite_backup_secret_access_key",
            "users.json:password_hash",
            "users.json:recovery_code_hashes",
            "users.json:totp_secret",
            "cameras.json:pairing_secret",
        },
        config_dir,
    )

    assert errors == []


def test_lint_runtime_reports_missing_inventory_for_runtime_secret(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"tailscale_auth_key": "tskey-123"}),
        encoding="utf-8",
    )

    errors = MODULE.lint_runtime(set(), config_dir)

    assert "Runtime secret-like config keys missing from inventory:" in errors[0]
    assert "settings.json:tailscale_auth_key" in "\n".join(errors)


def test_lint_runtime_reports_dead_inventory_rows(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"tailscale_auth_key": ""}),
        encoding="utf-8",
    )

    errors = MODULE.lint_runtime(
        {
            "settings.json:tailscale_auth_key",
            "settings.json:nonexistent_secret",
        },
        config_dir,
    )

    assert "Inventory rows referencing missing config-schema paths:" in errors[-2]
    assert "settings.json:nonexistent_secret" in errors[-1]
