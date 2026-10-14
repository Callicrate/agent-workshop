# Trace Span Hierarchy

Use this reference when auditing an MLflow trace whose spans do not reflect real execution structure.

## Symptom: Flattened Tree

A trace can look correct at a glance while every span is a direct child of the root. This is a gap when the tree omits a real containment or spawning relationship.

Audit signal: work known to execute inside an active span, or work known to be spawned by it, appears as an unrelated sibling. Sequential stages whose prior stage has ended may legitimately be siblings under a workflow root.

## Correct Shape

A trace should encode actual execution relationships:

- **Same-thread containment nests automatically.** A span opened while its parent context remains active is a child of that active parent.
- **Spawned work nests under its spawner.** Parallel tasks such as `generate_<X>` tasks are children of the span that actually spawns them, and siblings of each other.
- **Sequential work can remain sibling work.** A predecessor that has ended before a later stage begins is not automatically that stage's parent.

## Fix: Use the Supported Parent APIs

```python
import mlflow


# Same-thread containment uses the active context. The nested span becomes a child
# without passing an ID or a parent argument.
with mlflow.start_span(name="<workflow>"):
    with mlflow.start_span(name="<contained-step>"):
        ...

    # This stage starts after <contained-step> has ended, so it can be a sibling.
    with mlflow.start_span(name="<later-stage>"):
        ...


def run_off_context(item: str, spawning_span) -> None:
    # An in-process worker/callback without the active context receives the live parent object.
    child_span = mlflow.start_span_no_context(
        name=f"generate_{item}",
        parent_span=spawning_span,
    )
    try:
        ...
    finally:
        child_span.end()


with mlflow.start_span(name="<workflow>"):
    with mlflow.start_span(name="<spawn-work>") as spawning_span:
        # Wait for every worker/callback to finish before this context exits.
        run_workers(lambda item: run_off_context(item, spawning_span))
```

`mlflow.start_span` manages same-thread lifecycle and parentage through its active context. Across an off-context in-process thread or callback, that context does not propagate automatically. Use `mlflow.start_span_no_context(..., parent_span=<LiveSpan>)` when the live parent object can be passed in process, and end the returned child in `finally`. Keep every ancestor span active until its children finish.

Do not serialize a `LiveSpan` across a process or remote boundary. Use the selected MLflow version's documented context-propagation API for that boundary; do not invent an ID-based parent argument. A span from `start_span_no_context` is not the current global context, so automatically instrumented nested calls do not become its children merely because the detached span exists.

## Audit Checklist

- For each claimed containment relationship, is the child created while its same-thread parent context is active?
- Do spawned or parallel tasks appear as children of the span that actually spawned them?
- Are sequential stages treated as siblings when no parent remains active or no spawn relationship exists?
- For off-context in-process work, is a live parent span passed to `start_span_no_context`, and is the child ended in `finally` before its ancestors finish?
- For process or remote boundaries, does the selected MLflow version use its documented context-propagation API instead of serializing a live span or deriving parentage from an ID?
- Does detached-span code avoid assuming that automatically instrumented grandchildren inherit a global active context?
