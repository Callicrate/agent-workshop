# Product UI Workflow

Use this when implementing, revising, reviewing, or operating a frontend screen or app.

## Choose The Work Mode

Classify the UI task before optimizing it:

- Build UI: create the working product surface and expected controls.
- Revise UI: preserve existing contracts while improving workflow, layout, state, or polish.
- Review UI: verify rendered behavior, defects, and completion evidence.
- Operate existing UI: use the UI as the execution substrate and prioritize access, state capture, reproducibility, and reliable interaction before visual polish.

For operate-existing mode, the finish criteria are verified access, captured state, documented reproduction, and a tested main action path. Palette, component hierarchy, and decorative polish are secondary unless the user specifically asks to redesign the interface.

## Start With The Product Task

Identify:

- Primary user and frequency of use
- First task the screen must support
- Data the user needs to compare, edit, or act on
- Navigation path into and out of the screen
- Required controls, states, and feedback
- Whether the UI is being built/revised or operated as an existing product surface
- Auth/session owner when access is browser-bound
- Evidence needed to prove the main workflow actually worked

Operational tools should be dense, calm, and scannable. Games and expressive interactive pieces can be more animated and playful. Marketing pages need strong first-viewport identity, but app requests should start with the working app.

Operational dashboards, lab consoles, admin pages, monitoring surfaces, and status tools should emphasize dense current state, timestamps, source confidence, filters, action affordances, and handoff-ready reproduction. Avoid marketing-style hero treatment for these surfaces.

## Implementation Rules

- Match the existing framework, component library, icon set, routing, state, and CSS conventions.
- Prefer complete workflows over decorative sections.
- Preserve public contracts used by existing scripts, loaders, game loops, global variables, route params, and test hooks unless the task explicitly changes them.
- Use stable dimensions for boards, grids, toolbars, counters, tiles, and controls so hover and dynamic text do not shift layout.
- Use familiar controls: icons for tools, tabs for views, toggles for binary settings, segmented controls for modes, sliders or inputs for numbers, and menus for option sets.
- Keep page sections unframed unless the design system already uses cards for that purpose. Use cards for repeated items, modals, and genuinely framed tools.

## Operate Existing UI

When the UI is the medium for doing work rather than the thing being built:

- Verify entrypoint, session/auth source, and current state before acting.
- Treat screenshots, terminal output, log snippets, and status docs as operational evidence.
- Capture what action was tested, when it was tested, and what result was observed.
- Preserve discovered workarounds in the local runbook or access map when the interface differs from the usual path.
- When other agents own related docs or status files, keep a trusted-source ledger and update only the file you are responsible for unless the user asks otherwise.

Use [ui-access-map.md](ui-access-map.md) when the useful deliverable is an interface inventory.

## Copy And Text Fit

- Do not use in-app instructional prose to explain obvious controls.
- Keep headings sized for their container.
- Do not scale font size with viewport width.
- Verify long words, button labels, and compact panels on mobile and desktop.
