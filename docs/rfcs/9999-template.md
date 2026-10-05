---
rfc: 9999
title: Template
status: template
---

# RFC 9999: Template

<!-- Copy this file to NNNN-short-name.md using the next available RFC number.
9999 is reserved for this template. Set rfc to the new number, status to draft,
and update both the frontmatter title and Markdown heading. Replace the guidance with the actual
proposal. Remove unused sections and these comments. Add discussion or implementation
links only when they exist. Keep sections numbered from 0. -->

One paragraph explaining the proposed change and its intended benefit.

## 0. Motivation

What problem do researchers, experiment authors, or other users encounter? Describe the desired outcome independently of the proposed solution. State the scope and the important things this proposal does not attempt to solve.

## 1. Basic example

Show one small, concrete example of the proposed behavior. Explain what the reader can do or understand with it. Distinguish proposed behavior from current behavior.

## 2. Design

Introduce concepts in dependency order, building on the example. Define names, relationships, and behavior precisely enough to implement the proposal. Follow the [record naming conventions](README.md#record-naming) for discriminators.

For data models, include Pydantic snippets that execute in order and define the meaning of fields alongside them. Explain relevant ordering, identity, unknown values, and validation rules that the types alone cannot express.

<!-- Split this into successive numbered chapters when that makes the models
easier to learn. Keep essential requirements here, not in an addendum. -->

## 3. Tradeoffs and alternatives

Why this design? What does it make harder or leave unsupported? Describe the strongest simpler alternative and what happens if we make no change.

## 4. Implementation and adoption

Identify the first concrete integration and how to verify the intended behavior. Describe relevant effects on existing producers, readers, or stored data. Distinguish acceptance of the proposal from implementation status.

## 5. Open questions

List decisions still needed, distinguishing blockers from future extensions. Remove this section if there are no open questions.

<!-- Optional: link NNNN-short-name-motivation.md for longer worked examples,
research motivation, or prior art. Mark it Informative: it explains the proposal
and adds no requirements. Cite sources near the claims they support. -->
