# Trace Span Hierarchy

Use this reference when auditing an MLflow trace whose spans do not reflect real execution structure.

## Symptom: Flattened Tree

A trace looks correct at a glance but every span is a direct child of the root. This happens when spans are created without an explicit parent, so MLflow attaches them all under the root span instead of nesting them by causal or spawn relationship.

Audit signal: one root span with all other spans as siblings under it, regardless of which step actually produced or spawned them.

## Correct Shape

A trace should encode two relationships explicitly:

- **Predecessors nest as ancestors.** A step that must complete before the next step runs is the parent of that next step, not its sibling.
- **Parallel work nests as a fan-out.** Tasks spawned by one step (for example `generate_<X>` tasks) are children of the step that spawns them, siblings of each other.

## Fix: Set Explicit Parent Spans

```python
# WRONG - every span opens against the active/root span, producing a flat tree
with mlflow.start_span(name="<step>") as step_span:
    ...
# fan-out tasks each open a fresh root-level span
for item in items:
    with mlflow.start_span(name=f"generate_{item}"):
        ...

# CORRECT - thread parent span ids so predecessors nest and fan-out nests
with mlflow.start_span(name="<predecessor>") as pred_span:
    ...
    with mlflow.start_span(name="<step>", parent_id=pred_span.span_id) as step_span:
        # parallel work fans out UNDER the spawning step
        for item in items:
            with mlflow.start_span(
                name=f"generate_{item}",
                parent_id=step_span.span_id,
            ):
                ...
```

When spans are created across threads, processes, or callbacks, the active span does not propagate automatically. Capture the spawning span's id and pass it as `parent_id` (or reattach the trace context) at each child.

## Audit Checklist

- Does the tree depth exceed one level below the root, or is it flat?
- Do predecessor steps appear as ancestors of the steps that depend on them?
- Do spawned/parallel tasks appear as children of the step that spawns them?
- Is a `parent_id` (or reattached context) set for every span created off the main call path?
