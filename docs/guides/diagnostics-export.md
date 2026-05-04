---
title: Diagnostics Export Guide
status: active
audience: [human, ai]
owner: engineering
source_of_truth: false
---

# Diagnostics Export

Use the Settings -> System -> Diagnostics card to download a support bundle from
the server without shell access.

## What It Does

`POST /api/v1/system/diagnostics/export` builds a timestamped
`hm-diagnostics-<host>-<utc>.tar.gz` bundle for authenticated admins. The
bundle is staged under `/data/config/diagnostics-staging/`, streamed back to
the browser, then removed when the download closes. A fallback cleanup timer
removes stale staging directories if a client disconnects mid-transfer.

The route is governed by:

- `SWR-068` for the admin-only, CSRF-protected, rate-limited download surface
- `SWR-069` for bundle contents, redaction, and sensitive-path exclusions
- `SWR-070` for size/time bounds, cleanup, and audit behavior

## Bundle Layout

The archive contains a single top-level directory with:

- `manifest.json`
- `logs/`
- `config/`
- `hardware/`
- `network/`
- `systemd/`
- `identity/`

`manifest.json` records the bundle version, generation timestamp, sanitized
host, firmware version, requester label, section summaries, redaction notes,
tool-version probes, and whether collection aborted early because of the
overall timeout.

## Redaction And Exclusions

The export is intentionally not a raw file dump.

Redacted config fields currently include:

- `users[*].password_hash`
- `users[*].totp_secret`
- `users[*].recovery_code_hashes`
- `cameras[*].pairing_secret`
- `settings.tailscale_auth_key`
- `settings.offsite_backup_access_key_id`
- `settings.offsite_backup_secret_access_key`
- `settings.webhook_destinations[*].secret`
- `settings.webhook_destinations[*].custom_headers`

The bundle does not include:

- `/data/config/.secret_key`
- `/data/certs/**`
- `/data/recordings/**`

If a new secret-bearing field is added to a traced model, update
`app/server/monitor/utils/redact.py` and the diagnostics security tests in the
same change.

## Bounds And Failure Behavior

The export is intentionally bounded to protect the appliance:

- one export at a time per process
- per-session rate limiting
- per-tool subprocess timeouts
- per-section size caps
- total bundle cap
- overall collection timeout

If an OS command is unavailable or times out, the bundle still ships with a
marker file and manifest error for that section. If staging fails entirely, the
route returns a structured JSON error instead of a partial archive.

## Audit Events

Each completed request records one of:

- `DIAGNOSTICS_EXPORTED`
- `DIAGNOSTICS_EXPORT_FAILED`

Use the audit log when you need to confirm who exported a bundle and whether a
failed operator report was caused by staging, timeout, or rate-limiting
behavior.

## Validation

For code or behavior changes in this area, run:

```bash
pytest app/server/tests/unit/test_diagnostics_bundle.py app/server/tests/integration/test_api_diagnostics_export.py app/server/tests/security/test_diagnostics_redaction.py app/server/tests/contracts/test_api_contracts.py -k diagnostics -q
python tools/traceability/check_traceability.py
```

For full implementer validation, also run the repo-governance, Ruff,
pre-commit, link, and full server-test commands listed in
[`testing-guide.md`](testing-guide.md) and `docs/ai/roles/implementer.md`.

## Troubleshooting

- `429 diagnostics_export_in_progress`: another export is still running.
- `429 diagnostics_export_rate_limited`: the current session exceeded the
  hourly limit; respect the `Retry-After` header.
- `503 diagnostics_staging_failed`: `/data/config` could not stage the bundle,
  usually because of disk or permission issues.
- Bundle missing a section: inspect `manifest.json` for `error`,
  `truncated`, or `aborted` flags before assuming the route regressed.
