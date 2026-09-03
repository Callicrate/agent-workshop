# Hosted Terminal And Canvas UI

Use this reference when the product surface is a browser-hosted terminal, remote desktop, Guacamole-like session, HMI text console, lab console, canvas terminal, or other UI-mediated execution surface.

## Preflight

Before sending meaningful input:

- verify the correct browser tab or popout window is active
- click or focus the terminal/desktop/canvas target
- send a harmless short command or keystroke when appropriate
- confirm typing latency and paste behavior
- confirm screenshot readability at the current zoom and resolution
- identify how to recover focus if the desktop or terminal loses it

Do not rely on long pasted commands until a short input test succeeds.

## Command Strategy

Hosted terminal output is often hard to parse. Prefer compact, self-delimiting commands:

```text
printf 'START marker\n'; <short command>; printf '\nEXIT=%s\nEND marker\n' "$?"
```

Use these habits:

- keep commands short enough for the terminal paste/input limits
- print `START` and `END` markers around important output
- print exit codes
- cap output width or row counts
- write large output to files and show a small summary
- use helper scripts or uploaded files when the command would be too long to type safely
- screenshot after state-changing actions

## Screenshots And Text Extraction

Screenshots are operational evidence, not passive decoration.

- Name screenshots by state or action when saving them.
- Check that relevant text is readable before relying on the screenshot.
- Crop to the terminal or control region when the full desktop is too noisy.
- Use OCR, pixel checks, or manual crop inspection when structured text extraction is unavailable.
- Pair screenshots with command snippets, timestamps, or logs when possible.

## Focus Loss And Timeouts

Remote desktops and canvas terminals can lose focus or stop updating.

- Re-check focus after taskbar clicks, popouts, modals, or browser tab switches.
- Watch for textarea reads, CDP calls, or screenshot tools timing out.
- Keep long-running work visible with a progress marker or periodic bounded output.
- Treat a stale screen as unverified until a small command or UI action proves it is live.

## Runbook Updates

When you discover an interface workaround that future workers will need, update the local guide or access map with:

- prerequisites
- launch path
- focus method
- input method
- verification command or visual check
- common failure modes
- screenshots or artifact paths when useful

The goal is that another worker can reproduce the same hosted UI workflow without rediscovering the interaction recipe.