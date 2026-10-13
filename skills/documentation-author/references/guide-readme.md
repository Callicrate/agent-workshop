# README Workflow

Use this guide only for `README.md`.

## Workflow

1. Read the repo entry points, package or build config, and any existing docs before writing.
2. Keep only sections supported by the codebase. Omit anything you cannot verify.
3. Default section order:
	- `# <project name>`
	- one-sentence summary
	- `## Overview`
	- `## Project Structure` when the layout is not obvious
	- `## Getting Started` with verified setup and first-run commands
	- `## Usage` with the common commands users actually need
	- `## Documentation` with links to existing docs
	- optional `## Contributing` and `## License` only when the repo already supports them
4. Link to `docs/architecture.md` only if that file already exists or you are creating it in the same task.
5. Preserve the repo's existing README voice and section order when updating instead of rewriting around this template.

## Rules

- Verify every command, path, environment variable, and file reference against the repo.
- Prefer short prose plus bullets over large narrative sections.
- Describe what the project does, how to set it up, and how to run the common workflows. Skip marketing copy.
- Do not leave placeholders, TODO markers, or speculative sections.

## Package README Pattern

For Python or JavaScript libraries, lead with the user workflow before internals:

1. State the workflow pain the package removes.
2. Show the smallest useful install and usage path.
3. Add common sync or async examples that match the public API.
4. Move large-result handling, pickle/codec behavior, wire formats, or other internals after the basic workflow.
5. Verify examples from an installed or editable package when packaging is part of the task.
