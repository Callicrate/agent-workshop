# Architecture Note Workflow

Target file: `docs/architecture.md` unless the repo already uses another location.

Use this guide for system structure, component boundaries, execution flow, and integrations.

## Workflow

1. Read the existing docs, entry points, config, and representative modules before writing.
2. Record only verified structure:
  - what the system does and where it runs
  - major components and their responsibilities
  - the main execution or data flow between components
  - external systems and storage boundaries
  - configuration or deployment boundaries that materially change behavior
3. Use only the sections the project needs. A common order is Summary, Component Map, Execution Flow, External Integrations, and Configuration Boundaries.
4. Use diagrams only when the project already maintains them or the user explicitly asked. Otherwise prefer short prose and tables.
5. Keep component names, paths, and runtime terms identical to the codebase.

## External SDK Or Platform Guidance

- When the document gives SDK, API, or architecture guidance for a named external system, verify the guidance against that system's canonical source before writing.
- Prefer official docs, official repositories, published API references, or the installed package source. Cite the canonical source in the document.
- Separate verified requirements from project-local conventions.
- Do not present remembered SDK behavior as authoritative when the external system may have changed.

## Suite Or MCP Spec Boundaries

- For suite-level MCP or platform specs, read the suite contract first, then only the sibling specs needed to define the boundary of the current component.
- Document scope, non-goals, ownership, handoffs, transport boundary, and configuration boundary before adding implementation detail.
- Check for boundary leakage: requirements copied from a sibling component, stale server names, mismatched transport assumptions, or headings that imply unsupported ownership.
- Verify table of contents, heading hierarchy, paths, and stale markers after rewriting a spec.

## Avoid

- exhaustive function catalogs
- field-by-field schema tables unless they matter architecturally
- speculative future-state design
- diagram files created just because they are possible
