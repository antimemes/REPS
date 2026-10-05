# RFCs

Copy [9999-template.md](9999-template.md) to `NNNN-short-name.md` to start a proposal. Use the next available RFC number; `9999` is reserved for the template.

Current documents:

- [RFC 0001: Event basics and model API instrumentation](0001-event-basics.md) — draft.
- [RFC 0002: Schema versioning](0002-schema-versioning.md) — draft.
- [RFC 0003: Run directory and published layout](0003-run-directory-and-published-layout.md) — draft.
- [RFC 0004: Identity, provenance and pooling](0004-identity-provenance-and-pooling.md) — draft.

Keep tabled or exploratory proposals in `drafts/` without an RFC number. They retain `title` and `status: draft` frontmatter and state that they are tabled in the text. Assign a number when bringing a proposal into the active RFC sequence. Numbering does not imply acceptance.

Use a lightweight Markdown design proposal, inspired by the [Rust RFC template](https://github.com/rust-lang/rfcs/blob/master/0000-template.md) and [React RFC template](https://github.com/reactjs/rfcs/blob/main/0000-template.md). Rust separates teaching the idea from specifying it; React puts a basic example near the beginning and motivates the problem independently of the chosen design.

## Suggested structure

- YAML frontmatter and a visible title; implementation status when relevant. Link a discussion or implementation when one exists, without placeholder administrative fields.
- Short summary and motivation: the problem and the intended user benefit.
- An example that makes the idea concrete.
- Progressively introduced definitions and executable model snippets, with behavioral rules sufficient to implement them. Number chapters starting at 0.
- Tradeoffs and alternatives, including what the design deliberately cannot do.
- Adoption/implementation notes and unresolved questions, where applicable.

Scale the structure to the proposal. A small RFC can combine these sections. Use ordinary prose for requirements; distinguish proposed behavior from existing code. Keep essential definitions and constraints in the RFC itself.

Long worked examples, research motivation, and prior-art discussion can live in a linked `NNNN-name-motivation.md` addendum marked **Informative**. It explains the proposal and introduces no extra requirements. Write it for a new reader, not as a transcript of conversations or reviewer history. State rejected alternatives only where their tradeoffs explain the current design.

Use four-digit numbered filenames. Initial proposals are Draft; record their status explicitly when a project decision is made. Acceptance and implementation are separate facts. These conventions do not establish a committee or approval process.

## Frontmatter

Each RFC starts with authoritative metadata:

```yaml
---
rfc: 1
title: Event basics and model API instrumentation
status: draft
---
```

Keep a matching Markdown heading for GitHub readers, but do not repeat a status line in the body. `status` describes the proposal's lifecycle, not whether it is implemented. The template uses `rfc: 9999` and `status: template` so tooling can exclude it. Informative addenda are not separate RFCs and do not receive this frontmatter. Add metadata fields only when there is a concrete use for them.

Python's [PEP template](https://peps.python.org/pep-0012/) is another useful example of separating specification and rationale, but its reStructuredText and process metadata are unnecessary for this repository's current needs.

## Record naming

Use short, descriptive snake_case discriminator names. Related record families may use one domain prefix separated by a dot: `namespace.snake_case_name`. Prefer at most one dot; flat names are fine when a family adds no useful grouping. For example:

| Discriminator | Python model |
|---|---|
| `channel.declared` | `ChannelDeclared` |
| `channel.audience_changed` | `ChannelAudienceChanged` |
| `channel.message` | `ChannelMessage` |
| `llm.call` | `LLMCall` |

A namespace groups related records; it does not imply object nesting, permissions, or an RFC boundary. Names may describe records (`message`, `call`) or transitions (`audience_changed`). Do not imply an action was observed when the record only establishes captured state (`declared`, rather than `created`). This convention for proposals does not rename existing runtime records by itself.

Dispatch and validate using exact discriminator literals and the typed union. A shared prefix does not guarantee shared fields or behavior; consumers should not infer semantics from prefixes or verb endings. A future RFC should justify its grouping with concrete records rather than reserve a speculative hierarchy.

## Revising a proposal

Amend drafts freely to describe the current proposed design. Clarifications to an accepted RFC can be made in place when they do not change its meaning. New behavior that deserves a separate decision gets a new RFC referencing the earlier one; a replacement explicitly identifies the parts it supersedes.

Namespaces are independent of RFC boundaries. An artifact model can have its own RFC, and a later message-edit proposal can extend the channel namespace. After acceptance, link relevant amendments or superseding proposals from the original; do not silently replace an agreed design. Consolidated schema documentation should describe the current format without requiring readers to reconstruct RFC history.
