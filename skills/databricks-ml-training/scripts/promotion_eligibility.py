"""Deterministic candidate selection and promotion-eligibility gates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

METRIC_DIRECTIONS = {
    "pr_auc": "maximize",
    "roc_auc": "maximize",
    "f1": "maximize",
    "precision": "maximize",
    "recall": "maximize",
    "log_loss": "minimize",
}
_POLICY_FIELDS = frozenset(
    {
        "enabled",
        "required_mode",
        "metric",
        "direction",
        "metric_threshold",
        "baseline_delta",
        "required_slices",
        "calibration_max_error",
        "approval",
    }
)
_ENABLED_POLICY_FIELDS = _POLICY_FIELDS - {"enabled"}
_CANDIDATE_FIELDS = frozenset(
    {
        "candidate_id",
        "metrics",
        "support",
        "baseline_metrics",
        "slices",
        "calibration_error",
    }
)
_SLICE_FIELDS = frozenset({"name", "minimum_support", "minimum_metric"})
_OBSERVED_SLICE_FIELDS = frozenset({"support", "metric"})
_APPROVAL_FIELDS = frozenset({"status", "reference"})


@dataclass(frozen=True)
class EligibilityDecision:
    """An auditable result of applying an eligibility policy."""

    eligible: bool
    reasons: tuple[str, ...]


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(float(value))
    )


def _require_exact_fields(
    value: Mapping[str, Any], allowed: frozenset[str], label: str
) -> None:
    unexpected = set(value).difference(allowed)
    if unexpected:
        raise ValueError(f"{label} contains unsupported fields")


def _validate_metric_direction(metric: Any, direction: Any) -> None:
    if not isinstance(metric, str) or METRIC_DIRECTIONS.get(metric) != direction:
        raise ValueError("metric and direction must be an allowed pair")


def validate_metric_value(metric: str, value: Any, label: str) -> None:
    """Require each named metric to stay in its meaningful numeric domain."""

    if not _is_number(value):
        raise ValueError(f"{label} must be a finite number")
    numeric_value = float(value)
    if metric == "log_loss":
        if numeric_value < 0.0:
            raise ValueError(f"{label} must be non-negative for log_loss")
    elif metric in METRIC_DIRECTIONS:
        if not 0.0 <= numeric_value <= 1.0:
            raise ValueError(f"{label} must be in [0, 1]")
    else:
        raise ValueError("metric is unsupported")


def _validate_baseline_delta(metric: str, value: Any) -> None:
    if not _is_number(value) or float(value) < 0.0:
        raise ValueError("baseline_delta must be a finite non-negative number")
    if metric != "log_loss" and float(value) > 1.0:
        raise ValueError("baseline_delta must not exceed one for bounded metrics")


def _validate_metric_mapping(metrics: Any, label: str) -> None:
    if not isinstance(metrics, Mapping) or not metrics:
        raise ValueError(f"{label} are required")
    for metric, value in metrics.items():
        if not isinstance(metric, str):
            raise ValueError(f"{label} names must be strings")
        validate_metric_value(metric, value, f"{label} {metric}")


def _validate_slice_requirement(requirement: Any, *, metric: str) -> None:
    if not isinstance(requirement, Mapping):
        raise ValueError("slice requirement must be an object")
    _require_exact_fields(requirement, _SLICE_FIELDS, "slice requirement")
    if set(requirement) != _SLICE_FIELDS:
        raise ValueError("slice requirement is incomplete")
    if not isinstance(requirement["name"], str) or not requirement["name"]:
        raise ValueError("slice requirement name is required")
    support = requirement["minimum_support"]
    if not isinstance(support, int) or isinstance(support, bool) or support < 1:
        raise ValueError("slice minimum support must be a positive integer")
    validate_metric_value(metric, requirement["minimum_metric"], "slice minimum metric")


def validate_eligibility_policy(policy: Mapping[str, Any]) -> None:
    """Reject incomplete, ambiguous, or unsupported eligibility policies."""

    if not isinstance(policy, Mapping):
        raise ValueError("eligibility policy must be an object")
    _require_exact_fields(policy, _POLICY_FIELDS, "eligibility policy")
    if set(policy).intersection({"enabled"}) != {"enabled"} or not isinstance(
        policy["enabled"], bool
    ):
        raise ValueError("eligibility policy requires boolean enabled")
    if not policy["enabled"]:
        return

    if not _ENABLED_POLICY_FIELDS.issubset(policy):
        raise ValueError("enabled eligibility policy is incomplete")
    if policy["required_mode"] != "full":
        raise ValueError("eligibility policy requires full mode")
    _validate_metric_direction(policy["metric"], policy["direction"])
    metric = str(policy["metric"])
    validate_metric_value(metric, policy["metric_threshold"], "metric_threshold")
    _validate_baseline_delta(metric, policy["baseline_delta"])
    if (
        not _is_number(policy["calibration_max_error"])
        or float(policy["calibration_max_error"]) < 0.0
    ):
        raise ValueError("calibration_max_error must be a finite non-negative number")
    if float(policy["calibration_max_error"]) > 1.0:
        raise ValueError("calibration_max_error must not exceed one")
    requirements = policy["required_slices"]
    if not isinstance(requirements, Sequence) or isinstance(requirements, (str, bytes)):
        raise ValueError("required_slices must be an array")
    names: set[str] = set()
    for requirement in requirements:
        _validate_slice_requirement(requirement, metric=metric)
        name = str(requirement["name"])
        if name in names:
            raise ValueError("slice requirement names must be unique")
        names.add(name)
    if not names:
        raise ValueError("at least one slice requirement is required")
    approval = policy["approval"]
    if not isinstance(approval, Mapping):
        raise ValueError("approval must be an object")
    _require_exact_fields(approval, _APPROVAL_FIELDS, "approval")
    if (
        approval.get("status") != "approved"
        or not isinstance(approval.get("reference"), str)
        or not approval["reference"]
    ):
        raise ValueError("enabled eligibility policy requires an approved reference")


def _validate_candidate(
    candidate: Mapping[str, Any],
    *,
    require_observations: bool,
    observation_metric: str | None = None,
) -> None:
    if not isinstance(candidate, Mapping):
        raise ValueError("candidate must be an object")
    _require_exact_fields(candidate, _CANDIDATE_FIELDS, "candidate")
    if (
        not isinstance(candidate.get("candidate_id"), str)
        or not candidate["candidate_id"]
    ):
        raise ValueError("candidate_id is required")
    metrics = candidate.get("metrics")
    _validate_metric_mapping(metrics, "candidate metrics")
    support = candidate.get("support")
    if support is not None:
        supports = support.values() if isinstance(support, Mapping) else (support,)
        for value in supports:
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("candidate support must be a positive integer")
    if not require_observations:
        return

    required = {"baseline_metrics", "slices", "calibration_error"}
    if not required.issubset(candidate):
        raise ValueError("candidate eligibility observations are incomplete")
    baseline_metrics = candidate["baseline_metrics"]
    _validate_metric_mapping(baseline_metrics, "baseline metrics")
    slices = candidate["slices"]
    if not isinstance(slices, Mapping):
        raise ValueError("candidate slices must be an object")
    for observed in slices.values():
        if not isinstance(observed, Mapping):
            raise ValueError("candidate slice must be an object")
        _require_exact_fields(observed, _OBSERVED_SLICE_FIELDS, "candidate slice")
        if set(observed) != _OBSERVED_SLICE_FIELDS:
            raise ValueError("candidate slice is incomplete")
        support_value = observed["support"]
        if (
            not isinstance(support_value, int)
            or isinstance(support_value, bool)
            or support_value < 0
        ):
            raise ValueError("candidate slice support must be a non-negative integer")
        if observation_metric is None:
            raise ValueError("candidate slice metric requires a policy metric")
        validate_metric_value(
            observation_metric, observed["metric"], "candidate slice metric"
        )
    calibration_error = candidate["calibration_error"]
    if not _is_number(calibration_error) or not 0.0 <= float(calibration_error) <= 1.0:
        raise ValueError("calibration error must be finite and in [0, 1]")


def _meets_threshold(value: float, threshold: float, direction: str) -> bool:
    return value >= threshold if direction == "maximize" else value <= threshold


def select_candidate(
    candidates: Sequence[Mapping[str, Any]], *, metric: str, direction: str
) -> Mapping[str, Any]:
    """Select one candidate by an allowed metric and lexical candidate ID."""

    _validate_metric_direction(metric, direction)
    if not candidates:
        raise ValueError("candidate selection requires at least one candidate")

    prepared: list[tuple[float, str, Mapping[str, Any]]] = []
    candidate_ids: set[str] = set()
    for candidate in candidates:
        _validate_candidate(candidate, require_observations=False)
        candidate_id = str(candidate["candidate_id"])
        if candidate_id in candidate_ids:
            raise ValueError("candidate IDs must be unique")
        candidate_ids.add(candidate_id)
        metrics = candidate["metrics"]
        metric_value = metrics.get(metric) if isinstance(metrics, Mapping) else None
        validate_metric_value(metric, metric_value, "selection metric")
        prepared.append((float(metric_value), candidate_id, candidate))
    if direction == "maximize":
        return min(prepared, key=lambda item: (-item[0], item[1]))[2]
    return min(prepared, key=lambda item: (item[0], item[1]))[2]


def evaluate_eligibility(
    candidate: Mapping[str, Any], *, policy: Mapping[str, Any], training_mode: str
) -> EligibilityDecision:
    """Evaluate a closed full-mode policy; disabled policies are always ineligible."""

    validate_eligibility_policy(policy)
    if not policy["enabled"]:
        return EligibilityDecision(False, ("eligibility_disabled",))
    metric = str(policy["metric"])
    _validate_candidate(candidate, require_observations=True, observation_metric=metric)

    reasons: list[str] = []
    if training_mode != "full":
        reasons.append("requires_full_mode")
    direction = str(policy["direction"])
    metrics = candidate["metrics"]
    baseline_metrics = candidate["baseline_metrics"]
    metric_value = metrics.get(metric) if isinstance(metrics, Mapping) else None
    baseline_value = (
        baseline_metrics.get(metric) if isinstance(baseline_metrics, Mapping) else None
    )
    if not _is_number(metric_value):
        reasons.append("missing_metric")
    elif not _meets_threshold(
        float(metric_value), float(policy["metric_threshold"]), direction
    ):
        reasons.append("metric_threshold_not_met")
    if not _is_number(baseline_value) or not _is_number(metric_value):
        reasons.append("missing_baseline")
    else:
        improvement = float(metric_value) - float(baseline_value)
        if direction == "minimize":
            improvement = -improvement
        if improvement < float(policy["baseline_delta"]):
            reasons.append("baseline_delta_not_met")

    candidate_slices = candidate["slices"]
    for requirement in policy["required_slices"]:
        observed = candidate_slices.get(requirement["name"])
        if not isinstance(observed, Mapping):
            reasons.append("missing_required_slice")
            continue
        if observed["support"] < requirement["minimum_support"]:
            reasons.append("slice_support_not_met")
        if not _meets_threshold(
            float(observed["metric"]), float(requirement["minimum_metric"]), direction
        ):
            reasons.append("slice_metric_not_met")

    calibration_error = float(candidate["calibration_error"])
    if calibration_error > float(policy["calibration_max_error"]):
        reasons.append("calibration_not_met")
    return EligibilityDecision(not reasons, tuple(sorted(set(reasons))))
