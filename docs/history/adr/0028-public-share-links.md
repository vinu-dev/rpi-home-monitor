# ADR-0028: Public Share Links For Clips And Live Cameras

**Status:** Proposed
**Date:** 2026-05-04
**Deciders:** vinu-dev

---

## Context

Operators need to share one clip or one live camera with a neighbor, family
member, or insurer without handing over an account or exposing the rest of the
dashboard. The existing product has authenticated live and recordings views,
per-camera authorization checks, audit logging, and login-rate limiting, but it
does not have a way to expose one resource safely to an unauthenticated
recipient.

The feature spec for issue #244 requires:

- one share link per clip or per camera
- revocation and optional expiry
- no account creation for recipients
- generic public failures that do not reveal why access failed
- rate limiting shared with the existing auth abuse bucket
- audit records for create, revoke, and access activity

The main design tension is that the product must add a public entry point
without accidentally reusing authenticated dashboard routes, exposing unrelated
media, or leaking token details.

## Decision

Create a dedicated share-link service and separate public share blueprint.

### 1. Token-scoped, single-resource links

- Each share link is bound to exactly one resource type (`clip` or `camera`)
  and one resource identifier.
- Tokens are generated with high entropy and stored only as path segments, not
  as query parameters.
- Metadata includes owner, created time, optional expiry, revoked state,
  optional first-visitor pinning, and access counters.

### 2. Separate admin and recipient surfaces

- Admins create, list, and revoke links through authenticated
  `/api/v1/share/links` routes and share-management UI affordances in the live
  and recordings views.
- Recipients use dedicated unauthenticated `/share/...` routes.
- Public routes do not reuse the authenticated dashboard media endpoints as the
  primary viewer surface. They validate token scope first, then serve only the
  bound clip or camera assets.

### 3. Generic public failures and bounded public viewers

- Invalid, expired, revoked, and mismatched share links return the same generic
  public failure page so recipients do not learn internal state.
- Missing resources return a separate generic "resource not available" page.
- Public clip and camera pages render only the shared resource plus a visible
  watermark/banner. They do not expose camera lists, settings, alerts, or other
  dashboard navigation.

### 4. Abuse controls and auditability

- Failed public share access reuses the existing auth/login IP abuse bucket
  rather than introducing a second rate-limit policy.
- Optional first-use pinning can bind a link to the first successful visitor's
  IP subnet and browser family.
- Create, revoke, and recipient access activity is audit logged.

## Alternatives Considered

### Reuse authenticated recordings and live routes with a bypass flag

Rejected. That would couple public recipient access to session-oriented
dashboard code paths and increase the risk of scope bypass or chrome leakage.

### Add guest accounts instead of token links

Rejected for v1. Guest accounts would require lifecycle management, password
recovery, and broader authorization design. The spec only needs one-resource
sharing.

### Put tokens in query parameters

Rejected. Path tokens are easier to avoid leaking into copied URLs, browser UI,
and generic logging patterns.

### Create a separate rate limiter for share links

Rejected for v1. Reusing the existing abuse bucket keeps operator behavior more
predictable and avoids policy drift between two unauthenticated entry points.

## Consequences

### Positive

- Operators get a direct evidence-sharing workflow without sharing passwords.
- Public access stays narrowly scoped to one resource per link.
- Security-sensitive behavior is centralized in a dedicated service.
- Public routes remain visibly distinct from the authenticated dashboard.

### Negative / Trade-offs

- The server now has a deliberate unauthenticated media surface that needs
  ongoing review and testing.
- Token-backed sharing does not prevent recipients from manually forwarding the
  link once they can view it.
- Optional first-use pinning adds edge cases for mobile-network changes and
  browser family changes.

## Implementation

- Add `ShareLink` persistence under `/data/config` through the server store.
- Add `ShareLinkService` for token minting, lifecycle, validation, abuse
  handling, and audit events.
- Add authenticated share-management routes plus public `/share/clip/<token>`
  and `/share/camera/<token>` routes.
- Add stripped-down public templates and admin share controls in live and
  recordings views.
- Verify with service, integration, contract, and view tests covering create,
  list, revoke, valid access, invalid access, and scope isolation.
