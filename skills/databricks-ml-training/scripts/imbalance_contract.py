"""Class-order-safe validation and deterministic binary threshold selection."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Any


@dataclass(frozen=True)
class RecallAtPrecisionFloorResult:
    """One validation-set recall observation at a requested precision floor."""

    precision_floor: float
    attained: bool
    threshold: float | None
    precision: float | None
    recall: float | None
    predicted_positive_count: int | None
    true_positive_count: int | None
    false_positive_count: int | None
    positive_support: int
    negative_support: int


def _validated_unit_interval_values(
    values: Sequence[Any], *, value_name: str
) -> list[float]:
    """Return finite real values in the closed unit interval."""

    normalized: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{value_name} must be numeric, not booleans")
        try:
            normalized_value = float(value)
        except (OverflowError, TypeError, ValueError):
            raise ValueError(f"{value_name} must be finite values in [0, 1]") from None
        if (
            not isfinite(normalized_value)
            or normalized_value < 0.0
            or normalized_value > 1.0
        ):
            raise ValueError(f"{value_name} must be finite values in [0, 1]")
        normalized.append(normalized_value)
    return normalized


def resolve_positive_class_index(classes: Sequence[Any], positive_class: Any) -> int:
    """Find the configured positive class without assuming index 1."""

    if len(classes) != 2:
        raise ValueError("binary thresholding requires exactly two fitted classes")
    if len(set(classes)) != 2:
        raise ValueError("fitted classes must be unique")
    matches = [index for index, value in enumerate(classes) if value == positive_class]
    if len(matches) != 1:
        raise ValueError("positive_class must occur exactly once in fitted classes")
    return matches[0]


def validate_binary_split_support(
    labels: Sequence[Any],
    *,
    classes: Sequence[Any],
    positive_class: Any,
    minimum_positive: int,
    minimum_negative: int,
) -> Counter[Any]:
    """Validate binary labels and the configured minimum class support."""

    positive_index = resolve_positive_class_index(classes, positive_class)
    if (
        not isinstance(minimum_positive, int)
        or isinstance(minimum_positive, bool)
        or not isinstance(minimum_negative, int)
        or isinstance(minimum_negative, bool)
        or minimum_positive < 1
        or minimum_negative < 1
    ):
        raise ValueError("class-support minima must both be positive")
    counts: Counter[Any] = Counter(labels)
    unknown = set(counts).difference(classes)
    if unknown:
        raise ValueError("split contains labels outside fitted classes")
    positive_count = counts[classes[positive_index]]
    negative_class = classes[1 - positive_index]
    negative_count = counts[negative_class]
    if positive_count < minimum_positive or negative_count < minimum_negative:
        raise ValueError("split does not satisfy binary class-support minima")
    return counts


def tune_binary_threshold(
    labels: Sequence[Any],
    positive_probabilities: Sequence[float],
    *,
    classes: Sequence[Any],
    positive_class: Any,
    minimum_positive: int,
    minimum_negative: int,
    objective: str = "f1",
) -> float:
    """Optimize F1, precision, or recall with a deterministic lower-threshold tie."""

    if len(labels) != len(positive_probabilities) or len(labels) == 0:
        raise ValueError("labels and probabilities must be non-empty and aligned")
    if objective not in {"f1", "precision", "recall"}:
        raise ValueError("threshold objective must be f1, precision, or recall")
    counts = validate_binary_split_support(
        labels,
        classes=classes,
        positive_class=positive_class,
        minimum_positive=minimum_positive,
        minimum_negative=minimum_negative,
    )
    probabilities = _validated_unit_interval_values(
        positive_probabilities, value_name="positive probabilities"
    )

    candidates = sorted(set(probabilities))
    scores: list[tuple[float, float]] = []
    positive_support = counts[positive_class]
    sorted_pairs = sorted(
        zip(probabilities, labels, strict=True), key=lambda pair: pair[0], reverse=True
    )
    true_positive = 0
    false_positive = 0
    pair_index = 0
    for threshold in reversed(candidates):
        while (
            pair_index < len(sorted_pairs)
            and sorted_pairs[pair_index][0] >= threshold
        ):
            _score, label = sorted_pairs[pair_index]
            true_positive += int(label == positive_class)
            false_positive += int(label != positive_class)
            pair_index += 1
        false_negative = positive_support - true_positive
        precision_denominator = true_positive + false_positive
        recall_denominator = true_positive + false_negative
        precision = (
            true_positive / precision_denominator if precision_denominator else 0.0
        )
        recall = true_positive / recall_denominator if recall_denominator else 0.0
        f1_denominator = precision + recall
        f1 = 2 * precision * recall / f1_denominator if f1_denominator else 0.0
        score = {"f1": f1, "precision": precision, "recall": recall}[objective]
        scores.append((score, threshold))
    best_score = max(score for score, _ in scores)
    return min(threshold for score, threshold in scores if score == best_score)


def recall_at_precision_floors(
    labels: Sequence[Any],
    positive_probabilities: Sequence[float],
    *,
    classes: Sequence[Any],
    positive_class: Any,
    minimum_positive: int,
    minimum_negative: int,
    precision_floors: Sequence[float],
) -> tuple[RecallAtPrecisionFloorResult, ...]:
    """Report maximal validation recall for each precision floor.

    Each floor is evaluated against actual finite score thresholds using
    ``score >= threshold``. Repeated floors intentionally produce repeated,
    equivalently evaluated results in the caller's requested order.
    """

    if len(labels) != len(positive_probabilities) or len(labels) == 0:
        raise ValueError("labels and probabilities must be non-empty and aligned")
    counts = validate_binary_split_support(
        labels,
        classes=classes,
        positive_class=positive_class,
        minimum_positive=minimum_positive,
        minimum_negative=minimum_negative,
    )
    probabilities = _validated_unit_interval_values(
        positive_probabilities, value_name="positive probabilities"
    )
    if len(precision_floors) == 0:
        raise ValueError("precision floors must be non-empty")
    floors = _validated_unit_interval_values(
        precision_floors, value_name="precision floors"
    )

    positive_index = resolve_positive_class_index(classes, positive_class)
    positive_support = counts[classes[positive_index]]
    negative_support = counts[classes[1 - positive_index]]
    threshold_metrics: list[tuple[float, float, float, int, int, int]] = []
    sorted_pairs = sorted(
        zip(probabilities, labels, strict=True), key=lambda pair: pair[0], reverse=True
    )
    true_positive = 0
    false_positive = 0
    start = 0
    while start < len(sorted_pairs):
        threshold = sorted_pairs[start][0]
        end = start
        group_positive = 0
        while end < len(sorted_pairs) and sorted_pairs[end][0] == threshold:
            group_positive += int(sorted_pairs[end][1] == positive_class)
            end += 1
        group_size = end - start
        true_positive += group_positive
        false_positive += group_size - group_positive
        predicted_positive = true_positive + false_positive
        precision = true_positive / predicted_positive if predicted_positive else 0.0
        recall = true_positive / positive_support
        threshold_metrics.append(
            (
                threshold,
                precision,
                recall,
                predicted_positive,
                true_positive,
                false_positive,
            )
        )
        start = end

    results: list[RecallAtPrecisionFloorResult] = []
    for floor in floors:
        attained = [metrics for metrics in threshold_metrics if metrics[1] >= floor]
        if not attained:
            results.append(
                RecallAtPrecisionFloorResult(
                    precision_floor=floor,
                    attained=False,
                    threshold=None,
                    precision=None,
                    recall=None,
                    predicted_positive_count=None,
                    true_positive_count=None,
                    false_positive_count=None,
                    positive_support=positive_support,
                    negative_support=negative_support,
                )
            )
            continue
        (
            threshold,
            precision,
            recall,
            predicted_positive,
            true_positive,
            false_positive,
        ) = max(attained, key=lambda metrics: (metrics[2], metrics[1], metrics[0]))
        results.append(
            RecallAtPrecisionFloorResult(
                precision_floor=floor,
                attained=True,
                threshold=threshold,
                precision=precision,
                recall=recall,
                predicted_positive_count=predicted_positive,
                true_positive_count=true_positive,
                false_positive_count=false_positive,
                positive_support=positive_support,
                negative_support=negative_support,
            )
        )
    return tuple(results)
