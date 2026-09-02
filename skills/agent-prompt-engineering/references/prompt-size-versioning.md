# Prompt Size And Versioning

Use this when a prompt is growing, must work across model families, or needs durable iteration history.

## Length Budget

- Keep the system prompt focused on rules the model must apply on every turn.
- Move long examples, domain catalogs, version history, and rare fallback procedures into linked references.
- Treat the audit script's long-prompt warning as a review trigger, not an automatic failure.
- When adding a rule, remove or merge any older rule that now says the same thing.

## What To Cut First

- Generic identity text that would fit any agent.
- Repeated confirmation gates already stated in Security.
- Examples that do not exercise a real failure mode.
- Implementation details that belong inside a backing tool or wrapper.

## Model Targets

- Name the intended model family or runtime when testing prompt behavior.
- If a prompt must work across models, run at least one normal case and one blocker case on each target.
- Do not assume another model will honor tone, ordering, or implicit hierarchy the same way.
- Prefer observable acceptance checks over model-specific phrasing tricks.

## Version Records

- Store prompt changes in git when the prompt lives in a repository.
- For each iteration pass, record the before version, after version, evidence artifact, and model target.
- Keep evidence close to the prompt change: PR description, commit notes, or a dedicated evidence file.
- If multiple prompt versions remain active, document which caller or model target uses each version.

## Review Questions

- Can the prompt fit in the intended context with its required runtime context?
- Which reference could absorb detail that is not needed every turn?
- Which model target has actually been tested?
- Can a future maintainer connect the change to its evidence artifact?