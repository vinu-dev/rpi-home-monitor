# Intended Use

Status: Draft prepared to support expert regulatory review.

## Intended Use Statement

The RPi Home Monitor is intended to provide local, self-hosted home video
monitoring using Raspberry Pi server and camera hardware. It provides live
viewing, recording, storage management, device health visibility, local alerts,
pairing, and update mechanisms for household security and awareness.

## Explicit Exclusions

- The system is not intended for diagnosis, treatment, prevention, mitigation,
  monitoring, or management of disease or injury.
- The system is not intended for life support, life sustaining use, emergency
  response dispatch, clinical surveillance, or patient monitoring.
- The system is not intended to replace smoke alarms, carbon monoxide alarms,
  professional security monitoring, emergency call systems, or medical alert
  systems.

REGULATORY REVIEW REQUIRED: If intended use changes toward medical monitoring,
elder care, fall detection, clinical observation, or emergency response, update
this record and perform qualified regulatory review before implementation or
release.

## Intended Users

- Primary operator: homeowner or self-hosting operator.
- Secondary user: household viewer with restricted access.
- Service role: developer/operator maintaining builds, updates, and backups.

## Intended Environment

- Local home network with WiFi/Ethernet.
- Raspberry Pi server and Raspberry Pi camera nodes.
- Optional remote access through operator-managed VPN such as Tailscale.
- No mandatory vendor cloud service.

## Assumptions

- ASSUMPTION: Operators control their local network and physical devices.
- ASSUMPTION: Hardware can fail without warning; alerts reduce but do not
  eliminate loss of monitoring.
- ASSUMPTION: AI-generated records in this repository require human review.

## Open Questions

- OPEN QUESTION: Should market-facing language explicitly prohibit medical or
  emergency use?
- OPEN QUESTION: What retention period and privacy disclosures are required for
  target deployments?
