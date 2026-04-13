# Design Standards

This repository is not only a code sample. It is a product sample.

## System Design

- Service-layer architecture is the default for the server.
- The camera follows an explicit lifecycle/state-machine model.
- Runtime mutable state belongs on `/data`.
- Yocto policy belongs in layers, recipes, packagegroups, image definitions, or
  machine config, not in per-developer `local.conf` workarounds.
- Security-sensitive behavior must be explicit: auth, TLS, pairing, storage,
  and OTA should have clear contracts and tests.

## Product Design

- Every user-facing flow should have a clear primary path and clear failure
  states.
- Setup, provisioning, login, status, update, and recovery flows matter as much
  as the happy-path dashboard.
- Browser and device behavior should be treated as product behavior, not as an
  afterthought.

## UI Standards

- Avoid generic or placeholder-feeling layouts.
- Use a deliberate hierarchy, spacing, naming, and state model.
- Prefer component patterns that can scale with more devices and settings.
- If changing an existing UI, preserve the established product language unless
  the change intentionally introduces a new system-wide standard.

## Operational Design

- Deployment paths must be real and reproducible.
- Hardware smoke tests should reflect how the product is actually used.
- If a runbook fails on real hardware, the runbook is wrong until fixed.
- Design docs and operational docs are product assets, not optional extras.
