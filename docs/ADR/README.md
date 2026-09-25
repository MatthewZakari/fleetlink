# Architectural decision records

Use ADRs for significant architecture changes: context ownership, data/persistence strategy, identity provider, payment integration, event guarantees, service extraction, major dependencies or compatibility policy.
The initial direction is prescribed by the FL-001 brief and recorded in ARCHITECTURE.md. No additional architectural decision is represented as accepted by this directory's existence.

## Process
Create NNNN-short-title.md with the next unused sequential number. Propose before implementation, review with responsible maintainers, then mark accepted or rejected. Preserve decision history; supersede accepted records with a linked new ADR rather than silently rewriting rationale.
Reference the ADR from affected docs and pull requests. A proposed ADR does not authorize a breaking change.

## Template
- Title: ADR-NNNN — descriptive decision.
- Status: Proposed / Accepted / Rejected / Superseded.
- Date: YYYY-MM-DD.
- Owners/reviewers and related task.
- Context: problem, constraints, requirements and evidence.
- Decision: chosen approach and explicit boundaries.
- Alternatives: credible options and reasons not selected.
- Consequences: benefits, costs, risks, security/privacy and operations.
- Compatibility and data impact: migration, rollout and rollback.
- Validation: tests, measurements and acceptance criteria.
- Follow-up: unresolved questions, owner and review trigger.
- Links: related or superseding ADRs and relevant documentation.
