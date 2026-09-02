"""Class-order-safe validation and deterministic binary threshold selection."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from math import isfinite
from typing import Any


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

    if len(labels) != len(positive_probabilities) or not labels:
        raise ValueError("labels and probabilities must be non-empty and aligned")
    if objective not in {"f1", "precision", "recall"}:
        raise ValueError("threshold objective must be f1, precision, or recall")
    validate_binary_split_support(
        labels,
        classes=classes,
        positive_class=positive_class,
        minimum_positive=minimum_positive,
        minimum_negative=minimum_negative,
    )
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool)
        for value in positive_probabilities
    ):
        raise ValueError("positive probabilities must be numeric, not booleans")
    probabilities = [float(value) for value in positive_probabilities]
    if any(
        not isfinite(value) or value < 0.0 or value > 1.0 for value in probabilities
    ):
        raise ValueError("positive probabilities must be finite values in [0, 1]")

    candidates = sorted(set(probabilities))
    scores: list[tuple[float, float]] = []
    for threshold in candidates:
        true_positive = sum(
            label == positive_class and score >= threshold
            for label, score in zip(labels, probabilities, strict=True)
        )
        false_positive = sum(
            label != positive_class and score >= threshold
            for label, score in zip(labels, probabilities, strict=True)
        )
        false_negative = sum(
            label == positive_class and score < threshold
            for label, score in zip(labels, probabilities, strict=True)
        )
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
