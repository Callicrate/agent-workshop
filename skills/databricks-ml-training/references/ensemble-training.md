# Ensemble Training

Use this when a model is made of multiple trained members, per-label member sets, or an aggregation package consumed as one promotion or inference unit.

## Decide The Logged Unit

Choose and document one packaging contract:

- **Member models:** each member is logged and registered separately; promotion selects a group by durable member IDs.
- **Ensemble package:** one MLflow model contains member metadata and scoring aggregation logic.
- **Hybrid:** members are logged separately and a pyfunc wrapper or config artifact defines the ensemble unit.

Do not leave downstream promotion to infer ensemble membership from latest runs or notebook state.

## Required Ensemble Artifacts

Log or persist:

- ensemble ID and version
- member run IDs and model URIs
- member count per label or category
- aggregation method such as min, max, mean, weighted mean, vote, or stacked model
- member weights, seeds, folds, and feature configs
- per-member metrics and aggregate ensemble metrics
- threshold or gate settings applied after aggregation
- input example and signature for the ensemble scoring unit

## Promotion Semantics

Decide whether promotion treats the ensemble as one unit or promotes each member independently.

If promotion is by ensemble unit, the promotion candidate row must identify the ensemble ID, all member run IDs, the aggregate metric, and the point-in-time training window.

If promotion is by member, the downstream ensemble builder must resolve exactly which current members are eligible at the requested `AT_TIMESTAMP`.

## Metric Provenance

Report whether metrics belong to:

- one member
- average of members
- full ensemble scoring output
- post-threshold or post-gate output
- per-label or global aggregate

Do not compare a member metric to an ensemble metric without labeling the provenance.