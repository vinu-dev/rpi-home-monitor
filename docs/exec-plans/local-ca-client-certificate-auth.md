# Local CA Client-Certificate Auth Implementation

## Goal

Implement the first safe vertical slice of local/offline-CA client certificate
authentication for the server GUI without changing default password behavior.

## Non-Goals

- Do not merge to `main`.
- Do not remove the existing password/session auth path in this slice.
- Do not generate or commit real CA keys, RPI keys, client certs, `.p12`, or
  secrets.
- Do not implement mobile installation tooling in this slice.
- Do not implement full first-boot password removal until the opt-in auth core
  is tested and reviewed.

## Constraints

- Security-sensitive change: keep default behavior unchanged unless
  certificate mode is explicitly configured.
- Preserve ADR-0022: certificate auth must be a primary auth mechanism for the
  configured mode, not a hidden recovery bypass.
- Trust certificate headers only from nginx-to-Flask localhost deployment.
- Keep routes thin and put certificate validation in a service.
- Maintain traceability annotations and focused security tests.
- Existing unrelated dirty files in the worktree must not be staged.

## Context

- Branch: `docs/local-ca-mtls-design`
- Design spec: `docs/history/specs/local-ca-client-certificate-auth.md`
- Server nginx TLS config: `app/server/config/nginx-monitor.conf`
- Server auth/session code: `app/server/monitor/auth.py`
- Server view routing: `app/server/monitor/views.py`
- Existing camera mTLS precedent: `app/camera/camera_streamer/control_server.py`

## Plan

1. Add opt-in auth mode configuration:
   - `AUTH_MODE=password|certificate|mixed`
   - default `password`
2. Add certificate auth service:
   - read nginx client certificate headers
   - require nginx verification success
   - parse PEM cert when present
   - enforce `clientAuth` EKU
   - map certificate profile to effective role
   - support local denylist/allowlist shape
3. Add cert-backed session entry point:
   - `POST /api/v1/auth/cert/session`
   - `GET /api/v1/auth/cert/me`
   - no password fallback in `certificate` mode
4. Wire view auto-login for certificate mode if safe and testable.
5. Add nginx client-cert request headers in a way that does not require client
   certs by default.
6. Add focused unit/security tests.
7. Run relevant validation and push the design branch.

## Resumption

- Current status: opt-in certificate auth core implemented and validated
  locally.
- Last completed step: broad auth/security, startup, lint, docs, traceability,
  and versioning checks passed.
- Next step: review branch diff and push branch update.
- Branch / PR: `docs/local-ca-mtls-design`, no PR merged.
- Devices / environments: local repo first; VM only if build/image validation is
  needed later.
- Commands to resume:
  - `git switch docs/local-ca-mtls-design`
  - inspect dirty files with `git status --short`
  - continue from this exec plan
- Open risks / blockers:
  - existing unrelated dirty files must remain unstaged
  - full no-password first-boot flow is intentionally deferred until core auth
    is reviewed
  - `openapi/server.yaml` was already dirty before this task; avoid mixing API
    contract edits into this branch update unless a clean patch can be staged
    separately

## Validation

Completed local validation:

```bash
pytest app/server/tests/unit/test_certificate_auth_service.py app/server/tests/security/test_certificate_auth.py -v
pytest app/server/tests/security/ -v
pytest app/server/tests/unit/test_init.py app/server/tests/unit/test_app.py -v
python tools/docs/check_doc_map.py
python scripts/ai/check_doc_links.py
python scripts/ai/validate_repo_ai_setup.py
python tools/traceability/check_traceability.py
ruff check app/server/monitor app/server/tests/unit/test_certificate_auth_service.py app/server/tests/security/test_certificate_auth.py
ruff format --check app/server/monitor app/server/tests/unit/test_certificate_auth_service.py app/server/tests/security/test_certificate_auth.py
ruff check .
ruff format --check .
python scripts/check_version_consistency.py
python scripts/check_versioning_design.py
```

Results:

- Focused certificate-auth tests: 15 passed.
- Server security suite: 114 passed.
- Startup/app factory tests: 47 passed.
- Repo-wide ruff check/format, docs, traceability, and versioning guards passed.
- VM image build validation was not run for this opt-in app/config slice.

## Risks

- Header spoofing if Flask is exposed directly instead of only behind nginx.
- Browser client certificate prompts vary by platform.
- Certificate-only mode could lock out existing password users if enabled
  before owner certificates are registered.
- Offline revocation depends on local denylist/expiry/work-order controls.

## Completion Criteria

- Existing default password auth remains unchanged.
- Certificate auth can be enabled explicitly in tests.
- Valid local-CA-style client cert creates a role-scoped session.
- Invalid, missing, wrong-EKU, denied, or insufficient-role certs are rejected.
- nginx passes certificate metadata to Flask.
- Security tests document the auth-mode behavior.
- Branch is pushed without staging unrelated dirty files.
