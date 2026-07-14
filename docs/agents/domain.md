# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- `CONTEXT-MAP.md` at the repo root. It points at one `CONTEXT.md` per context.
- `docs/adr/` for architecture decisions that touch the area about to be changed.
- The relevant context-specific `CONTEXT.md` files listed in `CONTEXT-MAP.md`.

If a referenced file does not exist, proceed silently. Do not suggest creating it upfront.

## Layout

This repo uses a multi-context domain-doc layout:

```text
/
|-- CONTEXT-MAP.md
|-- docs/adr/
|-- backend/agent/CONTEXT.md
`-- backend/agent/planning/CONTEXT.md
```

## Use project vocabulary

When output names a domain concept, issue title, refactor proposal, hypothesis, or test name, use the vocabulary in the relevant `CONTEXT.md` and ADRs.

If the needed concept is not in the glossary yet, note it as a domain-modeling gap instead of inventing a new term casually.

## Flag ADR conflicts

If output contradicts an existing ADR, surface it explicitly rather than silently overriding it.
