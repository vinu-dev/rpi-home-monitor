# Feature Specs

This directory holds implementation-ready specs for features that are approved
for planning or active delivery. The goal is simple: an AI coding agent should
be able to pick up a spec from here and work without inventing product behavior.

## How To Use This Directory

- start from the relevant release plan under `docs/history/releases/`
- read the feature spec before creating or taking an issue
- treat the spec as the execution contract unless a newer ADR or approved issue
  explicitly overrides it
- if implementation changes behavior, update the spec in the same branch

## Current Specs

- [TOTP-based 2FA for admin and remote users](238-totp-2fa.md)
- [Outbound webhook delivery](239-outbound-webhooks.md)
- [Offsite / cloud backup of recordings](243-offsite-backup.md)
- [Rich motion notifications](r1-rich-motion-notifications.md)
- [Local alert center and Tailscale-remote review](r1-local-alert-center-and-tailscale-remote-review.md)
- [Camera offline alerts](r1-camera-offline-alerts.md)
- [Storage low / retention risk alerts](r1-storage-retention-alerts.md)
- [Review queue for important events](r1-review-queue.md)

## Spec Requirements

Each spec should define:

- problem and user value
- exact scope and explicit non-goals
- precise user-visible behavior
- architecture fit and preferred implementation shape
- trust/security/privacy constraints
- testing expectations
- rollout notes
- open questions or deferred items

## Naming

- prefix with the release wave when the spec is release-scoped
- keep one user-facing feature per file
- avoid mixing multiple architecture bets into one spec
