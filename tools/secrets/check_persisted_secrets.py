#!/usr/bin/env python3
"""Persisted-secret inventory guard.

REQ: SWR-101-B, SWR-101-C; RISK: RISK-101-1; SEC: SC-101; TEST: TC-101-AC-2, TC-101-AC-10, TC-101-AC-12, TC-101-AC-14

This tool has two modes:

1. Default (pre-commit / CI): scan persisted model fields plus
   ``monitor.services.settings_service.SECRET_FIELDS`` and fail if a
   secret-like field is missing from ``docs/operations/secrets-inventory.md``
   and is not on ``KNOWN_NON_SECRET_FIELDS`` with a justification.
2. Runtime smoke mode (``--runtime-config-dir``): scan real
   ``/data/config/*.json`` content and fail if secret-like runtime keys are not
   documented in the inventory, or if the inventory references config-schema
   keys that do not exist.

Canonical inventory rows use ``field: <path>`` lines, for example:

- ``field: settings.json:tailscale_auth_key``
- ``field: settings.json:webhook_destinations[].secret``
- ``field: users.json:password_hash``

Secret-like field matching is intentionally conservative:

- suspect substrings: ``secret``, ``password``, ``token``, ``key``
- extra exact-name coverage: ``recovery_code_hashes``
- explicit exclusions: names such as ``public_key`` or
  ``pairing_secret_hash`` are never treated as persisted secrets

If a field is not actually sensitive but still matches the heuristic, add it to
``KNOWN_NON_SECRET_FIELDS`` with a one-line justification instead of bypassing
the hook.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS_PATH = ROOT / "app/server/monitor/models.py"
DEFAULT_INVENTORY_PATH = ROOT / "docs/operations/secrets-inventory.md"

SUSPECT_SUBSTRINGS = ("secret", "password", "token", "key")
EXCLUDED_FIELD_NAMES = ("public_key", "pairing_secret_hash")
ADDITIONAL_SUSPECT_FIELDS = {"recovery_code_hashes"}

MODEL_FIELD_PREFIXES = {
    "Camera": "cameras.json:",
    "User": "users.json:",
    "Settings": "settings.json:",
    "WebhookDestination": "settings.json:webhook_destinations[].",
}

KNOWN_NON_SECRET_FIELDS = {
    "cameras.json:keyframe_interval": "H.264 GOP interval setting, not credential material.",
    "settings.json:offsite_backup_access_key_id": "Access key identifier only; authentication requires the secret key.",
    "users.json:must_change_password": "Password-rotation policy flag, not secret material.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inventory",
        default=str(DEFAULT_INVENTORY_PATH),
        help="Path to docs/operations/secrets-inventory.md",
    )
    parser.add_argument(
        "--runtime-config-dir",
        help="Optional runtime config directory to validate (for smoke-test use).",
    )
    return parser.parse_args()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalize_path(path: str) -> str:
    return path.strip().lstrip("-* ").strip()


def load_inventory_fields(path: Path) -> set[str]:
    if not path.exists():
        raise FileNotFoundError(f"Missing inventory file: {path}")

    fields: set[str] = set()
    for raw_line in read_text(path).splitlines():
        line = raw_line.strip()
        if "field:" not in line:
            continue
        prefix, value = line.split("field:", 1)
        if prefix.strip() not in {"", "-", "*"}:
            continue
        normalized = normalize_path(value)
        if normalized:
            fields.add(normalized)
    return fields


def looks_secret_like(name: str) -> bool:
    lowered = name.lower()
    if lowered in ADDITIONAL_SUSPECT_FIELDS:
        return True
    if any(excluded in lowered for excluded in EXCLUDED_FIELD_NAMES):
        return False
    return any(token in lowered for token in SUSPECT_SUBSTRINGS)


def load_settings_secret_fields() -> set[str]:
    app_server_root = ROOT / "app/server"
    sys.path.insert(0, str(app_server_root))
    try:
        from monitor.services.settings_service import SECRET_FIELDS

        return set(SECRET_FIELDS)
    except Exception:
        return parse_secret_fields_from_ast(
            ROOT / "app/server/monitor/services/settings_service.py"
        )
    finally:
        try:
            sys.path.remove(str(app_server_root))
        except ValueError:
            pass


def parse_secret_fields_from_ast(path: Path) -> set[str]:
    tree = ast.parse(read_text(path), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SECRET_FIELDS":
                    return extract_string_literals(node.value)
    return set()


def extract_string_literals(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        values: set[str] = set()
        for element in node.elts:
            values.update(extract_string_literals(element))
        return values
    if isinstance(node, ast.Call) and node.args:
        return extract_string_literals(node.args[0])
    return set()


def discover_model_secret_fields(path: Path) -> set[str]:
    tree = ast.parse(read_text(path), filename=str(path))
    candidates: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        prefix = MODEL_FIELD_PREFIXES.get(node.name)
        if prefix is None:
            continue
        for child in node.body:
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                field_name = child.target.id
                if looks_secret_like(field_name):
                    candidates.add(f"{prefix}{field_name}")
    return candidates


def lint_repo(inventory_fields: set[str]) -> list[str]:
    errors: list[str] = []
    candidates = (
        discover_model_secret_fields(MODELS_PATH) | load_settings_secret_fields()
    )

    undeclared = sorted(
        path
        for path in candidates
        if path not in inventory_fields and path not in KNOWN_NON_SECRET_FIELDS
    )
    if undeclared:
        errors.append("Undeclared persisted-secret fields:")
        for path in undeclared:
            errors.append(f"  - {path}")
        errors.append(
            "Add a matching `field:` row to docs/operations/secrets-inventory.md "
            "or an allowlist justification to KNOWN_NON_SECRET_FIELDS."
        )
    return errors


def canonicalize_runtime_path(filename: str, segments: list[str]) -> str:
    if not segments:
        return filename
    rendered = ""
    for segment in segments:
        if segment == "[]":
            rendered += "[]"
        elif not rendered:
            rendered = segment
        else:
            rendered += f".{segment}"
    return f"{filename}:{rendered}"


def walk_runtime_paths(
    filename: str,
    value,
    segments: list[str] | None = None,
    *,
    all_paths: set[str],
    candidate_paths: set[str],
) -> None:
    segments = segments or []
    if isinstance(value, dict):
        for key, child in value.items():
            next_segments = [*segments, key]
            all_paths.add(canonicalize_runtime_path(filename, next_segments))
            if looks_secret_like(key):
                candidate_paths.add(canonicalize_runtime_path(filename, next_segments))
            walk_runtime_paths(
                filename,
                child,
                next_segments,
                all_paths=all_paths,
                candidate_paths=candidate_paths,
            )
        return

    if isinstance(value, list):
        if segments:
            list_path = canonicalize_runtime_path(filename, segments)
            if segments[-1] in ADDITIONAL_SUSPECT_FIELDS:
                candidate_paths.add(list_path)
        for child in value:
            if isinstance(child, dict):
                next_segments = [*segments, "[]"] if segments else segments
                walk_runtime_paths(
                    filename,
                    child,
                    next_segments,
                    all_paths=all_paths,
                    candidate_paths=candidate_paths,
                )
        return

    if (
        isinstance(value, str)
        and segments
        and segments[-1] in ADDITIONAL_SUSPECT_FIELDS
    ):
        candidate_paths.add(canonicalize_runtime_path(filename, segments))


def scan_runtime_config(config_dir: Path) -> tuple[set[str], set[str]]:
    all_paths: set[str] = set()
    candidate_paths: set[str] = set()
    for json_path in sorted(config_dir.glob("*.json")):
        try:
            payload = json.loads(read_text(json_path))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to parse {json_path}: {exc}") from exc
        walk_runtime_paths(
            json_path.name,
            payload,
            all_paths=all_paths,
            candidate_paths=candidate_paths,
        )
    return all_paths, candidate_paths


def runtime_path_is_covered(path: str, all_paths: set[str]) -> bool:
    if path in all_paths:
        return True
    if "[]." in path:
        parent = path.split("[].", 1)[0]
        return parent in all_paths
    return False


def lint_runtime(inventory_fields: set[str], config_dir: Path) -> list[str]:
    errors: list[str] = []
    all_paths, candidate_paths = scan_runtime_config(config_dir)

    missing_runtime = sorted(
        path
        for path in candidate_paths
        if path not in inventory_fields and path not in KNOWN_NON_SECRET_FIELDS
    )
    if missing_runtime:
        errors.append("Runtime secret-like config keys missing from inventory:")
        for path in missing_runtime:
            errors.append(f"  - {path}")

    inventory_config_fields = sorted(
        path for path in inventory_fields if path.endswith(".json") or ".json:" in path
    )
    dead_rows = sorted(
        path
        for path in inventory_config_fields
        if path not in KNOWN_NON_SECRET_FIELDS
        and not runtime_path_is_covered(path, all_paths)
    )
    if dead_rows:
        errors.append("Inventory rows referencing missing config-schema paths:")
        for path in dead_rows:
            errors.append(f"  - {path}")

    return errors


def main() -> int:
    args = parse_args()
    inventory_path = Path(args.inventory)
    try:
        inventory_fields = load_inventory_fields(inventory_path)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    errors = lint_repo(inventory_fields)
    if args.runtime_config_dir:
        errors.extend(lint_runtime(inventory_fields, Path(args.runtime_config_dir)))

    if errors:
        print("Persisted-secret inventory check failed:")
        for line in errors:
            print(line)
        return 1

    print("Persisted-secret inventory check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
