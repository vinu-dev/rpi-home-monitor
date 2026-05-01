# Architecture Decision Records

This directory collects the Architecture Decision Records (ADRs) for
`rpi-home-monitor`. Each ADR captures **one** decision: the context that
forced it, the option chosen, the alternatives considered, and the
consequences the project now has to live with.

Format: lightly adapted Michael Nygard style (`Status / Date / Deciders`
header, then `Context → Decision → Alternatives → Consequences →
Implementation`). Numbering is sequential and gap-free; a superseded ADR
is marked `Status: Superseded by ADR-XXXX` rather than deleted.

## Index

| #    | Title                                                                                           | Status     |
|------|-------------------------------------------------------------------------------------------------|------------|
| 0001 | [Custom Yocto distro](0001-custom-yocto-distro.md)                                              | Accepted   |
| 0002 | [JSON file storage (no database)](0002-json-file-storage.md)                                    | Accepted   |
| 0003 | [Service layer pattern](0003-service-layer-pattern.md)                                          | Accepted   |
| 0004 | [Camera lifecycle state machine](0004-camera-lifecycle-state-machine.md)                        | Accepted   |
| 0005 | [WebRTC primary, HLS fallback for live view](0005-webrtc-primary-hls-fallback.md)               | Accepted   |
| 0006 | [Modular monolith architecture](0006-modular-monolith-architecture.md)                          | Accepted   |
| 0007 | [Dev-build default credentials](0007-dev-build-default-credentials.md)                          | Accepted   |
| 0008 | [SWUpdate A/B rollback](0008-swupdate-ab-rollback.md)                                           | Accepted   |
| 0009 | [Camera pairing via mTLS + TOFU](0009-camera-pairing-mtls.md)                                   | Accepted   |
| 0010 | [LUKS data encryption](0010-luks-data-encryption.md)                                            | Accepted   |
| 0011 | [Auth hardening (password + rate limit + TOTP)](0011-auth-hardening.md)                         | Accepted   |
| 0012 | [UI architecture (HTMX + Alpine)](0012-ui-architecture.md)                                      | Accepted   |
| 0013 | [Unified provisioning + GPIO triggers](0013-unified-provisioning-gpio-triggers.md)              | Accepted   |
| 0014 | [SWUpdate signing (dev / prod split)](0014-swupdate-signing-dev-prod.md)                        | Accepted   |
| 0015 | [Server → camera control channel](0015-server-camera-control-channel.md)                        | Accepted   |
| 0016 | [Camera health heartbeat protocol](0016-camera-health-heartbeat-protocol.md)                    | Accepted   |
| 0017 | [On-demand, viewer-driven streaming + recording modes](0017-on-demand-viewer-driven-streaming.md) | Accepted   |
| 0018 | [Dashboard information architecture](0018-dashboard-information-architecture.md)                 | Accepted   |
| 0019 | [Time sync and timezone](0019-time-sync-and-timezone.md)                                         | Accepted   |
| 0020 | [Dual-transport OTA (server upload + server-to-camera push)](0020-dual-transport-ota.md)         | Accepted   |
| 0021 | [Camera-side motion detection](0021-camera-side-motion-detection.md)                             | Accepted   |
| 0022 | [No backdoors in authentication or recovery](0022-no-backdoors.md)                               | Accepted   |
| 0023 | [Unified fault framework](0023-unified-fault-framework.md)                                       | Proposed   |
| 0024 | [Local alert center](0024-local-alert-center.md)                                                 | Proposed   |
| 0025 | [Information architecture consolidation](0025-ia-consolidation.md)                               | Proposed   |
| 0026 | [Desired-vs-observed camera state reconciliation](0026-desired-vs-observed-state-reconciliation.md) | Proposed   |
| 0027 | [Rich motion notifications](0027-rich-motion-notifications.md)                                   | Proposed   |

## Writing a new ADR

1. Copy the most recent accepted ADR as a template (0016 is the current
   reference for formatting).
2. Number it `NNNN-short-slug.md` — the next unused integer, four digits.
3. Keep the scope tight: one ADR per decision. Cross-link rather than
   bundle.
4. Start in `Status: Proposed`. Flip to `Accepted` when merged, or
   `Rejected` / `Superseded by ADR-XXXX` if the decision is reversed
   later.
5. Update the index table above in the same commit.
6. Link the ADR from the relevant code or doc (`docs/architecture.md`,
   module docstrings, etc.) so readers can find it from the surface
   they're reading.

## See also

- [`docs/ai/index.md`](../ai/index.md) — AI-assistant entrypoint
- [`docs/architecture.md`](../architecture.md) — current system picture
- [`docs/exec-plans/`](../exec-plans/) — multi-session execution plans
  that implement ADRs
