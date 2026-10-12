# Install Runbook Workflow

Use this guide for WSL, local, Docker, workstation, and environment-specific install docs.

## Required Sections

```markdown
# Install: <target>

## Target Environment
## Prerequisites
## Install Commands
## First Run
## Verification
## Expected Output
## Troubleshooting
## Unsupported Or Out Of Scope
```

## Rules

- Name the target shell, distro, OS, container, or runtime explicitly.
- For WSL docs, name the distro and whether commands run from Windows PowerShell or inside WSL.
- Include exact commands with working directory and environment assumptions.
- Include a first-run command and a verification command.
- State expected success output or markers.
- List common failures and recovery steps backed by source or observed output.
- Include cleanup or uninstall only when verified or already documented by the project.
- State what is intentionally unsupported, such as Windows native, Docker-only, or WSL-only paths.

## Avoid

- generic execution-policy advice without a concrete `pwsh -File` or shell-specific invocation
- commands that assume global Python, Node, Docker, or WSL without a prerequisite check
- placeholders such as `<your path>` unless the project docs already use a template