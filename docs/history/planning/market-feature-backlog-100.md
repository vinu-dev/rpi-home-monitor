# Market-Driven Feature Backlog (100 Items)

Date: 2026-04-20
Owner: Codex market/product research pass
Scope: Open-source and commercial surveillance/NVR products
Purpose: Convert competitor research into a de-duplicated, prioritized feature backlog that fits the current RPi Home Monitor architecture.

## Method

This backlog was built from official docs, product pages, and help-center content across:

- Open-source: Frigate, Scrypted NVR, Shinobi, Blue Iris
- Commercial: Synology Surveillance Station, UniFi Protect, Ring, Google Nest Aware, Arlo Secure, TP-Link Tapo, Reolink, Wyze, Eufy

The goal is not "copy competitors." The goal is to identify:

- table-stakes features where the product is currently behind
- differentiators that fit the local-first/self-hosted positioning
- features the current architecture can support without a full redesign

## Priority Model

- `P0`: Market-critical parity or trust gap. Should be planned early.
- `P1`: Strong next-wave features with clear user/business value.
- `P2`: Valuable expansion after core gaps are closed.
- `P3`: Longer-horizon or niche features.

## Implementation Waves

- `W1`: Can start with current app/event architecture and minimal protocol change.
- `W2`: Builds on W1 and existing event model.
- `W3`: Needs stronger detection metadata, richer settings, or deeper UI work.
- `W4`: Needs cross-feature coordination or moderate platform changes.
- `W5`: Needs new services, APIs, or external integration surfaces.
- `W6`: Needs heavier compute, new storage/indexing, or broader product scope.
- `W7`: Needs hardware refresh, mobile app, or major protocol expansion.
- `W8`: Strategic/advanced platform work.

## Architecture Fit Legend

- `Web`: mostly server UI/API/frontend work
- `Server`: backend/service logic on the monitor server
- `Camera`: camera-side logic or protocol expansion
- `Data`: persistence/indexing/search changes
- `Ops`: Yocto/build/release/admin changes
- `Integrations`: Home Assistant, webhooks, MQTT, external services

## Competitor Shorthand

- `Frigate`
- `Scrypted`
- `Shinobi`
- `Blue Iris`
- `Synology`
- `UniFi`
- `Ring`
- `Nest`
- `Arlo`
- `Tapo`
- `Reolink`
- `Wyze`
- `Eufy`

## Backlog

### A. Alerts and Awareness (1-15)

| # | Feature | Priority | Wave | Architecture Fit | Market Signal | Expected Behavior |
|---|---|---|---|---|---|---|
| 1 | Local alert center for motion activity | P0 | W1 | Web, Server | Frigate review, Scrypted timeline, Synology alerts, Ring event UX | User sees important motion activity immediately in the local UI, with the same experience available remotely over Tailscale. |
| 2 | Rich alert cards with snapshot thumbnail | P0 | W1 | Web, Server | Scrypted, Arlo, Wyze, Tapo | Alert card includes camera name, event type, timestamp, and thumbnail; opening it lands on the matching event. |
| 3 | Per-camera notification preferences | P0 | W1 | Web, Server | Ring, Arlo, Tapo, Wyze | User can enable notifications for selected cameras only. |
| 4 | Notification cooldown / anti-spam windows | P0 | W1 | Server, Web | Ring, Synology, UniFi | Multiple events in a burst are coalesced to reduce notification fatigue. |
| 5 | Quiet hours / sleep schedule for alerts | P1 | W1 | Server, Web | Ring, Arlo, phone OS norms | User can suppress non-critical alerts during configured time windows. |
| 6 | Camera offline / heartbeat failure alerts | P0 | W1 | Server, Web | Synology, UniFi, Wyze | User gets an alert when a camera misses heartbeats long enough to be considered offline. |
| 7 | Storage low / retention risk alerts | P0 | W1 | Server, Web | Synology, Blue Iris, UniFi | Server warns when free space or retention horizon drops below safe thresholds. |
| 8 | OTA update success/failure alerts | P1 | W1 | Server, Ops, Web | Synology, UniFi, commercial appliance norms | Operator receives status alerts when a server or camera update succeeds, fails, or rolls back. |
| 9 | Daily/weekly security digest | P2 | W2 | Server, Web | Ring, commercial security UX patterns | User can receive a summary of recent motion, outages, and storage/update issues. |
| 10 | Geofenced home/away notification rules | P1 | W3 | Web, Server, Integrations | UniFi, Tapo, Ring | Alert intensity changes automatically when the primary user leaves or returns home. |
| 11 | Multi-channel delivery (webhook, local automation, optional messaging) | P1 | W3 | Server, Integrations | Synology, UniFi, Tapo, Shinobi | Same event can be routed to one or more destinations beyond the built-in alert center, without becoming cloud-required. |
| 12 | Alert severity levels | P2 | W3 | Server, Web | Synology, UniFi Alarm Manager | Events can be classified as info, warning, or critical with different delivery rules. |
| 13 | Per-user notification routing | P2 | W3 | Server, Web | Scrypted, Ring, Synology | Different users can receive different alerts from the same system. |
| 14 | Escalation rules for unanswered alerts | P3 | W5 | Server, Integrations | Alarm-center products, UniFi Alarm Manager | If one alert is not acknowledged, the system escalates to another channel or user. |
| 15 | Alert acknowledgment / snooze states | P2 | W2 | Web, Server, Data | Synology, UniFi, operator products | User can mark an alert handled, snooze a camera, or mute a noisy event source temporarily. |

### B. Detection and Intelligence (16-35)

| # | Feature | Priority | Wave | Architecture Fit | Market Signal | Expected Behavior |
|---|---|---|---|---|---|---|
| 16 | Person detection | P0 | W2 | Camera, Server | Ring, Arlo, Tapo, Reolink, Wyze | Events distinguish person motion from generic motion and surface as a first-class type. |
| 17 | Vehicle detection | P1 | W2 | Camera, Server | Ring, Arlo, Tapo, Reolink | Vehicle events are recognized and can drive alerts, filters, and search. |
| 18 | Pet / animal detection | P1 | W2 | Camera, Server | Arlo, Tapo, Reolink, Wyze | Pets/animals become a distinct event type rather than generic motion. |
| 19 | Package detection | P0 | W3 | Camera, Server | Ring, Arlo, Tapo, UniFi | Front-door cameras can identify package drop and pickup events. |
| 20 | Familiar face recognition | P2 | W5 | Camera, Server, Data | Nest, Eufy, Scrypted, Frigate | User can label known faces and separate familiar vs unknown-person events. |
| 21 | Known vs unknown person alerting | P2 | W5 | Server, Data, Web | Nest, Eufy | Unknown people can trigger stronger alerts than known household members. |
| 22 | License plate recognition | P2 | W6 | Camera, Server, Data | Scrypted, Synology, some commercial systems | Vehicle events can optionally include a searchable license plate value. |
| 23 | Descriptive AI event summaries | P1 | W5 | Server, Data | Wyze, Frigate GenAI | Event cards and alerts include short natural-language descriptions of what happened. |
| 24 | Semantic search over events | P2 | W6 | Server, Data | Frigate, Scrypted | User can search history using natural phrases like "red car at night" or "person with backpack." |
| 25 | Motion zones / activity zones | P0 | W2 | Web, Camera, Server | Frigate, UniFi, Ring, Tapo, Wyze | User draws zones so only motion inside meaningful regions triggers events. |
| 26 | Privacy zones | P0 | W2 | Web, Camera, Server | Ring, Tapo, UniFi | User masks sensitive regions so they are not visible or not processed. |
| 27 | Line-crossing detection | P1 | W3 | Camera, Server | Tapo, Synology, enterprise camera norms | Event fires when motion crosses a defined directional boundary. |
| 28 | Intrusion / region-enter detection | P1 | W3 | Camera, Server | Tapo, Synology, Eufy | Event fires when an object enters or remains in a watched area. |
| 29 | Loitering / dwell-time detection | P2 | W4 | Camera, Server | Synology, enterprise VMS patterns | Event escalates when a person remains in a zone longer than a threshold. |
| 30 | Detection sensitivity profiles | P1 | W2 | Web, Camera | Ring, Tapo, Wyze | User can pick presets like low, medium, high or tune thresholds per camera. |
| 31 | Object size and confidence filters | P1 | W3 | Camera, Server | Frigate filters, false-positive reduction patterns | Very small or low-confidence detections are suppressed. |
| 32 | Day/night detection profiles | P2 | W4 | Camera, Server | Commercial camera analytics norms | Different sensitivity and rules apply during night vs daylight. |
| 33 | Camera onboard-AI passthrough | P2 | W4 | Camera, Server | Scrypted, Tapo ONVIF/RTSP ecosystem | If a third-party camera exposes smart events, ingest them instead of duplicating compute. |
| 34 | Tamper detection | P1 | W3 | Camera, Server | Tapo, commercial systems | Alert when the camera is covered, moved, defocused, or otherwise disrupted. |
| 35 | Sound event detection | P2 | W5 | Camera, Server | Nest, Arlo, Wyze | Detect sound classes such as glass break, baby cry, or alarm tones where supported. |

### C. Review, Search, and Playback (36-55)

| # | Feature | Priority | Wave | Architecture Fit | Market Signal | Expected Behavior |
|---|---|---|---|---|---|---|
| 36 | Fast "review queue" of important events | P0 | W1 | Web, Server | Frigate review, Scrypted timeline, Ring event timeline | User opens a condensed queue of key events instead of scrubbing raw recordings. |
| 37 | Event bookmarks / starring | P1 | W1 | Web, Data | Synology, commercial NVR norms | User can mark events/clips as important for later retrieval. |
| 38 | Protected clips exempt from rotation | P1 | W2 | Server, Data | Commercial NVRs, evidence workflows | Protected clips are never deleted by loop retention until explicitly unprotected. |
| 39 | Download/export by event | P1 | W1 | Web, Server | Ring, Arlo, Synology | User can export a clip directly from an event card. |
| 40 | Download/export by time range | P1 | W2 | Web, Server | Synology, Blue Iris | User chooses camera plus time window and gets a stitched export. |
| 41 | Timelapse generation | P2 | W4 | Server, Data | Reolink, Synology, Scrypted | User can generate a timelapse over a selected time range. |
| 42 | Scrubbable timeline with event markers | P0 | W1 | Web, Server | Synology, Ring, UniFi | Timeline shows motion/detection markers and supports direct seek. |
| 43 | Calendar heatmap of activity | P2 | W3 | Web, Data | Commercial VMS patterns | Days/hours with more events are visually highlighted. |
| 44 | Search by event type | P0 | W1 | Web, Server | All serious NVRs | User filters history by motion, person, package, offline, update, etc. |
| 45 | Search by camera group / location | P1 | W1 | Web, Server | Synology, UniFi, multi-camera UX norms | User filters events to driveway, front door, backyard, or custom camera groups. |
| 46 | Search by known face / plate / label | P2 | W6 | Web, Data | Scrypted, Nest, Frigate, Eufy | User searches history for a specific known person, plate, or labeled class. |
| 47 | Search by zone | P2 | W3 | Web, Data | Frigate zones, commercial analytics | User filters to events that occurred in a selected zone. |
| 48 | Saved searches and saved views | P2 | W3 | Web, Data | Analytics and VMS tooling patterns | User can save recurring filters such as "unknown person at front door." |
| 49 | Cross-camera event correlation view | P1 | W4 | Server, Data, Web | Eufy cross-camera tracking, operator consoles | Nearby events from multiple cameras are grouped into one incident view. |
| 50 | Multi-camera synchronized playback | P2 | W5 | Server, Data, Web | Synology, enterprise VMS | User scrubs one time cursor and sees multiple camera angles at once. |
| 51 | Clean snapshots vs annotated snapshots | P1 | W1 | Server, Web | Frigate snapshot variants | User can choose whether to export/share a clean image or one with overlays. |
| 52 | Event notes / operator annotations | P2 | W3 | Web, Data | Security operations workflows | User can add notes like "courier" or "false alert" to incidents. |
| 53 | Incident case folders | P3 | W6 | Web, Data | Commercial evidence systems | User groups multiple clips/events into a single case or incident package. |
| 54 | Recycle bin / undo delete | P2 | W4 | Server, Data | Consumer storage UX norms | Deleted clips remain recoverable for a short retention period. |
| 55 | Shareable expiring links for clips | P2 | W5 | Web, Server, Security | Ring, Arlo, cloud products | User can create a time-limited link to a clip without exposing the rest of the system. |

### D. Live Experience and Camera Control (56-70)

| # | Feature | Priority | Wave | Architecture Fit | Market Signal | Expected Behavior |
|---|---|---|---|---|---|---|
| 56 | Birdseye / multi-camera live overview | P1 | W2 | Server, Web | Frigate Birdseye, VMS wallboards | User sees a live auto-switching overview of the most active cameras. |
| 57 | Camera groups in live view | P1 | W1 | Web, Server | Frigate groups, UniFi, Synology | User can create custom live-view groups such as "outside" or "entries." |
| 58 | Full-screen wallboard / kiosk mode | P2 | W2 | Web | Commercial NVR dashboards | UI supports a TV or tablet dashboard with minimal controls. |
| 59 | Instant replay buffer | P2 | W3 | Server, Web | UniFi, sports replay UX patterns | From live view, user can jump back 15-60 seconds without opening recordings. |
| 60 | Low-bandwidth live mode | P1 | W2 | Camera, Server, Web | Scrypted adaptive bitrate, commercial mobile apps | Mobile clients can request lower bitrate / lower FPS live feeds. |
| 61 | Adaptive bitrate / stream selection | P2 | W4 | Camera, Server, Web | Scrypted, modern camera platforms | System automatically chooses the best stream profile for the viewer's network. |
| 62 | Two-way audio | P1 | W5 | Camera, Server, Web | Ring, Nest, Wyze, Scrypted | User can talk to a visitor from live view. |
| 63 | Quick replies / prerecorded talk-down | P2 | W5 | Camera, Web | Ring quick replies, active deterrence UX | User can play a predefined message like "Leave package at the door." |
| 64 | PTZ controls | P2 | W5 | Camera, Server, Web | UniFi PTZ, Reolink, enterprise cameras | For capable cameras, user can pan, tilt, and zoom from live view. |
| 65 | PTZ presets and patrols | P2 | W6 | Camera, Server, Web | Reolink, enterprise cameras | User can save viewpoints and optionally run patrol routines. |
| 66 | Auto-tracking | P3 | W7 | Camera, Server | Wyze Pan tracking, PTZ platforms | PTZ camera follows a moving person/vehicle automatically. |
| 67 | Doorbell press events | P1 | W5 | Camera, Server, Web | Ring, Arlo, Nest, Tapo | Doorbell devices generate a distinct event and alert separate from motion. |
| 68 | Package-zone-optimized doorbell view | P2 | W5 | Camera, Web | Ring, Arlo doorbells | Front-door UI emphasizes package area and doorstep interactions. |
| 69 | Manual deterrence controls (siren/light) | P1 | W4 | Camera, Server, Web | Reolink, Ring, Arlo, Tapo | User can activate camera siren, spotlight, or warning on demand. |
| 70 | Auto deterrence on selected detections | P2 | W4 | Camera, Server | Ring Active Warnings, Reolink dual alarms | Camera can speak, flash, or siren automatically for selected event classes. |

### E. Automation and Integrations (71-85)

| # | Feature | Priority | Wave | Architecture Fit | Market Signal | Expected Behavior |
|---|---|---|---|---|---|---|
| 71 | Home Assistant integration | P1 | W2 | Integrations, Server | Frigate, Scrypted, Tapo, Ring/Alexa ecosystems | Cameras, events, and controls are exposed cleanly to Home Assistant. |
| 72 | MQTT event bus | P1 | W3 | Integrations, Server | Frigate, home automation ecosystems | Event topics are published for automation consumers on the local network. |
| 73 | Generic webhook actions | P1 | W3 | Server, Integrations | UniFi Alarm Manager, Synology | Events can trigger outbound webhooks with useful payloads. |
| 74 | Rule-based automation engine | P1 | W4 | Server, Data, Integrations | UniFi Alarm Manager, Tapo Smart Actions | User can build if-this-then-that rules from events and conditions. |
| 75 | Schedule-aware automations | P2 | W4 | Server, Integrations | Tapo Smart Actions, commercial systems | Automations can run only during specified times/days. |
| 76 | Presence-aware automations | P2 | W4 | Server, Integrations | UniFi geofence, Tapo geofencing | Home/away state can change notification and automation behavior. |
| 77 | Linked sensors on the timeline | P2 | W5 | Integrations, Data, Web | Scrypted nearby devices | Door, lock, or motion sensors appear on the camera timeline as related events. |
| 78 | Nearby device controls in camera view | P2 | W5 | Integrations, Web | Scrypted nearby devices | User can toggle linked lights, locks, or sirens from the live camera page. |
| 79 | Smart relay / GPIO actions | P2 | W5 | Server, Ops, Integrations | Embedded/local automation patterns | Server GPIO or relay modules can drive local sirens, lights, or gates. |
| 80 | Voice-assistant announcements | P3 | W6 | Integrations | Ring/Alexa, Nest/Google, Tapo | Person/package events can be announced on smart speakers. |
| 81 | Slack / Teams / ServiceNow webhooks | P3 | W6 | Integrations | UniFi Alarm Manager | Business/ops users can push alerts into collaboration tools. |
| 82 | Local REST API for automation clients | P1 | W2 | Server, Integrations | Frigate, commercial APIs | External clients can query events, clips, health, and controls through a stable API. |
| 83 | Event-triggered scene actions | P2 | W5 | Integrations | Tapo Smart Actions, smart-home norms | Example: turn on porch light when front-door person event fires. |
| 84 | Camera profile switching by automation | P3 | W6 | Camera, Server | Enterprise cameras, policy automation | System can raise bitrate or FPS during an active incident, then restore baseline. |
| 85 | Incident webhook with evidence package | P3 | W6 | Server, Integrations | Security operations tools | A serious incident can emit a bundled payload with clip, snapshot, and metadata. |

### F. Device, Fleet, Storage, and Trust (86-100)

| # | Feature | Priority | Wave | Architecture Fit | Market Signal | Expected Behavior |
|---|---|---|---|---|---|---|
| 86 | Bulk camera settings | P1 | W2 | Web, Server | Synology, UniFi | User can apply selected settings to multiple cameras at once. |
| 87 | Camera templates / profiles | P1 | W2 | Server, Web | UniFi, fleet-management UX | User creates reusable camera profiles like "front door" or "indoor low-noise." |
| 88 | Bulk OTA orchestration | P1 | W3 | Server, Ops | UniFi, appliance platforms | Admin can stage, schedule, and roll out updates to multiple cameras safely. |
| 89 | Maintenance windows for updates | P2 | W3 | Server, Ops | Synology, UniFi | Updates can be deferred to a safe time window. |
| 90 | Camera tags, areas, and site metadata | P1 | W1 | Server, Web | Synology, UniFi, commercial NVRs | Cameras can be tagged by site, floor, area, purpose, or risk level. |
| 91 | Health trends and historical diagnostics | P2 | W3 | Server, Data, Web | Synology, admin platforms | User can inspect CPU temp, memory, dropouts, and storage trends over time. |
| 92 | Support bundle / diagnostic export | P1 | W2 | Server, Ops | Synology, appliance support norms | Admin can export a scrubbed support package with logs, config, and health state. |
| 93 | Config backup and restore | P1 | W2 | Server, Ops | Nearly all appliance products | User can export/import settings, users, cameras, and retention policy. |
| 94 | Retention policy per camera | P1 | W2 | Server, Data | Synology, Blue Iris | High-value cameras can retain more history than low-value ones. |
| 95 | Retention policy by event type | P2 | W3 | Server, Data | Enterprise VMS patterns | Person/package events can outlive generic motion or health events. |
| 96 | External archive target (NAS / object storage) | P2 | W5 | Server, Data, Integrations | Synology, Blue Iris, Scrypted ecosystems | Older clips can be offloaded to secondary storage without breaking history. |
| 97 | End-to-end signed evidence manifests | P2 | W6 | Server, Data, Security | Compliance/evidence workflows | Exported clips include hashes and metadata proving they were not altered after export. |
| 98 | Advanced roles and delegated admin | P2 | W4 | Server, Web | Synology, enterprise systems | Support roles like operator, installer, auditor, and household guest. |
| 99 | TOTP / stronger 2FA | P1 | W2 | Server, Web, Security | Wyze, modern security platforms | Admin and remote users can protect accounts beyond password-only login. |
| 100 | Multi-site management | P3 | W8 | Server, Web, Integrations | UniFi, Synology C2, commercial platforms | One operator can manage several home/server instances from a unified dashboard. |

## Recommended Top 20 To Plan First

These are the strongest blend of market pressure, user value, and fit to the current architecture:

1. Local alert center for motion activity
2. Rich alert cards with snapshots
3. Per-camera notification rules
4. Camera offline alerts
5. Storage low alerts
6. Person detection
7. Motion zones / activity zones
8. Privacy zones
9. Review queue
10. Scrubbable timeline with event markers
11. Bulk camera settings
12. Camera templates / profiles
13. Config backup and restore
14. TOTP / stronger 2FA
15. Doorbell/package detection track
16. Home Assistant integration
17. MQTT event bus
18. Bulk OTA orchestration
19. Protected clips
20. Diagnostic export bundle

## Suggested Implementation Order

### Wave 1: Awareness and control fundamentals

- Notifications
- alerting on failures
- review queue
- timeline polish
- camera tags/areas

### Wave 2: Detection quality and household usability

- person detection
- motion zones
- privacy zones
- bulk camera settings
- templates
- config backup
- 2FA
- Home Assistant base integration

### Wave 3: Automation-ready product shape

- per-camera advanced routing
- retention by camera/event
- webhooks/MQTT
- package detection
- bulk OTA
- health history

### Wave 4 and beyond: differentiation

- semantic search
- face recognition
- LPR
- two-way audio
- cross-camera incidents
- external archive
- delegated admin

## Notes on Architectural Fit

The current architecture is already a good base for a large part of this backlog:

- Motion events already exist and can power alerts, review, and automations.
- The server already owns policy and UI, which is ideal for notification rules, retention policy, search, and operator workflows.
- The camera control channel can support richer device behavior later, but should be hardened first if control-plane features expand.
- The local-first design is an advantage for notifications, automations, Home Assistant, MQTT, and privacy-sensitive search features.

The biggest classes of features that need deeper platform investment are:

- advanced AI search and recognition
- two-way audio and richer live transport controls
- PTZ and doorbell device classes
- multi-site and more enterprise-style account models

## Source Appendix

### Open-source

- Frigate notifications: <https://docs.frigate.video/configuration/notifications>
- Frigate review workflow: <https://docs.frigate.video/configuration/review>
- Frigate zones and filters: <https://docs.frigate.video/configuration/zones>, <https://docs.frigate.video/configuration/object_filters/>
- Frigate semantic search: <https://docs.frigate.video/configuration/semantic_search/>
- Frigate object descriptions / GenAI: <https://docs.frigate.video/configuration/genai/genai_objects>
- Frigate Birdseye: <https://docs.frigate.video/configuration/birdseye>
- Frigate restream: <https://docs.frigate.video/configuration/restream/>
- Frigate face recognition: <https://docs.frigate.video/configuration/face_recognition/>
- Frigate custom/object classification: <https://docs.frigate.video/configuration/custom_classification/object_classification/>
- Scrypted features: <https://docs.scrypted.app/scrypted-nvr/features.html>
- Scrypted nearby devices: <https://docs.scrypted.app/scrypted-nvr/nearby-devices.html>
- Scrypted smart motion sensor and LPR: <https://docs.scrypted.app/detection/smart-motion-sensor.html>
- Scrypted motion detection: <https://docs.scrypted.app/detection/motion-detection.html>
- Scrypted Home Assistant integration: <https://docs.scrypted.app/home-assistant.html>
- Scrypted onboard camera AI: <https://docs.scrypted.app/scrypted-nvr/camera-ai.html>
- Shinobi motion docs: <https://docs.shinobi.video/detect/motion>
- Blue Iris reference PDF: <https://blueirissoftware.com/BlueIris.PDF>

### Commercial

- Synology Surveillance Station software spec: <https://www.synology.com/en-us/dsm/software_spec/surveillance_station>
- Synology live view alerts: <https://www.synology.com/en-us/surveillance/feature/live_view_alert>
- UniFi Protect location-based alerts: <https://help.ui.com/hc/en-us/articles/360037982314-UniFi-Protect-Configuring-Location-Based-Activity-Alerts>
- UniFi Protect camera zones: <https://help.ui.com/hc/en-us/articles/360056987954-UniFi-Protect-Manage-Camera-Zones>
- UniFi Alarm Manager: <https://help.ui.com/hc/en-us/articles/27721287753239-UniFi-Alarm-Manager-Customize-Alerts-Integrations-and-Automations-Across-UniFi>
- Ring smart alerts and package alerts: <https://ring.com/us/en/support/articles/rk2nf/How-to-Get-Package-Alerts>
- Ring privacy zones: <https://ring.com/gb/en/support/articles/g4e2w/Creating-and-Deleting-Privacy-Zones>
- Ring Bird's Eye zones: <https://ring.com/support/articles/nx1sf/How-to-Use-Birds-Eye-Zones>
- Ring active warnings: <https://ring.com/support/articles/mx2o5/active-warnings>
- Ring Alexa announcements: <https://ring.com/us/en/support/articles/2mgbt/Setting-Up-Alexa-Person-and-Package-Announcements>
- Google Nest Aware: <https://support.google.com/googlenest/answer/13315909>
- Google Home automation starters: <https://support.google.com/googlenest/answer/15684394>
- Arlo advanced object detection: <https://www.arlo.com/da_dk/support/faq/000062255/What-are-the-Arlo-Smart-advanced-object-detection-features>
- Arlo smart notifications: <https://www.arlo.com/en_gb/support/faq/000062927/what-are-arlo-s-advanced-motion-alerts-and-how-do-i-set-them-up-arlo-secure-4-0>
- TP-Link Tapo C720: <https://www.tp-link.com/us/home-networking/cloud-camera/tapo-c720/>
- TP-Link Tapo C401: <https://www.tp-link.com/us/products/details/tapo-c401.html>
- TP-Link Tapo C230: <https://www.tp-link.com/us/home-networking/cloud-camera/tapo-c230/>
- TP-Link Tapo Smart Actions: <https://www.tp-link.com/us/support/faq/2734/>
- Reolink ColorX / smart detection / local recording example: <https://reolink.com/us/product/reolink-rlk8-cx410b4/>
- Wyze detection zones: <https://support.wyze.com/hc/en-us/articles/360051212491-Detection-Settings-and-Zones>
- Wyze VerifiedView: <https://support.wyze.com/hc/en-us/articles/38125840497819-What-is-VerifiedView>
- Wyze descriptive alerts: <https://support.wyze.com/hc/en-us/articles/32693597570587-What-is-Descriptive-Alert>
- Wyze automations: <https://support.wyze.com/hc/en-us/articles/35083741294363-Wyze-Cam-OG-Automations>
- Eufy smart features / cross-camera tracking: <https://www.eufy.com/rs-en/security-features>
- Eufy E330 example: <https://www.eufy.com/au/products/eufy-security-eufycam-e330-add-on-camera>
