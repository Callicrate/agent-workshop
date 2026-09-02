# Visual Verification

Use this before finishing frontend work.

## Required Checks

- Start the dev server when the app needs one. If the port is occupied, use a nearby available port and report the URL.
- Capture or inspect desktop and mobile viewports.
- Check that primary content is not blank, hidden, clipped, overlapped, or below an unintended fold.
- Exercise the main controls, state transitions, and navigation paths.
- Verify the named user workflow in the running app, not only by reading components or checking a static screenshot.
- Check loading, empty, and error states when the implementation includes data fetching or async work.
- Check browser console errors and network failures when the workflow depends on scripts, loaders, API calls, or generated assets.
- Verify images, generated assets, icons, and canvases render as intended.
- For operated existing UIs, verify the main workflow and durable evidence, not only that a page is visible.
- When browser console or network checks are unavailable, capture equivalent UI evidence such as screenshot plus command output, status text, log snippet, or timestamped state.

## Three.js And Canvas

- Ensure the scene is nonblank, correctly framed, and visibly interactive or moving when expected.
- Check canvas pixels or screenshots rather than assuming initialization worked.
- Keep primary 3D scenes full-bleed or unframed unless the app design requires a tool panel.

## Hosted Terminal And Canvas UI

- Confirm focus is on the terminal, remote desktop window, or canvas before typing.
- Capture before/after screenshots for state-changing actions.
- Check that terminal or canvas text is readable at the captured resolution.
- Use OCR, image crop inspection, or pixel checks when the UI exposes no structured text.
- Prefer short commands with `START`/`END` markers, exit-code output, bounded width, and log files for large output.
- Test paste, typing latency, and timeout behavior before sending long payloads.

## Responsive QA

- Test at least one narrow mobile viewport and one desktop viewport.
- Confirm text does not overflow controls or overlap adjacent content.
- Confirm fixed-format elements keep stable aspect ratios and dimensions.
- Confirm menus, tabs, and panels remain reachable without horizontal scrolling unless the product intentionally uses it.

## Finish Criteria

Do not report completion while obvious visual defects remain: text overflow, incoherent overlap, broken controls, blank media, missing states, or a palette that reads as one flat hue family.

For operated existing UIs, do not report completion until the target workflow has been exercised, the session/access boundary is understood, and durable evidence exists. A nonblank screenshot alone is not enough unless the user only asked for a visual snapshot.
