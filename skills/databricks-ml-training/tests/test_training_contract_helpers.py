"""Offline tests for pure helpers and the side-effect-free parameter template."""

# ruff: noqa: E402

from __future__ import annotations

import hashlib
import importlib.util
import json
from random import Random
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from feature_schema_contract import (  # noqa: E402
    compute_feature_schema_fingerprint,
    validate_feature_schema_artifact,
    validate_feature_schema_before_fit,
)
from imbalance_contract import (  # noqa: E402
    recall_at_precision_floors,
    resolve_positive_class_index,
    tune_binary_threshold,
    validate_binary_split_support,
)
from mlflow_example_privacy import (  # noqa: E402
    find_sensitive_columns,
    validate_input_example_columns,
)
from point_in_time_contract import (  # noqa: E402
    select_feature_as_of,
    validate_pairwise_disjoint_split_ids,
)
from promotion_eligibility import (  # noqa: E402
    METRIC_DIRECTIONS,
    evaluate_eligibility,
    select_candidate,
    validate_eligibility_policy,
)
from privacy_column_cases import (  # noqa: E402
    BENIGN_COLUMN_NAMES,
    COMPACT_HEADER_CASES,
    SENSITIVE_COLUMN_NAMES,
    V9_COMPOUND_CASES,
)


def load_parameter_template():
    template_path = SKILL_ROOT / "assets" / "parameter-block-template.py"
    spec = importlib.util.spec_from_file_location(
        "training_parameter_template", template_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("parameter template could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def full_policy(
    metric: str = "pr_auc", direction: str = "maximize"
) -> dict[str, object]:
    return {
        "enabled": True,
        "required_mode": "full",
        "metric": metric,
        "direction": direction,
        "metric_threshold": 0.7 if direction == "maximize" else 0.4,
        "baseline_delta": 0.05,
        "required_slices": [
            {
                "name": "global",
                "minimum_support": 10,
                "minimum_metric": 0.7 if direction == "maximize" else 0.4,
            }
        ],
        "calibration_max_error": 0.05,
        "approval": {"status": "approved", "reference": "ticket-123"},
    }


def eligible_candidate(
    metric: str = "pr_auc", value: float = 0.8, baseline: float = 0.7
) -> dict[str, object]:
    return {
        "candidate_id": "candidate-a",
        "metrics": {metric: value},
        "baseline_metrics": {metric: baseline},
        "slices": {"global": {"support": 20, "metric": value}},
        "calibration_error": 0.04,
    }


def feature_artifact() -> dict[str, object]:
    artifact: dict[str, object] = {
        "version": "v1",
        "fingerprint": "",
        "features": [
            {
                "name": "amount",
                "logical_type": "double",
                "nullable": False,
                "null_policy": "forbid",
            },
            {
                "name": "country_code",
                "logical_type": "string",
                "nullable": True,
                "null_policy": "indicator_and_impute",
            },
        ],
        "target": {
            "name": "label",
            "logical_type": "string",
            "nullable": False,
            "null_policy": "forbid",
        },
    }
    artifact["fingerprint"] = compute_feature_schema_fingerprint(artifact)
    return artifact


def fingerprint_without_validation(artifact: dict[str, object]) -> str:
    """Recreate the documented hash serialization for malformed-artifact tests."""

    payload = {
        "features": artifact["features"],
        "target": artifact["target"],
        "version": artifact["version"],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def brute_force_recall_at_precision_floors(
    labels: list[str], probabilities: list[float], floors: tuple[float, ...]
) -> tuple[tuple[object, ...], ...]:
    """Independent threshold scan used only to check the grouped sweep."""

    positive_support = labels.count("positive")
    negative_support = labels.count("negative")
    results: list[tuple[object, ...]] = []
    for floor in floors:
        candidates: list[tuple[float, float, float, int, int, int]] = []
        for threshold in sorted(set(probabilities)):
            true_positive = sum(
                label == "positive" and score >= threshold
                for label, score in zip(labels, probabilities, strict=True)
            )
            false_positive = sum(
                label == "negative" and score >= threshold
                for label, score in zip(labels, probabilities, strict=True)
            )
            predicted_positive = true_positive + false_positive
            precision = true_positive / predicted_positive
            recall = true_positive / positive_support
            if precision >= floor:
                candidates.append(
                    (
                        threshold,
                        precision,
                        recall,
                        predicted_positive,
                        true_positive,
                        false_positive,
                    )
                )
        if not candidates:
            results.append(
                (
                    floor,
                    False,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    positive_support,
                    negative_support,
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
        ) = max(
            candidates, key=lambda candidate: (candidate[2], candidate[1], candidate[0])
        )
        results.append(
            (
                floor,
                True,
                threshold,
                precision,
                recall,
                predicted_positive,
                true_positive,
                false_positive,
                positive_support,
                negative_support,
            )
        )
    return tuple(results)


class TrainingContractHelperTests(unittest.TestCase):
    def test_metric_allowlist_is_complete_and_directional(self) -> None:
        self.assertEqual(METRIC_DIRECTIONS["pr_auc"], "maximize")
        self.assertEqual(METRIC_DIRECTIONS["log_loss"], "minimize")
        with self.assertRaises(ValueError):
            select_candidate(
                [{"candidate_id": "a", "metrics": {"pr_auc": 0.8}}],
                metric="pr_auc",
                direction="minimize",
            )

    def test_selection_is_deterministic_for_both_directions(self) -> None:
        candidates = [
            {"candidate_id": "b", "metrics": {"pr_auc": 0.8, "log_loss": 0.3}},
            {"candidate_id": "a", "metrics": {"pr_auc": 0.8, "log_loss": 0.3}},
            {"candidate_id": "c", "metrics": {"pr_auc": 0.7, "log_loss": 0.4}},
        ]
        self.assertEqual(
            select_candidate(candidates, metric="pr_auc", direction="maximize")[
                "candidate_id"
            ],
            "a",
        )
        self.assertEqual(
            select_candidate(candidates, metric="log_loss", direction="minimize")[
                "candidate_id"
            ],
            "a",
        )

    def test_selection_rejects_duplicate_and_invalid_candidate_values(self) -> None:
        cases = [
            [
                {"candidate_id": "a", "metrics": {"pr_auc": 0.8}},
                {"candidate_id": "a", "metrics": {"pr_auc": 0.7}},
            ],
            [{"candidate_id": "a", "metrics": {"pr_auc": True}}],
            [{"candidate_id": "a", "metrics": {"pr_auc": float("nan")}}],
            [{"candidate_id": "a", "metrics": {"pr_auc": 0.8}, "support": True}],
            [{"candidate_id": "a", "metrics": {"pr_auc": 0.8}, "unexpected": 1}],
        ]
        for candidates in cases:
            with self.subTest(candidates=candidates):
                with self.assertRaises(ValueError):
                    select_candidate(candidates, metric="pr_auc", direction="maximize")

    def test_enabled_policy_requires_the_complete_closed_shape(self) -> None:
        policy = full_policy()
        validate_eligibility_policy(policy)
        for mutation in (
            lambda value: value.pop("metric"),
            lambda value: value.update({"direction": "minimize"}),
            lambda value: value.update({"unknown": True}),
            lambda value: value.update({"metric_threshold": True}),
            lambda value: value.update({"approval": {"status": "approved"}}),
        ):
            invalid = dict(policy)
            mutation(invalid)
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    validate_eligibility_policy(invalid)

    def test_disabled_policy_is_always_ineligible(self) -> None:
        decision = evaluate_eligibility(
            {"not_a_candidate": "ignored"},
            policy={"enabled": False},
            training_mode="full",
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reasons, ("eligibility_disabled",))

    def test_eligibility_requires_full_mode_and_all_maximize_gates(self) -> None:
        candidate = eligible_candidate()
        decision = evaluate_eligibility(
            candidate, policy=full_policy(), training_mode="full"
        )
        self.assertTrue(decision.eligible)
        self.assertFalse(
            evaluate_eligibility(
                candidate, policy=full_policy(), training_mode="dev"
            ).eligible
        )
        for field, value, expected_reason in (
            ("metrics", {"pr_auc": 0.6}, "metric_threshold_not_met"),
            ("baseline_metrics", {"pr_auc": 0.78}, "baseline_delta_not_met"),
            (
                "slices",
                {"global": {"support": 9, "metric": 0.8}},
                "slice_support_not_met",
            ),
            (
                "slices",
                {"global": {"support": 20, "metric": 0.6}},
                "slice_metric_not_met",
            ),
            ("calibration_error", 0.06, "calibration_not_met"),
        ):
            invalid = eligible_candidate()
            invalid[field] = value
            decision = evaluate_eligibility(
                invalid, policy=full_policy(), training_mode="full"
            )
            with self.subTest(field=field, expected_reason=expected_reason):
                self.assertIn(expected_reason, decision.reasons)

    def test_minimize_eligibility_applies_threshold_baseline_and_slices(self) -> None:
        policy = full_policy("log_loss", "minimize")
        candidate = eligible_candidate("log_loss", 0.35, 0.43)
        candidate["slices"] = {"global": {"support": 20, "metric": 0.35}}
        self.assertTrue(
            evaluate_eligibility(
                candidate, policy=policy, training_mode="full"
            ).eligible
        )
        candidate["slices"] = {"global": {"support": 20, "metric": 0.45}}
        self.assertIn(
            "slice_metric_not_met",
            evaluate_eligibility(
                candidate, policy=policy, training_mode="full"
            ).reasons,
        )

    def test_eligibility_rejects_bool_and_nonfinite_observations(self) -> None:
        for field, value in (
            ("metrics", {"pr_auc": True}),
            ("baseline_metrics", {"pr_auc": float("inf")}),
            ("slices", {"global": {"support": True, "metric": 0.8}}),
            ("slices", {"global": {"support": 20, "metric": float("nan")}}),
        ):
            candidate = eligible_candidate()
            candidate[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                evaluate_eligibility(
                    candidate, policy=full_policy(), training_mode="full"
                )
        for calibration_error in (True, float("nan"), float("inf"), -0.01, 1.01):
            candidate = eligible_candidate()
            candidate["calibration_error"] = calibration_error
            with (
                self.subTest(calibration_error=calibration_error),
                self.assertRaises(ValueError),
            ):
                evaluate_eligibility(
                    candidate, policy=full_policy(), training_mode="full"
                )

    def test_metric_domains_reject_impossible_candidate_baseline_slice_and_policy_values(
        self,
    ) -> None:
        invalid_policy = full_policy()
        invalid_policy["metric_threshold"] = 1.01
        with self.assertRaises(ValueError):
            validate_eligibility_policy(invalid_policy)
        invalid_policy = full_policy()
        invalid_policy["required_slices"] = [
            {"name": "global", "minimum_support": 10, "minimum_metric": -0.01}
        ]
        with self.assertRaises(ValueError):
            validate_eligibility_policy(invalid_policy)
        for field, value in (
            ("metrics", {"pr_auc": 1.01}),
            ("baseline_metrics", {"pr_auc": -0.01}),
            ("slices", {"global": {"support": 20, "metric": 1.01}}),
        ):
            candidate = eligible_candidate()
            candidate[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                evaluate_eligibility(
                    candidate, policy=full_policy(), training_mode="full"
                )
        with self.assertRaises(ValueError):
            select_candidate(
                [{"candidate_id": "a", "metrics": {"log_loss": -0.01}}],
                metric="log_loss",
                direction="minimize",
            )

    def test_baseline_delta_is_bounded_only_for_bounded_metrics(self) -> None:
        for metric in ("pr_auc", "roc_auc", "f1", "precision", "recall"):
            policy = full_policy(metric, "maximize")
            policy["baseline_delta"] = 1.0
            validate_eligibility_policy(policy)
            policy["baseline_delta"] = 1.01
            with self.subTest(metric=metric), self.assertRaises(ValueError):
                validate_eligibility_policy(policy)
        log_loss_policy = full_policy("log_loss", "minimize")
        log_loss_policy["baseline_delta"] = 2.0
        validate_eligibility_policy(log_loss_policy)

    def test_reversed_string_class_order_is_resolved(self) -> None:
        self.assertEqual(
            resolve_positive_class_index(["negative", "positive"], "positive"), 1
        )
        self.assertEqual(
            resolve_positive_class_index(["positive", "negative"], "positive"), 0
        )

    def test_threshold_tuning_support_and_objectives_are_exact(self) -> None:
        labels = ["positive", "negative", "positive", "negative"]
        probabilities = [0.9, 0.8, 0.7, 0.1]
        expected = {"f1": 0.7, "precision": 0.9, "recall": 0.1}
        for objective, threshold in expected.items():
            with self.subTest(objective=objective):
                self.assertEqual(
                    tune_binary_threshold(
                        labels,
                        probabilities,
                        classes=["positive", "negative"],
                        positive_class="positive",
                        minimum_positive=2,
                        minimum_negative=2,
                        objective=objective,
                    ),
                    threshold,
                )
        with self.assertRaises(ValueError):
            tune_binary_threshold(
                labels,
                probabilities,
                classes=["positive", "negative"],
                positive_class="positive",
                minimum_positive=2,
                minimum_negative=2,
                objective="accuracy",
            )
        with self.assertRaises(ValueError):
            tune_binary_threshold(
                labels,
                [True, 0.8, 0.7, 0.1],
                classes=["positive", "negative"],
                positive_class="positive",
                minimum_positive=2,
                minimum_negative=2,
            )
        with self.assertRaises(ValueError):
            tune_binary_threshold(
                labels,
                ["0.9", 0.8, 0.7, 0.1],  # type: ignore[list-item]
                classes=["positive", "negative"],
                positive_class="positive",
                minimum_positive=2,
                minimum_negative=2,
            )

    def test_threshold_tie_breaks_at_the_lowest_threshold(self) -> None:
        self.assertEqual(
            tune_binary_threshold(
                ["positive", "negative"],
                [0.5, 0.5],
                classes=["positive", "negative"],
                positive_class="positive",
                minimum_positive=1,
                minimum_negative=1,
                objective="recall",
            ),
            0.5,
        )
        with self.assertRaises(ValueError):
            validate_binary_split_support(
                ["negative", "negative"],
                classes=["negative", "positive"],
                positive_class="positive",
                minimum_positive=1,
                minimum_negative=1,
            )

    def test_recall_at_precision_floor_reports_the_tradeoff_without_replacing_tuning(
        self,
    ) -> None:
        labels = ["positive"] * 10 + ["negative"] * 3
        probabilities = [
            0.99,
            0.799,
            0.79,
            0.78,
            0.77,
            0.76,
            0.75,
            0.74,
            0.73,
            0.72,
            0.8,
            0.71,
            0.7,
        ]
        self.assertEqual(
            tune_binary_threshold(
                labels,
                probabilities,
                classes=["positive", "negative"],
                positive_class="positive",
                minimum_positive=10,
                minimum_negative=3,
                objective="precision",
            ),
            0.99,
        )

        reports = recall_at_precision_floors(
            labels,
            probabilities,
            classes=["positive", "negative"],
            positive_class="positive",
            minimum_positive=10,
            minimum_negative=3,
            precision_floors=(0.9, 0.95, 0.9),
        )

        self.assertEqual(
            [report.precision_floor for report in reports], [0.9, 0.95, 0.9]
        )
        self.assertTrue(reports[0].attained)
        self.assertEqual(reports[0].threshold, 0.72)
        self.assertEqual(reports[0].precision, 10 / 11)
        self.assertEqual(reports[0].recall, 1.0)
        self.assertEqual(reports[0].predicted_positive_count, 11)
        self.assertEqual(reports[0].true_positive_count, 10)
        self.assertEqual(reports[0].false_positive_count, 1)
        self.assertEqual(reports[0].positive_support, 10)
        self.assertEqual(reports[0].negative_support, 3)
        self.assertEqual(reports[1].threshold, 0.99)
        self.assertEqual(reports[1].recall, 0.1)
        self.assertEqual(reports[2], reports[0])

    def test_recall_at_precision_floor_uses_actual_thresholds_and_explicit_unattained(
        self,
    ) -> None:
        (report,) = recall_at_precision_floors(
            ["positive", "positive", "negative"],
            [0.8, 0.7, 0.9],
            classes=["positive", "negative"],
            positive_class="positive",
            minimum_positive=2,
            minimum_negative=1,
            precision_floors=(0.8,),
        )

        self.assertFalse(report.attained)
        self.assertIsNone(report.threshold)
        self.assertIsNone(report.precision)
        self.assertIsNone(report.recall)
        self.assertIsNone(report.predicted_positive_count)
        self.assertIsNone(report.true_positive_count)
        self.assertIsNone(report.false_positive_count)
        self.assertEqual((report.positive_support, report.negative_support), (2, 1))

    def test_recall_at_precision_floor_breaks_recall_ties_by_precision(self) -> None:
        (report,) = recall_at_precision_floors(
            ["positive", "positive"] + ["negative"] * 7,
            [0.9, 0.6, 0.8, 0.7, 0.65, 0.64, 0.63, 0.62, 0.61],
            classes=["positive", "negative"],
            positive_class="positive",
            minimum_positive=2,
            minimum_negative=7,
            precision_floors=(0.5,),
        )

        self.assertTrue(report.attained)
        self.assertEqual(report.threshold, 0.9)
        self.assertEqual((report.precision, report.recall), (1.0, 0.5))

    def test_recall_at_precision_floor_includes_scores_equal_to_threshold(self) -> None:
        (report,) = recall_at_precision_floors(
            ["positive", "positive", "negative"],
            [0.8, 0.8, 0.7],
            classes=["positive", "negative"],
            positive_class="positive",
            minimum_positive=2,
            minimum_negative=1,
            precision_floors=(1.0,),
        )

        self.assertTrue(report.attained)
        self.assertEqual(report.threshold, 0.8)
        self.assertEqual(
            (report.predicted_positive_count, report.true_positive_count), (2, 2)
        )

    def test_recall_at_precision_floor_matches_small_brute_force_oracle(self) -> None:
        random = Random(20261012)
        labels = ["negative"] + [
            "positive" if random.randrange(4) == 0 else "negative" for _ in range(63)
        ]
        labels[1] = "positive"
        probabilities = [1.0, 0.9] + [random.randrange(10) / 10 for _ in range(62)]
        floors = (0.0, 0.25, 0.5, 0.95, 0.5)

        expected = brute_force_recall_at_precision_floors(labels, probabilities, floors)
        reports = recall_at_precision_floors(
            labels,
            probabilities,
            classes=["positive", "negative"],
            positive_class="positive",
            minimum_positive=1,
            minimum_negative=1,
            precision_floors=floors,
        )
        observed = tuple(
            (
                report.precision_floor,
                report.attained,
                report.threshold,
                report.precision,
                report.recall,
                report.predicted_positive_count,
                report.true_positive_count,
                report.false_positive_count,
                report.positive_support,
                report.negative_support,
            )
            for report in reports
        )

        self.assertEqual(observed, expected)
        self.assertFalse(reports[3].attained)
        self.assertEqual(reports[2], reports[4])

    def test_recall_at_precision_floor_accepts_numpy_model_arrays(self) -> None:
        try:
            import numpy as np
        except ImportError:
            self.skipTest("NumPy is not installed")

        labels = np.array(["positive", "positive", "negative"])
        classes = np.array(["positive", "negative"])
        for dtype in (np.float32, np.float64):
            probabilities = np.array([0.9, 0.8, 0.7], dtype=dtype)
            reports = recall_at_precision_floors(
                labels,
                probabilities,
                classes=classes,
                positive_class="positive",
                minimum_positive=1,
                minimum_negative=1,
                precision_floors=np.array([0.5, 0.95], dtype=np.float64),
            )
            with self.subTest(dtype=dtype):
                self.assertEqual(len(reports), 2)
                self.assertTrue(all(report.attained for report in reports))
                self.assertEqual(reports[0].threshold, float(probabilities[1]))
                self.assertEqual(reports[0].recall, 1.0)
                self.assertEqual(
                    recall_at_precision_floors(
                        labels,
                        list(probabilities),
                        classes=classes,
                        positive_class="positive",
                        minimum_positive=1,
                        minimum_negative=1,
                        precision_floors=list(np.array([0.5, 0.95], dtype=np.float64)),
                    ),
                    reports,
                )
                self.assertEqual(
                    tune_binary_threshold(
                        labels,
                        probabilities,
                        classes=classes,
                        positive_class="positive",
                        minimum_positive=1,
                        minimum_negative=1,
                        objective="precision",
                    ),
                    float(probabilities[1]),
                )

    def test_recall_at_precision_floor_scales_to_high_cardinality_scores(self) -> None:
        sample_count = 50_000
        labels = [
            "positive" if index % 3 == 0 else "negative"
            for index in range(sample_count)
        ]
        probabilities = [
            (index + 1) / (sample_count + 1) for index in range(sample_count)
        ]
        reports = recall_at_precision_floors(
            labels,
            probabilities,
            classes=["positive", "negative"],
            positive_class="positive",
            minimum_positive=1,
            minimum_negative=1,
            precision_floors=(0.0, 0.5, 0.9, 0.99),
        )

        self.assertEqual(len(reports), 4)
        self.assertEqual(
            (reports[0].positive_support, reports[0].negative_support),
            (16_667, 33_333),
        )
        self.assertTrue(reports[0].attained)
        self.assertEqual(reports[0].recall, 1.0)
        self.assertFalse(reports[2].attained)
        self.assertFalse(reports[3].attained)

    def test_recall_at_precision_floor_validates_inputs_and_support(self) -> None:
        valid_labels = ["positive", "negative"]
        valid_probabilities = [0.9, 0.1]
        valid_kwargs = {
            "classes": ["positive", "negative"],
            "positive_class": "positive",
            "minimum_positive": 1,
            "minimum_negative": 1,
            "precision_floors": (0.9,),
        }
        invalid_cases = (
            ([], [], valid_kwargs),
            (["positive", "positive"], [0.9, 0.8], valid_kwargs),
            (["positive", "unknown"], [0.9, 0.1], valid_kwargs),
            (valid_labels, [True, 0.1], valid_kwargs),
            (valid_labels, [float("nan"), 0.1], valid_kwargs),
            (valid_labels, [float("inf"), 0.1], valid_kwargs),
            (
                valid_labels,
                valid_probabilities,
                {**valid_kwargs, "minimum_positive": 2},
            ),
            (
                valid_labels,
                valid_probabilities,
                {**valid_kwargs, "classes": ["positive"]},
            ),
            (
                valid_labels,
                valid_probabilities,
                {**valid_kwargs, "precision_floors": ()},
            ),
            (
                valid_labels,
                valid_probabilities,
                {**valid_kwargs, "precision_floors": (True,)},
            ),
            (
                valid_labels,
                valid_probabilities,
                {**valid_kwargs, "precision_floors": (float("inf"),)},
            ),
            (
                valid_labels,
                valid_probabilities,
                {**valid_kwargs, "precision_floors": (float("nan"),)},
            ),
            (
                valid_labels,
                valid_probabilities,
                {**valid_kwargs, "precision_floors": (-0.01,)},
            ),
            (
                valid_labels,
                valid_probabilities,
                {**valid_kwargs, "precision_floors": (1.01,)},
            ),
        )
        for labels, probabilities, kwargs in invalid_cases:
            with self.subTest(
                labels=labels, probabilities=probabilities, kwargs=kwargs
            ):
                with self.assertRaises(ValueError):
                    recall_at_precision_floors(labels, probabilities, **kwargs)
        huge_integer = 10**10_000
        for probabilities, floors in (
            ([huge_integer, 0.1], (0.9,)),
            (valid_probabilities, (huge_integer,)),
        ):
            with self.subTest("large_integer"):
                with self.assertRaises(ValueError):
                    recall_at_precision_floors(
                        valid_labels,
                        probabilities,
                        **{**valid_kwargs, "precision_floors": floors},
                    )
        with self.assertRaises(ValueError):
            tune_binary_threshold(
                valid_labels,
                [huge_integer, 0.1],
                classes=valid_kwargs["classes"],
                positive_class="positive",
                minimum_positive=1,
                minimum_negative=1,
            )

    def test_split_and_point_in_time_fixtures(self) -> None:
        validate_pairwise_disjoint_split_ids(
            {"train": ["ab"], "validation": ["b"], "test": ["c"]}
        )
        for train_ids in (
            "ab",
            b"ab",
            [],
            [""],
            [b"ab"],
            [1],
            ["a", "a"],
        ):
            with self.subTest(train_ids=train_ids), self.assertRaises(ValueError):
                validate_pairwise_disjoint_split_ids(
                    {"train": train_ids, "validation": ["b"], "test": ["c"]}  # type: ignore[dict-item]
                )
        with self.assertRaises(ValueError):
            validate_pairwise_disjoint_split_ids(
                {"train": ["a"], "validation": ["a"], "test": ["c"]}
            )
        valid_row = {
            "id": "valid",
            "valid_from": "2025-01-01T00:00:00Z",
            "valid_to": None,
            "available_at": "2025-01-01T00:00:00Z",
        }
        self.assertEqual(
            select_feature_as_of(
                example_at="2025-01-10T00:00:00Z", feature_rows=[valid_row]
            )["id"],
            "valid",
        )
        for invalid_row in (
            {**valid_row, "valid_from": "2025-01-11T00:00:00Z"},
            {**valid_row, "available_at": "2025-01-11T00:00:00Z"},
            {**valid_row, "valid_to": "2025-01-01T00:00:00Z"},
        ):
            with self.subTest(invalid_row=invalid_row), self.assertRaises(ValueError):
                select_feature_as_of(
                    example_at="2025-01-10T00:00:00Z", feature_rows=[invalid_row]
                )

    def test_privacy_scan_normalizes_identifiers_network_and_credentials(self) -> None:
        self.assertEqual(
            find_sensitive_columns(SENSITIVE_COLUMN_NAMES),
            tuple(sorted(SENSITIVE_COLUMN_NAMES)),
        )
        with self.assertRaises(ValueError):
            validate_input_example_columns(["amount", "transaction_id"])
        self.assertEqual(find_sensitive_columns(BENIGN_COLUMN_NAMES), ())

    def test_privacy_scan_rejects_invalid_column_containers(self) -> None:
        for columns in ("token", b"token", None, {"token"}, iter(["token"])):
            with self.subTest(columns_type=type(columns).__name__):
                with self.assertRaises(ValueError):
                    find_sensitive_columns(columns)
                with self.assertRaises(ValueError):
                    validate_input_example_columns(columns)

    def test_privacy_scan_uses_bounded_compound_segmentation(self) -> None:
        self.assertEqual(
            find_sensitive_columns(["x" * 128, "accountability_score"]), ()
        )
        for column in ("accountid", "contactaddress", "accesstoken"):
            with self.subTest(column=column):
                self.assertEqual(find_sensitive_columns([column]), (column,))
        for column in ("customer-id", "x" * 129, "1invalid"):
            with self.subTest(column=column), self.assertRaises(ValueError):
                find_sensitive_columns([column])

    def test_privacy_scan_rejects_compact_header_compounds(self) -> None:
        self.assertEqual(
            find_sensitive_columns(COMPACT_HEADER_CASES),
            tuple(sorted(COMPACT_HEADER_CASES)),
        )

    def test_privacy_scan_rejects_number_optional_document_compounds(self) -> None:
        self.assertEqual(
            find_sensitive_columns(V9_COMPOUND_CASES),
            tuple(sorted(V9_COMPOUND_CASES)),
        )

    def test_feature_schema_artifact_and_pre_fit_check(self) -> None:
        artifact = feature_artifact()
        validate_feature_schema_artifact(artifact)
        validate_feature_schema_before_fit(
            artifact,
            observed_features=[
                {"name": "amount", "logical_type": "double", "nullable": False},
                {"name": "country_code", "logical_type": "string", "nullable": True},
            ],
            observed_target={
                "name": "label",
                "logical_type": "string",
                "nullable": False,
            },
        )
        with self.assertRaises(ValueError):
            validate_feature_schema_before_fit(
                artifact,
                observed_features=[
                    {"name": "amount", "logical_type": "integer", "nullable": False},
                    {
                        "name": "country_code",
                        "logical_type": "string",
                        "nullable": True,
                    },
                ],
                observed_target={
                    "name": "label",
                    "logical_type": "string",
                    "nullable": False,
                },
            )
        reordered = feature_artifact()
        with self.assertRaises(ValueError):
            validate_feature_schema_before_fit(
                reordered,
                observed_features=[
                    {
                        "name": "country_code",
                        "logical_type": "string",
                        "nullable": True,
                    },
                    {"name": "amount", "logical_type": "double", "nullable": False},
                ],
                observed_target={
                    "name": "label",
                    "logical_type": "string",
                    "nullable": False,
                },
            )
        for mutation in (
            lambda value: value["features"][0].update({"logical_type": "integer"}),
            lambda value: value["features"][0].update({"nullable": True}),
            lambda value: value["features"].reverse(),
            lambda value: value["target"].update({"null_policy": "impute"}),
        ):
            mutated = feature_artifact()
            mutation(mutated)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_feature_schema_artifact(mutated)
        artifact = feature_artifact()
        artifact["features"].append(artifact["features"][0])
        with self.assertRaises(ValueError):
            validate_feature_schema_artifact(artifact)

    def test_feature_schema_column_names_match_the_json_schema_contract(self) -> None:
        for location, invalid_name in (
            ("features", "../../not_a_column"),
            ("target", "x" * 129),
        ):
            artifact = feature_artifact()
            if location == "features":
                artifact["features"][0]["name"] = invalid_name  # type: ignore[index]
            else:
                artifact["target"]["name"] = invalid_name  # type: ignore[index]
            artifact["fingerprint"] = fingerprint_without_validation(artifact)
            with self.subTest(location=location), self.assertRaises(ValueError):
                validate_feature_schema_artifact(artifact)

    def test_parameter_template_is_import_safe_and_contained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            before = set(temporary_root.iterdir())
            template = load_parameter_template()
            self.assertEqual(set(temporary_root.iterdir()), before)
            project_root = temporary_root / "project"
            output_root = project_root / "outputs"
            context = template.build_run_context(
                started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                job_id="job-1",
                run_id="run-1",
                idempotency_key="same-inputs",
                artifact_root=output_root,
                project_root=project_root,
            )
            self.assertFalse(output_root.exists())
            self.assertIn("same-inputs", str(context.training_output_dir))
            self.assertEqual(
                context.training_output_dir,
                template.build_run_context(
                    started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    job_id="job-1",
                    run_id="run-1",
                    idempotency_key="same-inputs",
                    artifact_root=output_root,
                    project_root=project_root,
                ).training_output_dir,
            )

    def test_parameter_template_rejects_path_and_identifier_escapes(self) -> None:
        template = load_parameter_template()
        self.assertEqual(
            template.validate_run_identifier("run.name", "run_id"), "run.name"
        )
        self.assertEqual(
            template.validate_artifact_root(
                Path("/Volumes/main/ml_models/artifacts/fraud")
            ).as_posix(),
            "/Volumes/main/ml_models/artifacts/fraud",
        )
        for value in (
            "../run",
            "/run",
            "C:/run",
            "\\\\server\\run",
            "run/name",
            "run\x00x",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                template.validate_run_identifier(value, "run_id")
        for root in (
            Path("/Volumes/main/ml_models/../artifacts"),
            Path("/volumes/main/ml_models/artifacts"),
            Path("/Volumes/main/ml_models/artifacts/unsafe space"),
        ):
            with self.subTest(root=root), self.assertRaises(ValueError):
                template.validate_artifact_root(root)

    def test_parameter_template_keeps_legacy_logged_model_labels(self) -> None:
        template = load_parameter_template()
        self.assertEqual(
            template.validate_run_identifier(
                "main.ml_models.fraud_detector", "model name"
            ),
            "main.ml_models.fraud_detector",
        )

        original_model_name = template.MODEL_NAME
        template.MODEL_NAME = "main.ml_models.fraud_detector"
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                temporary_root = Path(temporary_directory)
                context = template.build_run_context(
                    started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    job_id="job-1",
                    run_id="run-1",
                    idempotency_key="same-inputs",
                    artifact_root=temporary_root / "outputs",
                    project_root=temporary_root,
                )
                self.assertIn(
                    "main.ml_models.fraud_detector", str(context.model_output_dir)
                )
        finally:
            template.MODEL_NAME = original_model_name


if __name__ == "__main__":
    unittest.main()
