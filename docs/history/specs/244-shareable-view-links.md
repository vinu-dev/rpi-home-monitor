# Feature Spec: Shareable View-Only Links for Clips and Live Cameras

## Goal

Operators need to share individual clips or live camera feeds with neighbors, family members, or insurance adjusters without granting them account access or visibility into other cameras. Today the only viable option is password sharing, which is insecure and exposes the entire system. Enable per-resource, time-limited, revocable, token-based view-only links that require no account creation and expose nothing but the requested resource and a watermark. Each share is audit-logged on creation and on first/last access.

## Context

- **Existing sharing patterns**: The product already serves unauthenticated content through IP rate-limiting (auth/provisioning pattern) and public-facing status surfaces (camera pairing UI).
- **Session and auth model**: `app/server/monitor/services/` owns auth/session logic; `app/server/monitor/api/auth.py` manages JWT tokens and permission checks.
- **Per-camera authorization**: Issue #86 established fine-grained camera authorization checks that gate clip and stream access.
- **Audit trail**: The product already logs admin actions (user reset, auth changes, OTA) to `/logs` and exposes them through the dashboard.
- **Public surfaces**: The camera pairing UI at `/setup` and WiFi join flows are already unauthenticated, establishing precedent for stripped-down public surfaces.
- **Recording model**: Clips are persisted to `/data/clips/` and served through the API; live streams are proxied from the camera.

## User-Facing Behavior

### Primary Path

**Create a share link (admin only):**
1. From the clip detail page or camera live view, operator taps/clicks "Share".
2. A modal or sidebar prompts for:
   - **TTL** (time-to-live): 1h / 24h / 7d / 30d / never (never = no expiry)
   - **Optional IP/UA pinning**: "Require this device to connect from the same IP" (optional checkbox, off by default)
   - Optional note for audit trail (e.g., "insurance adjuster")
3. System generates a unique token (e.g., `sharelink_xxxxxxxxxxxx_yyyyyyyy`) and returns a shareable URL like `https://device-name.local/share/clip/sharelink_xxxxxxxxxxxx_yyyyyyyy` or `/share/camera/sharelink_xxxxxxxxxxxx_yyyyyyyy`.
4. Operator copies the URL and shares it (SMS, email, etc.); the token is never exposed in query strings or logs.
5. Action is recorded in the audit log: `[timestamp] admin created share link for [resource type] [resource ID], TTL [duration], pinned [yes/no]`.

**View a shared clip or camera (unauthenticated visitor):**
1. Visitor clicks the link or pastes it into the browser.
2. No login required. The system validates the token (not expired, not revoked, IP/UA match if pinned).
3. If valid, the visitor sees:
   - **For clips**: The clip detail page (title, timestamp, thumbnail, player) with a watermark overlay or banner ("Shared by [device name] on [date]").
   - **For live cameras**: A single-camera view (live MJPEG or HLS stream, no multi-camera dashboard) with the same watermark.
4. Visitor can play/pause, seek the clip, or watch the live stream; no transcript search, no camera list, no settings access.
5. System logs: `[timestamp] visitor accessed share link [token, redacted in display] for [resource type] [resource ID], IP [logged], source [first/subsequent access]`.

**Revoke a share link (admin only):**
1. From the share-link management page or from the clip/camera detail page, operator clicks "Revoke" next to a link.
2. Link becomes immediately invalid; any in-flight requests complete, but new requests are rejected.
3. Audit log: `[timestamp] admin revoked share link [token] for [resource type] [resource ID]`.

### Failure States

- **Token expired**: Visitor is redirected to a public error page that says "This link has expired. Contact the person who shared it." (no details on what it was sharing).
- **Token revoked**: Same message as expired.
- **Token invalid or malformed**: Same message.
- **IP/UA pinned but mismatch**: Redirect to the same error page (don't leak "why" it failed).
- **Clip deleted or camera offline**: Visitor sees a "Resource not available" message (honest, no details that leak internal state).
- **Rate-limited or abusive access**: IP is rate-limited after repeated failed attempts on share links (same bucket as login rate-limiting).

## Acceptance Criteria

1. **Share-link creation (admin)**: Admin can mint a view-only token for a clip or camera from the UI, choosing TTL and optional IP/UA pinning. Token is cryptographically unpredictable.
   - Validation: unit test for token generation, integration test for UI flow.

2. **Share-link persistence**: Token metadata (resource type, resource ID, owner, TTL, pinned constraints, revoked flag) is persisted to the database.
   - Validation: unit test for model save/fetch, integration test for lifecycle.

3. **Public viewer (unauthenticated)**: Visitor can access `/share/clip/<token>` or `/share/camera/<token>` without authentication; system validates token and serves the resource if valid.
   - Validation: contract test (token validation logic), integration test (unauthenticated route + token check).

4. **Clip viewer page**: Shared clip is rendered with watermark, player controls, metadata (title, timestamp). No other clips, cameras, or settings visible.
   - Validation: browser-level UI test (clip page loads, watermark visible, controls work).

5. **Camera live viewer page**: Shared camera stream is rendered with watermark. No camera list, no multi-view, no recording controls visible.
   - Validation: browser-level UI test (live stream loads, watermark visible).

6. **Token expiry**: After TTL, token is rejected even if not explicitly revoked. Visitor sees generic "link expired" message.
   - Validation: unit test (TTL logic), integration test (time-based rejection), smoke test (real wall-clock verification).

7. **Token revocation**: Admin can revoke a token from the UI. Revoked tokens are immediately rejected.
   - Validation: unit test (revoke flag), integration test (revoke + subsequent access).

8. **IP/UA pinning**: If pinned, token is only valid if visitor's IP and User-Agent match at creation time (or within a small tolerance, e.g., /24 subnet). Mismatch triggers same error as invalid token.
   - Validation: unit test (pinning logic), integration test (matching + mismatching IPs).

9. **Audit trail**: Share-link create, revoke, and first/last access are logged to the audit trail with operator name, timestamp, token (redacted in UI), resource, TTL, and constraints.
   - Validation: integration test (audit entries exist), smoke test (audit log visible in UI).

10. **Rate-limiting**: Public share-link endpoints are rate-limited by IP to prevent token-enumeration attacks. Limit is shared with login rate-limiting.
    - Validation: contract test (rate-limit headers + behavior), load test (verify limit applied).

11. **Credentials never exposed**: Share tokens do not appear in query strings, HTTP logs (except in secure audit trail), browser history, or error messages. Token is in path only.
    - Validation: code review (no token in logs or error messages), integration test (no token leakage).

12. **No bypass of resource authorization**: A valid share token grants access only to the explicitly scoped resource (one clip or one camera). Visitor cannot use the token to access other resources, even if they guess the resource ID.
    - Validation: unit test (token scope validation), integration test (attempt to access different clip/camera with same token, expect rejection).

13. **Resource deletion or unavailability**: If a clip is deleted or a camera goes offline, the share link becomes invalid. Visitor sees generic "resource not available" message (not "deleted" or "offline", which leak internal state).
    - Validation: integration test (delete clip + access link, verify generic error).

14. **Management page**: Admin can see a list of active share links for a resource (clip/camera) with TTL remaining, pinning status, access count, and last-access time. Revoke action is available.
    - Validation: browser-level UI test (list page loads, revoke button works).

## Non-Goals

- **Multi-resource bundles**: Share links are per-resource only; no "share a folder of clips" or "share multiple cameras" in v1.
- **Public guest accounts**: Link-based, not account-based; no guest user registration or persistent identity.
- **Guest-initiated sharing**: Only admins can create links; guests cannot share links they receive.
- **Sharing metadata**: Share links grant access to the clip or stream only; no event metadata, audit log, or search index visible to the guest.
- **Server-side redaction**: Share links honor whatever the source resource already shows; no additional privacy zones or redaction applied at share time.
- **Mobile push notification**: No integration with device push or notification services to alert share-link recipients.
- **Expiring links with usage counters**: TTL is time-based only; usage-based expiry (e.g., "5 views max") is deferred.
- **Custom branding or vanity URLs**: Share links are functional tokens only; no custom slugs or branded public landing page.

## Module and File Impact

### Models (app/server/monitor/models.py or new file)

- **ShareLink model**: Stores token, resource type (clip or camera), resource ID, owner (user ID), created_at, expires_at, revoked_at, pinned_ip/pinned_ua, access_count, last_access_at.

### Services (app/server/monitor/services/)

- **share_link_service.py** (new): 
  - `generate_token()` → cryptographic token
  - `create_share_link(resource_type, resource_id, owner_id, ttl, pin_ip, pin_ua)` → ShareLink
  - `get_share_link(token)` → ShareLink or None
  - `validate_share_link(token, visitor_ip, visitor_ua)` → (valid: bool, error_reason: str)
  - `revoke_share_link(token)` → None
  - `list_share_links(resource_type, resource_id)` → [ShareLink]
  - `cleanup_expired_links()` → count (for optional background task or on-demand cleanup)

### API (app/server/monitor/api/)

- **api/share.py** (new):
  - `POST /api/share/links` → create link (requires auth, takes resource type/ID, TTL, pin options)
  - `GET /api/share/links?resource_type=<type>&resource_id=<id>` → list links for resource (requires auth)
  - `DELETE /api/share/links/<token>` → revoke link (requires auth)
  - `GET /share/clip/<token>` → public viewer (no auth required, validates token, serves clip page)
  - `GET /share/camera/<token>` → public viewer (no auth required, validates token, serves live stream page)

### Templates (app/server/monitor/templates/)

- **share_link_modal.html** (new): Modal for creating a share link (TTL + pinning options).
- **share_management.html** (new): Page to list, view expiry, and revoke share links for a resource.
- **shared_clip_viewer.html** (new): Public page for viewing a shared clip with watermark.
- **shared_camera_viewer.html** (new): Public page for viewing a shared live camera with watermark.

### Static Assets (app/server/static/)

- CSS/JS for watermark overlay (position, opacity, styling to not obstruct content but remain visible).
- JS for "Copy link to clipboard" button in share modal.

### Database / Schema

- New `share_links` table with columns: id (PK), token (unique), resource_type (enum: clip | camera), resource_id (FK if appropriate), owner_id (FK to user), created_at, expires_at, revoked_at, pinned_ip, pinned_ua, access_count, last_access_at.
- Store migration to create the table (Alembic or native SQLAlchemy).

### Audit and Logging (app/server/monitor/services/audit_service.py)

- Extend audit service to log share-link create/revoke/access events.
- Audit log schema: timestamp, action (create | revoke | access), user (admin), resource (type + ID), token (redacted in display, full in audit DB), TTL, pinned (yes/no), IP (for access events), visitor_ip (for access events).

### Rate-Limiting (app/server/monitor/services/ or middleware)

- Integrate share-link public endpoints (`/share/clip/<token>`, `/share/camera/<token>`) into the existing IP rate-limiter.
- Share the same bucket as login rate-limiting to preserve fair-share principles.

### Yocto (meta-home-monitor/)

- No new system dependencies required (use Python's `secrets` module for token generation, no external crypto libs needed).
- No recipe changes unless adding a new dependency (none planned for v1).

## Validation Plan

### Unit Tests (app/server/tests/test_share_link_service.py)

- Token generation is cryptographically unpredictable.
- Share-link creation and persistence.
- Token validation (not expired, not revoked, IP/UA match).
- TTL logic (token valid just before expiry, invalid after).
- Revocation (revoked token is invalid).
- Scope isolation (token valid for only its resource).
- IP/UA pinning logic (match, mismatch within tolerance, edge cases).

### Integration Tests (app/server/tests/test_api_share.py)

- Create share link via API, verify response includes URL.
- Create share link from UI flow (dashboard button), verify modal works.
- Access public viewer with valid token, verify resource loads (mock clip/camera).
- Access public viewer with invalid/expired/revoked token, verify rejection.
- Revoke link, verify access denied on next request.
- Admin can list share links for a resource.
- Audit events are logged (create, revoke, access).
- Rate-limiting applies to public endpoints.

### Contract Tests (app/server/tests/test_api_share_contract.py)

- Share-link endpoints return correct status codes (201 for create, 200 for valid public view, 404 for invalid token).
- Token validation logic is isolated and testable.

### Browser-Level UI Tests (smoke or manual)

- "Share" button is visible on clip and camera detail pages (admin only).
- Share modal opens and allows TTL/pinning selection.
- Copied link is valid and viewable.
- Shared clip page renders with watermark and player controls.
- Shared camera page renders with watermark and live stream.
- Expired or revoked link shows generic error message (not "expired" or "revoked" in detail).
- Share management page lists active links and allows revocation.

### Hardware Smoke Test (scripts/smoke-test.sh)

- Record a clip on real hardware.
- Create a share link for the clip.
- Access the shared clip from a different device/IP on the same LAN.
- Verify watermark and clip content are visible.
- Create a share link for a live camera.
- Access the shared camera from a different device/IP on the same LAN.
- Verify watermark and live stream are visible.
- Revoke a link and verify access denied on next attempt.
- Verify audit log entries exist for create, revoke, and access.

## Non-Goals Section (Detailed)

This feature does **not** include:

- **Multi-resource share bundles**: Operators cannot create a single link that grants access to multiple clips or cameras. Each link is per-resource.
- **Public guest accounts**: Share links do not create user accounts for recipients. No email verification, password setup, or persistent identity.
- **Guest-initiated sharing**: Recipients cannot use a share link to create new share links for the same resource or for other resources. Only the admin can mint links.
- **Audit log or event history sharing**: Guests see only the clip or live stream; no access to the audit trail, event metadata, motion detection log, or threat log.
- **Server-side redaction or privacy zones**: Share links respect the resource's existing visibility (e.g., if a clip has a privacy zone, the guest sees the zone as configured). No additional redaction is applied at share time.
- **Mobile app integration or push notifications**: No native app deep-linking or push notification to alert recipients that a link is ready.
- **Expiring by usage count**: Links expire only by time (TTL). Usage-based expiry (e.g., "valid for 5 views") is deferred to v2.
- **Vanity URLs or custom slugs**: All share links use the standard token format; no friendly slugs like `/share/clip/driveway-incident-may-3`.
- **Branded public landing pages**: The public viewer is a stripped-down resource-specific page, not a branded landing page with branding, logo, or custom UI.
- **Live transcription or AI-generated captions**: Shared clips show video only, not AI-generated descriptions or transcription.

## Risk Analysis

### Hazard 1: Credential Exposure in Share Links

**Description**: Share tokens could be logged, cached, or exposed in query strings, browser history, or HTTP logs.

**Severity**: High (token is a credential equivalent to a short-lived API key).

**Probability**: Medium (developer error, but mitigated by design).

**Risk Controls**:
- Tokens are in the path (`/share/clip/<token>`), not in query strings (no browser history or log exposure).
- Tokens are never logged in HTTP access logs (sanitized at the middleware level or explicitly excluded).
- Tokens are logged only in the secure audit trail with restricted access.
- Code review to verify no token appears in error messages, debug logs, or stack traces.

### Hazard 2: Token Enumeration or Brute-Force Attack

**Description**: An attacker could attempt to enumerate or brute-force share tokens to gain unauthorized access to clips or cameras.

**Severity**: High (could expose private footage).

**Probability**: Low (tokens are cryptographically strong, 128+ bits of entropy; endpoints are rate-limited).

**Risk Controls**:
- Tokens are generated using Python's `secrets.token_urlsafe(32)` or similar (128+ bits of entropy).
- Public endpoints (`/share/clip/<token>`, `/share/camera/<token>`) are rate-limited by IP (shared bucket with login).
- Failed attempts are logged for audit.
- Consider optional CAPTCHA or progressive delays after repeated failures (deferred to v2 if needed).

### Hazard 3: Link Revocation Not Honored in Real-Time

**Description**: A revoked link could still be accessible if in-flight requests or caches are not invalidated immediately.

**Severity**: Medium (revocation is expected to be immediate).

**Probability**: Low (invalidation is simple: revoke flag in DB is checked per-request).

**Risk Controls**:
- Token validation is per-request, not cached.
- Revocation is a simple flag update in the database.
- Verification test: create, revoke, and immediately access link (expect failure).

### Hazard 4: Confusion Between Time-Based and Never-Expiring Links

**Description**: An operator could mint a "never" (no-expiry) link intending to revoke it later but forget to do so, leaving a permanent backdoor.

**Severity**: Medium (depends on operator discipline; not a system flaw).

**Probability**: Medium (operator error).

**Risk Controls**:
- UI prompts warn when selecting "never": "This link will not expire. Remember to revoke it when no longer needed."
- Share management page shows all active links with "never-expiring" badges prominently.
- Audit log entry includes TTL selection so admin can review history.
- (Optional, deferred) Background task to warn admin of old "never" links (v2).

### Hazard 5: Resource Deleted While Link is Active

**Description**: If a clip is deleted or a camera is removed, the share link becomes dangling. Visitor sees a generic error, but the link metadata remains in the DB (minor resource leak).

**Severity**: Low (no security exposure; minor data hygiene).

**Probability**: Medium (normal operational flow).

**Risk Controls**:
- Visitor sees a generic "resource not available" error (expected behavior).
- Share-link cleanup can be triggered on-demand or scheduled periodically to remove dangling links.
- Test: delete a clip and verify link access is rejected.

### Hazard 6: IP/UA Pinning Bypass

**Description**: An attacker could spoof IP or User-Agent headers to bypass pinning constraints.

**Severity**: Low (pinning is optional and advisory, not cryptographic).

**Probability**: Low (attacker already has the link; spoofing adds little value).

**Risk Controls**:
- Pinning is documented as optional and not a security guarantee (advisory only).
- IP pinning uses subnet-based tolerance (e.g., /24) to allow for DHCP churn without breaking legitimate use.
- UA pinning uses substring matching (e.g., "Chrome" vs. exact version) to allow minor updates.
- Test: verify pinning logic accepts/rejects expected cases.

### Hazard 7: Rate-Limiter Circumvention

**Description**: An attacker could attempt to circumvent rate-limiting by using distributed IPs or slow enumeration.

**Severity**: Medium (slows attack but doesn't prevent determined attacker).

**Probability**: Low (requires coordinated effort; tokens have high entropy anyway).

**Risk Controls**:
- Rate-limiting is per-IP, shared with login rate-limiting (fair-share principle).
- Audit logs capture all failed attempts (for forensics).
- (Optional, deferred) Add IP-reputation checks or additional behavioral signals (v2).

## Security

### Threat Model

**Threat 1: Unauthorized clip/camera access via token theft**
- **Attacker**: Outside party who intercepts or steals a share link (e.g., SMS message, email).
- **Impact**: Access to the specific clip or camera stream for the duration of the token's TTL.
- **Mitigation**: Token is unpredictable; short default TTL (e.g., 24h); operator can revoke immediately; rate-limiting prevents brute-force.

**Threat 2: Token enumeration attack**
- **Attacker**: Automated attempt to enumerate share tokens via brute-force or dictionary attack.
- **Impact**: Unauthorized access to clips/cameras if enumeration succeeds.
- **Mitigation**: Tokens have 128+ bits of entropy; public endpoints are rate-limited by IP; failed attempts are logged.

**Threat 3: Denial-of-service via excessive share-link creation**
- **Attacker**: Admin or compromised account creates many share links to exhaust resources.
- **Impact**: Database bloat; performance degradation.
- **Mitigation**: Link cleanup runs periodically; database constraints can be added to limit active links per resource (deferred to v2 if needed).

**Threat 4: MITM attack on share link URL**
- **Attacker**: MITM intercepts the URL when shared over unencrypted channel (e.g., HTTP email link).
- **Impact**: MITM can access the clip/camera if in-network.
- **Mitigation**: Share links are sent over HTTPS (assumed); operator should use secure channels to share the URL; reminder in UI: "Share links are valid for anyone with the URL—use a secure channel."

**Threat 5: Compromised admin account creates permanent links**
- **Attacker**: Admin account is compromised; attacker creates no-expiry share links to maintain persistent access.
- **Impact**: Attacker can view clips/cameras indefinitely.
- **Mitigation**: Audit log captures all share-link creation (forensics); admin can revoke links; password rotation/session revocation can invalidate future link creation.

**Threat 6: Leaked share link in logs or error messages**
- **Attacker**: Developer or operator reviews logs and finds share tokens exposed.
- **Impact**: Token is exposed; attacker can access the clip/camera.
- **Mitigation**: Tokens are never logged in HTTP access logs or error messages; sanitized at middleware; logged only in secure audit trail.

### Sensitive Paths

This feature **touches**:
- `app/server/monitor/api/` (new endpoints for public share-link access)
- `app/server/monitor/services/` (new share-link service)
- Database schema (new share_links table)
- Audit logging (new audit event types)

This feature **does not touch**:
- `app/server/auth/` (authentication is not required for public viewers)
- `app/camera/camera_streamer/lifecycle.py` (camera runtime is not modified)
- `meta-home-monitor/` (no Yocto changes)
- Certificate/TLS/pairing flows

### Security Review Checklist

- [ ] Tokens are generated with `secrets.token_urlsafe(32)` (128+ bits of entropy).
- [ ] Public endpoints (`/share/*`) validate tokens and resource scope per-request (no caching).
- [ ] Tokens never appear in query strings, HTTP access logs, or error messages.
- [ ] IP/UA pinning is optional and documented as advisory (not cryptographic).
- [ ] Rate-limiting is applied to public endpoints (shared with login).
- [ ] Audit log captures create/revoke/access events with user, resource, timestamp, IP.
- [ ] Resource deletion/unavailability returns generic "not available" error (no internal state leaked).
- [ ] No bypass: a share link cannot be used to access resources it doesn't scope to.

## Traceability

The implementer must add and update these IDs:

| ID Family | Placeholder | Purpose |
|-----------|-------------|---------|
| UN- | UN-244 | Operator shares clips/cameras without password sharing |
| SYS- | SYS-244-01 | System supports per-resource, time-limited, revocable share links |
| SWR- | SWR-244-01, SWR-244-02, ... | Token generation, validation, revocation, IP/UA pinning, audit logging |
| ARCH- | ARCH-244-01 | Share-link service layer and API design |
| SEC- | SEC-244-01, SEC-244-02, ... | Token confidentiality, rate-limiting, scope isolation, audit trail |
| RISK- | RISK-244-01, RISK-244-02, ... | Token theft, enumeration, revocation delay, resource deletion, pinning bypass |
| RC- | RC-244-01, RC-244-02, ... | Token entropy, rate-limiting, audit logging, IP/UA pinning (optional) |
| TC- | TC-244-01, TC-244-02, ... | Unit tests (token generation, validation, TTL, revocation), integration tests (API, UI, auth), contract tests (endpoints), smoke tests (hardware) |

Code annotations:

- `REQ: SWR-244-01` → token generation logic
- `REQ: SWR-244-02` → token validation logic
- `SEC: SEC-244-01` → token storage/handling
- `RISK: RISK-244-01` → enumeration rate-limiting
- `TEST: TC-244-01` → share-link unit tests

The implementer must run `python tools/traceability/check_traceability.py` before submitting the PR.

## Deployment Impact

### Database Migration

- **Schema change**: New `share_links` table (create via Alembic or SQLAlchemy).
- **Backward compatibility**: No breaking changes; new table is opt-in. Existing deployments can upgrade without data loss.

### No Yocto Changes

- No new system dependencies (uses Python `secrets` module).
- No new recipes or packagegroups.
- Existing package groups remain unchanged.

### No OTA Required

- This feature is purely software (app layer).
- Standard application update path (restart Flask server) is sufficient.
- No camera firmware or bootloader changes.

### Configuration

- No new environment variables or configuration files required.
- TTL defaults and rate-limiting parameters can be exposed in a future version (deferred to v2).

### Rollout

1. Merge the feature branch to `main`.
2. Bump the application version.
3. Deploy via OTA or manual image rebuild.
4. Verify share-link creation and access on real hardware (smoke test).

## Open Questions

1. **Should the first slice support batch/bulk share creation?** (e.g., "Create share links for the last 10 clips")
   - **Deferral**: v2. v1 supports one link per resource via UI. API could support batching later.

2. **Should share links be rate-limited per-resource, per-admin, or per-system?**
   - **Deferral**: v1 applies rate-limiting only to public access attempts, not to link creation. Link creation is gated by auth (admins only). If spam becomes a problem, add per-admin rate-limiting in v2.

3. **Should IP/UA pinning use exact match or allow tolerance?**
   - **Decision**: Use tolerance. IP pinning allows /24 subnet (covers typical home WiFi DHCP churn). UA pinning uses substring (e.g., "Chrome" vs exact version). Document the tolerance in code and UI warnings.

4. **Should never-expiring links be allowed, or should we enforce a maximum TTL (e.g., 1 year)?**
   - **Deferral**: v1 allows "never". v2 could enforce a maximum TTL if operational experience shows abuse. UI warns about "never" links prominently.

5. **Should share links be exportable/importable (e.g., backup share-link list)?**
   - **Deferral**: v2. v1 supports view/revoke only from the dashboard. Bulk export/import can be added if needed.

6. **Should we implement a "share-link whitelist" where admins pre-approve domains or IPs?**
   - **Deferral**: v2. v1 uses per-link IP/UA pinning. System-wide whitelist can be added in v2 for multi-site deployments.

7. **Should share links be anonymized in the audit log for GDPR/privacy compliance?**
   - **Deferral**: Audit log includes full token and visitor IP (required for forensics). Privacy/compliance review is recommended before feature ship; may need to redact visitor IPs in UI but preserve them in secure audit backend.

8. **Should there be a "notification" when someone accesses a share link?** (e.g., admin gets a notification when the link is first used)
   - **Deferral**: v2. v1 supports view/revoke via the share-management page. Notifications can be added in v2 (integrate with the alert service).

9. **Should the public viewer require JavaScript, or should it work in text-based browsers?**
   - **Decision**: v1 requires JavaScript (for video player controls, watermark overlay). Text-based access is out of scope.

10. **Should we implement progressive token expiry?** (e.g., tokens expire faster if accessed from unusual IP or after a single access)
    - **Deferral**: v2. v1 uses fixed TTL only.

## Implementation Guardrails

- **Preserve the service-layer pattern**: Share-link creation/validation logic goes in `services/share_link_service.py`, not in routes.
- **Preserve the app-factory pattern**: Share-link service is injected into routes via the Flask app factory.
- **Keep mutable state on `/data`**: Share-link data (tokens, access metadata) is persisted to the database (in `/data`), not committed to the source tree.
- **Keep the product local-first by default**: Share links are opt-in; no automatic sharing or cloud relay.
- **Do not weaken auth or OTA flows**: Share-link creation is gated by admin auth. Link access does not bypass per-resource authorization; it is a special case of authorization (resource-scoped token instead of user-scoped token).
- **Update tests and docs together with code**: Every code change should have a corresponding test and/or doc update. No code without tests.
- **Follow medical-traceability discipline**: Every code annotation (`REQ:`, `SEC:`, `RISK:`, `TEST:`) must be in the traceability matrix. Run the traceability checker before submitting the PR.
- **Rate-limiting is non-negotiable**: All public endpoints must be rate-limited. Integration with the existing rate-limiter is required, not optional.

## Next Steps for Implementation

1. **Create the database migration** (new `share_links` table).
2. **Implement `share_link_service.py`** with token generation, validation, revocation, and cleanup logic.
3. **Implement API endpoints** (`POST /api/share/links`, `GET /api/share/links`, `DELETE /api/share/links/<token>`, public viewers).
4. **Implement UI** (share modal, management page, watermarked clip/camera viewers).
5. **Integrate with audit logging** (log create/revoke/access events).
6. **Integrate with rate-limiting** (apply to public endpoints).
7. **Write comprehensive tests** (unit, integration, contract, smoke).
8. **Run traceability checks** and update all required IDs.
9. **Smoke test on hardware** (create, share, revoke, verify access).
10. **Update user documentation** with share-link workflow and security expectations.
