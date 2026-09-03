# UI Patterns

## Assets

- Use real, generated, or domain-relevant bitmap images when the user needs to inspect a product, place, person, object, state, or gameplay.
- Avoid purely atmospheric assets when the actual subject matters.
- Use generated images for raster assets when they are faster and more appropriate than hand-built vectors.
- Use code-native SVG or canvas only when the UI element is inherently vector, interactive, or part of a game/system visualization.

## Layout

- Do not put cards inside cards.
- Do not style ordinary page sections as floating cards.
- Keep cards to repeated items, modals, and genuinely framed tools.
- Avoid decorative gradient blobs, orbs, and bokeh backgrounds.
- Ensure first-viewport brand, product, place, or object identity is visible on branded pages.

## Controls

- Use icon buttons for common tools when a familiar icon exists, with tooltips for unfamiliar actions.
- Use text buttons for clear commands.
- Use tabs for views, segmented controls for modes, toggles or checkboxes for binary settings, and menus for option sets.
- Keep controls complete enough that a target user can perform the expected workflow without hidden assumptions.

## Palette

- Avoid one-note palettes dominated by a single hue family.
- Limit heavy purple, purple-blue gradient, beige, tan, brown, espresso, dark blue, and slate themes unless the brand or existing design system requires them.
- Check actual CSS colors before finishing when the page visually collapses into one tone.
