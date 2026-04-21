# Roadmap: Next 2 Releases

Date: 2026-04-20
Purpose: Turn the market backlog into an AI-ready execution plan for the next two release cycles.
Source: [market-feature-backlog-100.md](./market-feature-backlog-100.md)
Execution pack:
- [Release 01 plan](./releases/release-01.md)
- [Release 02 plan](./releases/release-02.md)
- [Feature specs index](./specs/index.md)
- [AI execution rules](./ai/execution-rules.md)
- [Connectivity and privacy constraints](./connectivity-and-privacy-constraints.md)

## Planning Rules

- Prioritize features that materially improve buyer-perceived value.
- Prefer features that fit the current architecture without a major redesign.
- Avoid stacking too many high-risk platform changes into the same release.
- Any feature that touches auth, OTA, trust boundaries, or camera/server protocol needs explicit verification and doc updates.
- Keep account-recovery posture aligned with the security model: user recovery can improve in-app, but sole-admin recovery remains hardware reset / reflash until dedicated hardware-reset work ships.
- Preserve the no-internet-by-default product model; if remote access is needed, assume Tailscale rather than vendor cloud services.
- Every feature should end with updated docs, tests, and a production-readiness note.

## Release Next

Theme: "Make the system proactive and easier to live with"

### Goals

- The system should tell the user when something important happens.
- The event/review experience should become faster and more useful.
- The first layer of detection relevance should be added without introducing major platform risk.

### Features

1. Rich motion notifications with snapshots, filters, and per-camera rules
   - Why now: strongest market gap, high user value, strong fit to current motion-event architecture.
   - Dependencies: none beyond existing motion-event pipeline.

2. Local alert center and Tailscale-remote review flow
   - Why now: preserves privacy/local-first constraints while still making alerts visible and actionable off-LAN through the existing remote-access model.
   - Dependencies: event state persistence and review deep-link behavior.

3. Camera offline alerts
   - Why now: high trust/value feature, easy to explain, low algorithmic risk.
   - Dependencies: existing heartbeat/offline detection.

4. Storage low / retention risk alerts
   - Why now: improves reliability perception and avoids silent failure.
   - Dependencies: storage metrics and loop-recorder state.

5. Review queue for important events
   - Why now: complements notifications and reduces friction after alert delivery.
   - Dependencies: event model and event-routing UI.

6. Scrubbable timeline with event markers
   - Why now: makes recordings and events feel integrated rather than separate.
   - Dependencies: existing clip/event metadata.

7. Motion zones / activity zones
   - Why now: one of the most expected detection controls across competitors.
   - Dependencies: notification/event pipeline benefits immediately from lower false positives.

8. Privacy zones
   - Why now: complements motion zones and reinforces the product's privacy-first positioning.
   - Dependencies: zone UI and camera-side masking/ignore behavior.

9. Detection sensitivity presets per camera
   - Why now: natural follow-on to zones; helps users tune false positives quickly.
   - Dependencies: existing motion sensitivity support.

10. Config backup and restore
    - Why now: high-value operational feature, low marketing glamour but huge ownership benefit.
    - Dependencies: stable export/import schema for settings, cameras, users, and rules.

## Release After

Theme: "Make detections smarter and the system more connected"

### Goals

- Add semantic value to events beyond generic motion.
- Improve multi-device/home-automation usefulness.
- Strengthen account and fleet operations.

### Features

1. Person detection
   - Why here: big value unlock, but detection semantics need careful rollout after zones/notifications are in place.

2. Vehicle detection
   - Why here: natural extension of object-aware detection.

3. Package detection
   - Why here: highly marketable, especially for front-door cameras.

4. Home Assistant integration
   - Why here: strong fit with self-hosted buyers and local-first positioning.

5. MQTT event bus
   - Why here: unlocks local automation and power-user workflows.

6. Generic webhooks
   - Why here: lowest-friction automation surface for advanced users and external systems.

7. Bulk camera settings / profile templates
   - Why here: needed as multi-camera usage grows.

8. TOTP / stronger 2FA
   - Why here: important trust improvement after account-recovery docs and recovery posture are cleaned up.

9. Protected clips
   - Why here: complements richer event review and evidence handling.

10. Diagnostic export bundle
    - Why here: reduces support friction as the product gets more capable.

## Not In The Next 2 Releases

These are valuable, but should not lead the next two cycles:

- face recognition
- license plate recognition
- semantic search
- two-way audio
- PTZ and auto-tracking
- multi-site management
- mobile native app
- advanced cross-camera incident correlation
- physical factory-reset UX/hardware refinement for sole-admin recovery

They either need more compute, more UI surface, stronger search/indexing, or broader product investment than the current near-term roadmap should absorb.

For auth recovery specifically:

- Near-term: keep improving the in-app admin-resets-user flow and, later, one-shot reset tokens for normal users.
- Product rule: do not reintroduce any CLI or pre-auth software path that resets the sole admin account.
- Deferred hardware track: the sole-admin recovery path is physical factory reset / SD-card reflash until the dedicated hardware-reset workflow ships.

## AI Execution Order

For AI-assisted implementation, the preferred order is:

1. notifications foundation
2. local alert center and remote review flow
3. review/timeline improvements
4. zone controls
5. sensitivity tuning
6. backup/restore
7. object-aware detections
8. integrations
9. security/account hardening
10. fleet/ops improvements

## Definition Of Ready

A feature is ready for implementation when it has:

- a GitHub issue
- a feature spec using [ai-feature-template.md](./ai-feature-template.md)
- acceptance criteria
- identified modules/files likely to change
- test expectations
- explicit non-goals

## Definition Of Done

A feature is done when it has:

- code
- tests
- docs updates
- issue cross-links
- release note entry if user-visible
- a short verification note describing how it was validated
