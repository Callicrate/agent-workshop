# Agent Workshop

**Agent Skills I use in real work, packaged as installable plugins for Claude
Code, Codex, and Copilot.**

## How I build these skills

Each skill starts with work I've already done. I keep only patterns that repeat
and prove useful. Then I tighten the workflow and automate what I can. One-off work stays one-off. I don't want a skill for every possible task. I want
a small set of workflows that I know are worth using again.

Skills are grouped into plugins by category, and reachable via marketplace definitions for Claude, Copilot, and Codex. Feel free to install whatever looks useful and ignore the rest.

## Plugins

| Plugin | What it helps with |
| --- | --- |
| `agent-customization` | Creating prompts or writing skills |
| `databricks` | IaC with DABs, machine learning, model training & serving, batch inference, Spark ETL, runtime diagnostics, and sane MLFlow patterns |
| `frontend-product-ui` | Product-focused screens, dashboards, forms, and browser workflows |
| `python-local-dev` | Python development and debugging on a local machine |
| `repository-docs` | Authoring READMEs, technical documentation, API specs, and AI-facing `AGENTS.md` |
| `review-and-learning` | Analyzing text, code, and AI sessions specifically for ways to improve |

## Install

Add the marketplace once, then install any plugin from the list above. Replace
`databricks` with the plugin you want.

```bash
# Claude Code
claude plugin marketplace add https://github.com/Callicrate/agent-workshop
claude plugin install databricks@callicrate

# Codex
codex plugin marketplace add https://github.com/Callicrate/agent-workshop
codex plugin add databricks@callicrate

# Copilot
copilot plugin marketplace add https://github.com/Callicrate/agent-workshop
copilot plugin install databricks@callicrate
```

> Start a new session after changing plugins so the client reloads its skills.

---

Apache License 2.0. See the [license](LICENSE) and [notice](NOTICE).

Copyright 2026 Joel Callicrate.
