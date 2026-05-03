# Researcher — project-specific guidance for `vinu-dev/rpi-home-monitor`

This file describes WHAT to research for this repo. The HOW (cron trigger,
issue body format, label transitions, daily log writing, session continuity)
lives in agentry's bundled prompt — see `agentry/config.yml` for that.

## Mission alignment

Per `docs/ai/mission-and-goals.md`, the project is a **trustworthy,
self-hosted home monitoring system that feels like a real product, not a
prototype**. Ideas you file should:
- improve safety, reliability, or operator confidence
- close visible gaps in setup / login / status / update / recovery flows
- match how this product is actually used (Pi 4B server + Pi Zero 2W
  cameras, Yocto-built distro, dashboard + WebRTC live view + clip review)

## Anti-goals (drop ideas matching any)

- code churn without product movement
- prompt-shaped code that ignores repo architecture
- passing local tests while drifting from hardware reality
- design regressions justified as "good enough for now"

## Where to look

Cover at least 3 of these competing self-hosted projects every cycle:

- **Home Assistant** (`homeassistant/core`) — releases, integrations, blog
- **OpenHAB** (`openhab/openhab-distro`)
- **Domoticz** (`domoticz/domoticz`)
- **Frigate NVR** (`blakeblackshear/frigate`) — camera + AI focus
- **MotionEye** (`motioneye-project/motioneye`)
- **Shinobi** (`ShinobiCCTV/Shinobi`)
- **ZoneMinder** (`ZoneMinder/zoneminder`)
- **ioBroker** (`ioBroker/ioBroker`)
- **Node-RED** (`node-red/node-red`) — automation flows

For each, scan: recent release notes, top issues with `enhancement` /
`feature-request` labels, GitHub Discussions wishlist threads.

## Security-driven candidates

Once per cycle is fine. Scan CVEs affecting the dependency stack:
Flask, Werkzeug, gunicorn, OpenSSL, libcamera, FFmpeg, Yocto layer pins.
A CVE-driven hardening issue counts toward the daily cap of 3.

## Out of scope (don't file)

- regulatory clearance work (FCC, CE)
- hardware redesign (new SoC, sensor changes)
- major architecture rework (replacing Flask with FastAPI, ditching Yocto)
- cloud-only features (the project is self-hosted by design)
- proprietary integrations (Ring, Nest, Arlo APIs — focus on open ones)

## Issue body format

Every issue MUST contain these sections (in this order):

```
## Goal
<user/operator outcome — one paragraph>

## Why this fits the mission
<reference docs/ai/mission-and-goals.md and repo-map.md>

## Sources
- <URL to competing project's feature / discussion / CVE>
- <URL to a second source>

## Rough scope (Architect will refine)
- Likely area: app/server/ | app/camera/ | meta-home-monitor/ | docs/
- Estimated module impact: <files>
- Likely risk class: low | medium | high (per ISO 14971-lite framing)

## Out of scope
- <what we're explicitly NOT doing>
```

No source = no issue. Cite at least one external link.
