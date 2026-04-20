# AI Feature Template

Use this template before handing a feature to Codex or Claude Code.

## Title

One-sentence feature name.

## Problem

What user or operator pain does this solve?

## User Value

Why does this matter to the product, buyer, or operator?

## Scope

What is included in this slice?

## Non-Goals

What is explicitly not included?

## Acceptance Criteria

- Criterion 1
- Criterion 2
- Criterion 3

## User Experience

Describe exactly how the feature should behave from the user's perspective.

Include:

- entry point
- main flow
- success state
- failure state
- edge cases

## Architecture Fit

Which existing architectural pieces support this feature?

- server modules/services
- camera modules/services
- persistence/data model
- frontend/templates/static code
- Yocto/build/deployment impact

## Technical Approach

Describe the preferred implementation shape.

Include:

- API changes
- model/storage changes
- background jobs or service changes
- frontend behavior
- migration notes

## Affected Areas

List likely files or modules to touch.

## Security / Privacy Considerations

What trust, auth, data-retention, or privacy issues need to be respected?

## Testing Requirements

Include:

- unit tests
- integration tests
- end-to-end/UI tests
- manual verification notes if needed

## Documentation Updates

Which docs must be updated if this ships?

## Rollout Notes

How should the feature be introduced?

Examples:

- off by default first
- hidden behind a flag
- phased release
- migration required

## Open Questions

List unresolved decisions, if any.

## Implementation Guardrails

Use these defaults unless the feature explicitly requires otherwise:

- preserve the modular monolith architecture
- preserve the server/camera responsibility split
- do not add new long-lived daemons unless clearly justified
- keep the product local-first by default
- do not weaken auth, OTA, or device trust boundaries
- update tests and docs together with code
