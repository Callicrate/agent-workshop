# Diagram Workflow

Diagrams are opt-in. Use this guide only when the user explicitly asks for diagrams or the project already maintains diagram documents.

Default filenames for new docs are `docs/dataflow-diagram.md` and `docs/functional-diagram.md`.

## Workflow

1. Read the architecture docs and the actual execution flow before drawing anything.
2. Create only the diagram file or files that are explicitly in scope.
3. Prefer one end-to-end diagram plus short explanatory bullets. Add subsystem diagrams only when one diagram cannot stay readable.
4. Follow [ascii-art-standards.md](ascii-art-standards.md). Keep diagrams within 80 characters and use real component names.
5. Label arrows with the actual flow, protocol, or transition being shown.
6. Pair each diagram with a few bullets explaining the main boundaries, transformations, or decision points.

## Avoid

- creating diagram files by default
- decorative boxes that do not communicate structure
- duplicating architecture prose inside the diagram doc
- speculative flows that are not backed by the codebase
