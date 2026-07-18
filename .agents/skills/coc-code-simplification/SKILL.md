---
name: coc-code-simplification
description: Simplifies only understood, scoped project code while preserving observable behavior, public contracts, data models, business rules, error behavior, side effects, ordering, and project conventions.
---

# CoC code simplification

## Purpose

Remove unnecessary complexity from understood code so it is easier to read and maintain while all observable behavior and project invariants remain unchanged.

## When to use

Use after behavior is correct and protected, when scoped code has demonstrable duplication, nesting, misleading names, dead structure or an abstraction that adds no current value.

## When not to use

Do not use while the code is not understood, as part of a feature or bug fix, for cosmetic churn, to rewrite an area, or where a simpler form would weaken correctness, diagnostics, security, performance or testability.

## Required project context

Read applicable `AGENTS.md`, relevant docs, tests and callers before editing. Apply Chesterton's Fence concretely: identify the responsibility, callers, edge cases, error paths and likely reason for the existing structure before removing it.

If this skill conflicts with `AGENTS.md`, follow `AGENTS.md`.

## Workflow

1. State the exact scope and why simplification is needed.
2. Enumerate invariants: inputs, outputs, public contracts, errors, side effects, ordering, data model and business rules.
3. Confirm project conventions from neighboring current code.
4. Prefer deletion of redundant complexity over moving it behind a new layer.
5. Prefer clear names and explicit control flow over clever compression.
6. Do not create a generic framework or new abstraction without repeated current need.
7. Do not add a dependency to save a few lines.
8. Make one minimal conceptual change at a time and keep feature changes separate.
9. Verify all invariants with authorized checks; existing tests must not be weakened to accept changed behavior.
10. Revert the proposal conceptually if the result is harder to understand or review.

## Safety boundaries

Do not change public contracts, data models, metrics, clan rules, privacy policy or security boundaries. Do not run commands, tests, formatters, Git operations or edit files without explicit authorization. Avoid drive-by cleanup and unrelated files.

## Verification checklist

- The original purpose and constraints are understood.
- Preserved invariants are listed explicitly.
- Observable behavior, errors, side effects and ordering are unchanged.
- The diff is minimal and follows current conventions.
- No feature, dependency, speculative abstraction or unrelated cleanup is included.
- Regression verification was performed only as authorized; gaps are reported.

## Expected output format

Return: scope; reason; preserved invariants; before/after complexity explanation; minimal proposed diff; authorized verification; checks not performed; residual regression risk.

## Attribution and license reference

Project-local adaptation based on selected upstream guidance. See `THIRD_PARTY_NOTICES.md` for the Addy Osmani source, pinned revision and MIT notice. This is not an official upstream skill and the upstream author does not endorse this project.
