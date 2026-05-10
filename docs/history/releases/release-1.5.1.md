# Release 1.5.1 Plan

Date: 2026-05-10
Status: Prepared for operator-overridden release
Target version: 1.5.1

## Goal

Ship the post-1.5.0 live-device fixes as a patch release without changing the
1.5.x feature surface or trust model.

## Required Merged PRs

- PR #294: Fix dashboard settings and Tailscale status
- PR #295: Fix shared live camera links

## Scope

- Route-aware camera server endpoint selection for mixed Ethernet/WiFi devices
- Logged-in dashboard cleanup after setup; no QR/server-address card
- Camera Settings dropdowns and inputs reflect the current saved camera config
- Diagnostics export works from Settings
- Tailscale Settings reflects an already-running daemon
- Shared live-camera links render one scoped live stream without dashboard access

## Hardware Verification Steps

- Server dashboard: confirm no logged-in Server Address/QR card is visible.
- Camera Settings: open Test Camera 2 and confirm resolution/framerate fields
  match the camera card before saving.
- Diagnostics: export a bundle from Settings and confirm the browser downloads it.
- Tailscale: confirm the enable toggle reflects the active daemon and Tailscale IP.
- Share links: create a live-camera share link in an unauthenticated browser and
  confirm it shows only that camera's live viewer.
- Device smoke: run `scripts/smoke-test.sh` against the server and at least one
  paired camera before tagging.
- OTA release validation: if OS images/SWU bundles are produced, install signed
  server and camera bundles, reboot, and confirm both devices return healthy.

## Sign-Off

- PR-level live deployment for #294 and #295 was performed on the lab server and
  camera before merge.
- Release-level hardware smoke sign-off is still required immediately before
  tag/build/publish.

## Build Artifacts

Build these on a Linux build host after the release branch merges and
`v1.5.1` is tagged:

- `server-update-v1.5.1*.swu`
- `camera-update-v1.5.1*.swu`
- server production SD image (`*.wic.bz2` and `*.wic.bmap`)
- camera production SD image (`*.wic.bz2` and `*.wic.bmap`)
- image manifests and SBOM archives

## Release Gate

`docs/ai/roles/release.md` still has smoke-test mode enabled for normal
automated release-role runs. For `v1.5.1` only, the operator explicitly
overrode that gate on 2026-05-10 and requested a force merge plus release.
Leave the smoke-test gate in place for future automated release runs unless it
is removed through review.
