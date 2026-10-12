---
description: "Markdown documentation standards: heading structure, lists, links, tables"
applyTo: '**/*.md'
---

# Markdown Documentation Standards

## Structure

- Use a single `#` heading for the document title.
- Do not skip heading levels (`#` then `###` is wrong; use `##` between them).
- Add a table of contents when the document exceeds ~5 sections or ~150 lines.
- Keep sections focused on one topic. Split long sections into subsections.

## Formatting

- Use fenced code blocks with a language tag (`python`, `sql`, `bash`, `yaml`, `json`, `text`).
- Use concise paragraphs and bullet lists for scannable content.
- Use tables only when the information is genuinely tabular (rows share the same column structure).
- Use bold for key terms on first use. Use `code` for file paths, commands, function names, and config keys.
- Use `>` blockquotes for callouts, warnings, or notes - not for emphasis.

## Links and References

- Prefer descriptive relative links for internal documentation: `[Project standards](./project-level.instructions.md)` not `[click here](./project-level.instructions.md)`.
- Use reference-style links when the same URL appears multiple times.
- Verify links point to existing files. Broken links are worse than no links.

## File Conventions

- Use `README.md`, `CHANGELOG.md`, `AGENTS.md`, and other standard uppercase filenames where appropriate.
- Name non-standard docs in lowercase kebab-case: `deployment-guide.md`, not `DeploymentGuide.md`.
- Place images in a sibling `images/` or `assets/` directory, not inline as base64.

## Content Quality

- Lead with the most important information. Don't bury the key point after three paragraphs of context.
- Write for someone who will scan headings and bullet points first, then read details.
- Avoid repeating information that lives in another file. Link to it instead.
- Keep line length reasonable for diff readability. One sentence per line is acceptable for prose-heavy docs.
- No em dashes. Use commas, periods, semicolons, or regular dashes instead.