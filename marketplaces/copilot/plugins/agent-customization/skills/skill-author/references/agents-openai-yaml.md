# Artifact: agents/openai.yaml

Use this reference when a skill needs Codex app UI metadata, declarative external tool dependencies, or explicit-only invocation policy.

## Placement And Purpose

Place the file at `skills/<skill-name>/agents/openai.yaml`.
The `agents/` directory sits inside the skill directory, next to `SKILL.md`.

This file is optional and fails open.
If it is missing, malformed, or contains unknown keys, Codex logs a warning and loads the skill normally.
Do not put required model behavior here.

Use `agents/openai.yaml` only for:

- Codex app display metadata
- picker default prompt text
- declarative external tool dependencies
- `allow_implicit_invocation` runtime policy

Do not put these here:

- skill instructions, which belong in `SKILL.md`
- trigger conditions, which belong in `description` frontmatter
- this library's `metadata.short-description` compact-summary and audit field, which belongs in `SKILL.md` frontmatter
- model selection, sandbox mode, or MCP session config, which belong in a custom agent TOML file

## Full Schema

All fields are optional.
Unknown keys are ignored.

```yaml
interface:
  display_name: "string"
  short_description: "string"
  icon_small: "./assets/icon-small.svg"
  icon_large: "./assets/icon.png"
  brand_color: "#3B82F6"
  default_prompt: "string"

dependencies:
  tools:
    - type: "mcp"
      value: "server-name"
      description: "Human-readable dependency label"
      transport: "streamable_http"
      command: "command for local MCP servers"
      url: "https://example.com/mcp"

policy:
  allow_implicit_invocation: true
  products: []
```

## Interface Fields

`interface.display_name` is the human-readable label shown in the Codex app skill picker.
If omitted, Codex uses the skill `name` from `SKILL.md` frontmatter.
Use title case and keep it under 64 characters.

`interface.short_description` is a cosmetic one-liner under the picker label.
It is not the same as `metadata.short-description` in `SKILL.md` frontmatter.
Write it as a noun phrase describing what the skill does.

`interface.icon_small` and `interface.icon_large` are paths resolved relative to the skill root, not the `agents/` directory.
Use paths such as `./assets/github-small.svg` and `./assets/github.png`.
If the skill is part of a plugin, paths may also resolve relative to the plugin root.
If the icon file does not exist at load time, Codex drops the field silently.

`interface.brand_color` is a UI theming color.
Use a standard 6-digit hex string such as `"#3B82F6"`.
The parser does not currently validate the format.

`interface.default_prompt` is pre-filled when a user selects the skill from the picker.
Keep it under 1024 characters.
Write a complete instruction the user can run immediately without filling blanks.

Good:

```yaml
default_prompt: "Inspect failing GitHub Actions checks in this repo, summarize root cause, and propose a focused fix plan."
```

Bad:

```yaml
default_prompt: "Use this skill to fix [ISSUE]."
```

## Dependencies

`dependencies.tools` declares external tools the skill needs.
This is declarative metadata for UI display and dependency checking.
It does not install or configure tools.

Each tool object may include:

- `type`: tool category, usually `"mcp"` in OpenAI catalog entries
- `value`: MCP server name as it appears in Codex config
- `description`: human-readable dependency label
- `transport`: transport protocol, such as `"streamable_http"`
- `url`: endpoint for HTTP-based MCP servers
- `command`: command for locally launched MCP servers

Example:

```yaml
dependencies:
  tools:
    - type: "mcp"
      value: "linear"
      description: "Linear MCP server"
      transport: "streamable_http"
      url: "https://mcp.linear.app/mcp"
```

## Policy

`policy.allow_implicit_invocation` defaults to `true`.
Set it to `false` only for skills that should activate only when the user explicitly names the skill.
Use explicit-only policy for powerful, destructive, or highly context-sensitive skills.

```yaml
policy:
  allow_implicit_invocation: false
```

`policy.products` is parsed and stored but not enforced yet.
Leave it out unless a future runtime contract requires it.

## Examples

Skill with UI metadata and icons:

```yaml
interface:
  display_name: "GitHub Fix CI"
  short_description: "Debug failing GitHub Actions CI"
  icon_small: "./assets/github-small.svg"
  icon_large: "./assets/github.png"
  default_prompt: "Inspect failing GitHub Actions checks in this repo, summarize root cause, and propose a focused fix plan."
```

Skill with an MCP dependency:

```yaml
interface:
  display_name: "Linear"
  short_description: "Manage Linear issues in Codex"
  icon_small: "./assets/linear-small.svg"
  icon_large: "./assets/linear.png"
  default_prompt: "Use Linear context to triage or update relevant issues for this task, with clear next actions."
dependencies:
  tools:
    - type: "mcp"
      value: "linear"
      description: "Linear MCP server"
      transport: "streamable_http"
      url: "https://mcp.linear.app/mcp"
```

Explicit-only skill without icons:

```yaml
interface:
  display_name: "Security Threat Model"
  short_description: "Repo-grounded threat modeling and abuse-path analysis"
  default_prompt: "Create a repository-grounded threat model for this codebase with prioritized abuse paths and mitigations."
policy:
  allow_implicit_invocation: false
```
