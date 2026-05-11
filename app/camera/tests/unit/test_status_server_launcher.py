# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
"""Tests for the browser-test camera status launcher."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_launcher():
    repo_root = Path(__file__).resolve().parents[4]
    script = repo_root / "scripts" / "testing" / "run_camera_status_server.py"
    spec = importlib.util.spec_from_file_location("run_camera_status_server", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_generates_test_tls_when_openssl_is_missing(tmp_path, monkeypatch):
    launcher = _load_launcher()
    cfg = SimpleNamespace(certs_dir=str(tmp_path / "certs"))
    cert_path = tmp_path / "certs" / "status-test.crt"
    key_path = tmp_path / "certs" / "status-test.key"

    monkeypatch.setattr(launcher.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        launcher.status_server_module,
        "_status_tls_paths",
        lambda _cfg: (str(cert_path), str(key_path)),
    )

    launcher._install_tls_fallback_when_openssl_is_missing(cfg)

    generated_cert, generated_key = launcher.status_server_module._ensure_tls_material(
        cfg
    )
    assert generated_cert == str(cert_path)
    assert generated_key == str(key_path)
    assert cert_path.read_text(encoding="ascii").startswith(
        "-----BEGIN CERTIFICATE-----"
    )
    assert key_path.read_text(encoding="ascii").startswith(
        "-----BEGIN PRIVATE KEY-----"
    )
