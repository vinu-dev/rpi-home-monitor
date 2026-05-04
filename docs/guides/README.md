---
title: Guide Index
status: active
audience: [human, ai]
owner: engineering
source_of_truth: false
---

# Guides

This folder holds practical operating and development instructions. Guides are
optimized for doing work; controlled requirements, risk, security, and
traceability records live in their dedicated folders.

## Start Here

| Need | Read |
|---|---|
| Build the project or images | [`build-setup.md`](build-setup.md) |
| Work on application code | [`development-guide.md`](development-guide.md) |
| Run validation | [`testing-guide.md`](testing-guide.md) |
| Export or inspect diagnostics bundles | [`diagnostics-export.md`](diagnostics-export.md) |
| Assemble or provision hardware | [`hardware-setup.md`](hardware-setup.md) |
| Release safely | [`release-runbook.md`](release-runbook.md) |
| Manage OTA keys | [`ota-key-management.md`](ota-key-management.md) |
| Manage account security and 2FA | [`account-security.md`](account-security.md) |
| Recover admin access | [`admin-recovery.md`](admin-recovery.md) |
| Plan an AI-delivered feature | [`ai-feature-template.md`](ai-feature-template.md) |
| Understand connectivity constraints | [`connectivity-and-privacy-constraints.md`](connectivity-and-privacy-constraints.md) |

## Editing Rules

- Keep guides procedural and current.
- Link controlled requirements, architecture, risk, security, and tests when a
  guide changes behavior or validation expectations.
- Move obsolete guide material to [`../history/`](../history/) or
  [`../archive/`](../archive/) instead of leaving stale instructions in place.
