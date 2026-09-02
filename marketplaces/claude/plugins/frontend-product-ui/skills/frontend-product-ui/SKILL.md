---
name: frontend-product-ui
description: "Use when building frontend screens, dashboards, forms, games, browser workflows, or UI maps; guides product UI. Do not trigger for backend-only work, docs, icon edits, or headless automation."
metadata:
  short-description: Build product UI screens.
---

# Frontend Product UI


## When to Use

- Building or revising a frontend app, dashboard, tool, game, form, or product screen
- Turning a vague UI request into a working first screen with complete controls and states
- Reviewing visual quality, responsive behavior, interaction flow, or text fit after implementation
- Choosing between app-like, operational, editorial, game, or marketing presentation patterns
- Operating an existing browser-hosted interface, authenticated app, lab console, remote desktop, HMI, admin page, or monitoring dashboard
- Creating or updating a UI access map that records what views, controls, sessions, and actions are available

## When NOT to Use

- Backend-only work with no user interface
- Pure copyediting or documentation
- SVG/icon-only edits where an existing design system owns the visual language
- Headless automation where no UI session, visual state, browser surface, or interaction contract matters

## Workflow

1. Read [references/product-ui-workflow.md](references/product-ui-workflow.md) to identify the audience, primary workflow, information density, and expected interaction model.
2. Classify the task as building UI, revising UI, reviewing UI, or operating an existing UI. If operating an existing UI, prioritize session access, state capture, reproducibility, and interaction reliability before visual polish.
3. For browser-mediated tasks, load [references/browser-session-patterns.md](references/browser-session-patterns.md), identify the browser/profile/tool that owns authentication, verify auth against the target page, and avoid creating a new profile when the user depends on an existing session.
4. For browser-hosted terminals, remote desktops, HMI consoles, or canvas UIs, load [references/hosted-terminal-ui.md](references/hosted-terminal-ui.md), verify focus, input method, command latency, screenshot readability, and output capture before relying on the interface.
5. Follow the existing app framework, component library, routing, state management, and styling conventions before adding new patterns.
6. Build the actual usable experience as the first screen unless the user explicitly asked for a landing page.
7. Add expected controls, empty/loading/error states, keyboard or pointer interactions, and responsive constraints for fixed-format UI elements.
8. For live, closed, polling, spinner, workspace-inventory, operational-dashboard, log-monitor, status-file, cross-agent status, or stale transport states, load [references/state-transition-patterns.md](references/state-transition-patterns.md) before implementing, operating, or reviewing the UI.
9. When the deliverable is an interface inventory or durable handoff for an existing UI, use [references/ui-access-map.md](references/ui-access-map.md) to capture entrypoints, auth/session source, visible views, controls, read/write actions, proof artifacts, and last verification time.
10. Run or start the app when needed. Use [references/visual-verification.md](references/visual-verification.md) for screenshot and layout checks when visual behavior, layout, copy placement, interaction, responsive behavior, or product flow changed; for purely internal refactors with no visual surface, targeted tests may be sufficient.
11. Fix text overflow, incoherent overlap, blank media/canvas states, broken controls, one-note palettes, unverifiable auth/session claims, and missing operational evidence before reporting completion.

## Deterministic Tools

| Resource | Use When | Outcome |
|----------|----------|---------|
| [references/product-ui-workflow.md](references/product-ui-workflow.md) | You need to choose layout, workflow, controls, and visual tone | UI implementation plan and interaction checklist |
| [references/visual-verification.md](references/visual-verification.md) | You need to verify rendered UI quality | Screenshot, responsiveness, and state checks |
| [references/ui-patterns.md](references/ui-patterns.md) | You need product-specific UI pattern rules | Control, layout, asset, and palette guardrails |
| [references/browser-session-patterns.md](references/browser-session-patterns.md) | UI work depends on existing browser auth, SSO, cookies, extensions, or DevTools profile boundaries | Verified browser/session contract and safe auth handling |
| [references/hosted-terminal-ui.md](references/hosted-terminal-ui.md) | The UI is a hosted terminal, remote desktop, HMI, canvas console, or lab interface | Reliable focus, input, screenshot, and command-output workflow |
| [references/ui-access-map.md](references/ui-access-map.md) | The deliverable is an existing-interface inventory or cross-agent handoff | Reproducible access map with views, controls, evidence, and caveats |

## References

- [references/product-ui-workflow.md](references/product-ui-workflow.md) - workflow-first UI design method
- [references/visual-verification.md](references/visual-verification.md) - rendered UI QA rules
- [references/ui-patterns.md](references/ui-patterns.md) - layout, controls, assets, and palette rules
- [references/state-transition-patterns.md](references/state-transition-patterns.md) - live, closed, polling, spinner, and transition-state guidance
- [references/browser-session-patterns.md](references/browser-session-patterns.md) - authenticated browser session and profile-boundary rules
- [references/hosted-terminal-ui.md](references/hosted-terminal-ui.md) - hosted terminal, remote desktop, and canvas UI operating rules
- [references/ui-access-map.md](references/ui-access-map.md) - durable interface inventory and access-map template
