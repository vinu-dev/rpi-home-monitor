# Connectivity And Privacy Constraints

Date: 2026-04-20
Status: Active product constraint

## Core Product Rule

The product is **local-first and privacy-first**.

- cameras operate on the local network
- the server operates on the local network
- recordings, events, configuration, and credentials stay local by default
- the product must not require public internet access for normal operation

## Remote Access Rule

If remote access is needed, it is provided through **Tailscale** rather than a
vendor cloud.

- remote viewing/admin access happens over a private mesh/VPN path
- the product must not require public inbound ports
- the product must not depend on a vendor-managed SaaS control plane to remain
  usable

## Planning Implications

- features that require public-internet delivery infrastructure are not default
  roadmap candidates
- browser/vendor push infrastructure should not be assumed as the primary alert
  path
- prefer in-app alerting, review surfaces, and Tailscale-reachable remote flows
  over cloud push designs
- all planning should classify features as:
  - fully local
  - local-first with optional remote access via Tailscale
  - incompatible with product constraints

## Acceptable Direction

Good fits for this product:

- local event processing
- local review queue / alert center
- local snapshots and clips
- LAN-first setup and management
- remote access through Tailscale to the same local UI and APIs
- local integrations such as Home Assistant, MQTT, and local webhooks

## Direction To Avoid By Default

These should be treated as mismatches unless explicitly approved:

- features that need public cloud relay to be useful
- vendor push services as a required alert path
- email/SMS alert designs that assume always-on internet
- architecture that stores core product data outside the device/home network

## Execution Rule

If a feature idea conflicts with these rules, re-plan it before implementation.
Do not "work around" the product direction inside code.
