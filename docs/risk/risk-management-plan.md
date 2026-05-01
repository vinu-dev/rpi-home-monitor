# Risk Management Plan

Status: Draft prepared to support expert regulatory review.

## Purpose

This plan defines how this repository records product safety risks, risk
controls, DFMEA items, and verification links. It does not claim compliance
with any standard.

## Scope

The current scope is the local-first home monitoring system described in
`docs/intended-use/intended-use.md`. If the intended use changes toward medical
monitoring, clinical decision support, emergency response, or life-safety
claims, qualified regulatory review is required before release.

## Method

- Identify hazards as `HAZ-###`.
- Record safety risks as `RISK-###`.
- Record risk controls as `RC-###`.
- Record design failure modes as `DFMEA-###`.
- Link each risk to requirements, architecture, code, and tests.
- Verify each control through automated or manual test cases.

## Risk Ranking

Severity and probability are qualitative draft rankings for engineering
review:

- Severity: S1 negligible, S2 minor, S3 moderate, S4 serious, S5 critical.
- Probability: P1 remote, P2 unlikely, P3 occasional, P4 likely, P5 frequent.
- Initial and residual risk levels: Low, Medium, High.

REGULATORY REVIEW REQUIRED: A qualified reviewer must approve severity,
probability, risk acceptability, and residual risk before these records are
treated as controlled quality evidence.

## Review Triggers

Review risk records when changing:

- capture, recording, storage, motion, alerts, or offline logic
- authentication, pairing, certificate handling, recovery, or OTA
- hardware assumptions, image recipes, service hardening, or reset behavior
- documentation that changes intended use, warnings, or operator workflow
- traceability tooling or CI gates
