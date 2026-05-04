---
title: Documentation Front Door
status: active
audience: [human, ai]
owner: engineering
source_of_truth: true
---

# Documentation

This is the front door for humans and AI agents. Use it to find the current
source of truth quickly without loading every historical note into context.

Machine-readable metadata lives in [`doc-map.yml`](doc-map.yml).

## Agent Tooling Basis

This layout is intentionally optimized for Codex, Claude Code, and humans:

- short root adapters (`AGENTS.md`, `CLAUDE.md`) point to canonical docs instead
  of duplicating policy;
- `doc-map.yml` gives agents a small routing table before they load deeper
  context;
- historical and archived records stay available but are clearly separated from
  current source-of-truth records;
- validation commands are listed next to the docs they protect.

This follows current public agent-tooling guidance to keep persistent project
memory specific, structured, reviewed as the repo evolves, and backed by
programmatic checks:

- [Claude Code memory](https://docs.anthropic.com/en/docs/claude-code/memory)
- [Claude Code organization best practices](https://docs.anthropic.com/en/docs/claude-code/third-party-integrations)
- [OpenAI Codex AGENTS.md behavior](https://openai.com/index/introducing-codex/)
- [How OpenAI uses Codex](https://openai.com/business/guides-and-resources/how-openai-uses-codex/)

## Start Here

| Need | Read |
|---|---|
| AI agent operating rules | [`ai/index.md`](ai/index.md) |
| Repository routing and validation | [`ai/repo-map.md`](ai/repo-map.md) |
| Product intent | [`intended-use/intended-use.md`](intended-use/intended-use.md) |
| Requirements | [`requirements/system-requirements.md`](requirements/system-requirements.md), [`requirements/software-requirements.md`](requirements/software-requirements.md), [`requirements/hardware-requirements.md`](requirements/hardware-requirements.md) |
| Architecture | [`architecture/system-architecture.md`](architecture/system-architecture.md), [`architecture/software-architecture.md`](architecture/software-architecture.md), [`architecture/hardware-architecture.md`](architecture/hardware-architecture.md) |
| Safety risk | [`risk/hazard-analysis.md`](risk/hazard-analysis.md), [`risk/risk-control-verification.md`](risk/risk-control-verification.md) |
| Cybersecurity | [`cybersecurity/threat-model.md`](cybersecurity/threat-model.md), [`cybersecurity/security-risk-analysis.md`](cybersecurity/security-risk-analysis.md), [`cybersecurity/third-party-libraries.md`](cybersecurity/third-party-libraries.md) |
| Verification | [`verification-validation/test-plan.md`](verification-validation/test-plan.md), [`verification-validation/test-cases.md`](verification-validation/test-cases.md) |
| Traceability | [`traceability/traceability-matrix.md`](traceability/traceability-matrix.md), [`traceability/traceability-matrix.csv`](traceability/traceability-matrix.csv) |

## Human Guides

Use [`guides/README.md`](guides/README.md) for practical operating and
development material:

- [`guides/hardware-setup.md`](guides/hardware-setup.md)
- [`guides/build-setup.md`](guides/build-setup.md)
- [`guides/development-guide.md`](guides/development-guide.md)
- [`guides/testing-guide.md`](guides/testing-guide.md)
- [`guides/diagnostics-export.md`](guides/diagnostics-export.md)
- [`guides/release-runbook.md`](guides/release-runbook.md)
- [`guides/ota-key-management.md`](guides/ota-key-management.md)
- [`guides/account-security.md`](guides/account-security.md)
- [`guides/admin-recovery.md`](guides/admin-recovery.md)
- [`guides/network-fallback.md`](guides/network-fallback.md)

## Operations Runbooks

Operator-readable deployment and incident-response runbooks live here:

- [`operations/secrets-inventory.md`](operations/secrets-inventory.md)

## History

Use [`history/README.md`](history/README.md) for design history and planning
records that inform the current controlled records but are not themselves the
first place to edit:

- [`history/adr/`](history/adr/)
- [`history/specs/`](history/specs/)
- [`history/releases/`](history/releases/)
- [`history/baseline/`](history/baseline/)
- [`history/planning/`](history/planning/)

## Active And Archived Plans

Use [`exec-plans/README.md`](exec-plans/README.md) only for active, resumable
implementation plans and the template. Completed plans and field records live
under [`archive/README.md`](archive/README.md).

## Rules For AI Agents

1. Read [`AGENTS.md`](../AGENTS.md), then this file, then
   [`ai/index.md`](ai/index.md).
2. Use [`doc-map.yml`](doc-map.yml) to decide which docs are current,
   historical, archived, or guide material.
3. Do not treat `archive/` or `history/` as current source of truth unless a
   controlled record explicitly links to it.
4. Keep generated adapters short and regenerate them with
   `python scripts/ai/build_instruction_files.py`.
5. Run the documentation and traceability checks before PR handoff.

## Validation

```bash
python tools/docs/check_doc_map.py
python scripts/ai/check_doc_links.py
python scripts/ai/validate_repo_ai_setup.py
python tools/traceability/check_traceability.py
pre-commit run --all-files
```
