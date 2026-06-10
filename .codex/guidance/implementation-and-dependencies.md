# Implementation And Dependencies

Source reference: `.claude/rules/implementation-and-dependencies.md`.

- Implement against frozen contracts only, primarily `warehouse_interfaces` and
  shared topic/design contracts.
- Do not import internals from another track package.
- Record public produce/consume surfaces while implementing:
  topics, files, schemas, stores, and assumptions.
- New shared dependencies are contract changes. Use a `contract` PR, dependent
  track notice, and additive-first compatibility.
- If a dependency cannot wait, keep the local track independent with a fake or
  stub and replace it after the contract lands.
- Temporary hand-offs to another track's canonical path need owner notice and a
  TODO marker with the receiving issue.
