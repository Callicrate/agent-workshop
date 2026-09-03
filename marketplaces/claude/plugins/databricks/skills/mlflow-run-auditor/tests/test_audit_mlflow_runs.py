"""Network-free regression coverage for the MLflow readiness gate."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "audit_mlflow_runs.py"
SPEC = importlib.util.spec_from_file_location("audit_mlflow_runs", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
AUDITOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDITOR
SPEC.loader.exec_module(AUDITOR)


class Artifact:
    def __init__(self, path: str, *, is_dir: bool = False, size: int = 1) -> None:
        self.path = path
        self.is_dir = is_dir
        self.file_size = size


class ExplosiveArtifact:
    @property
    def path(self) -> str:
        secret = "file-info-secret"
        raise RuntimeError("Authorization" + f": Bearer {secret}")

    @property
    def is_dir(self) -> bool:
        return False

    @property
    def file_size(self) -> int:
        return 1


class FakeClient:
    def __init__(
        self,
        tree: dict[str | None, list[Artifact]],
        payloads: dict[str, object],
        *,
        list_error: Exception | None = None,
        download_error: Exception | None = None,
    ) -> None:
        self.tree = tree
        self.payloads = payloads
        self.list_error = list_error
        self.download_error = download_error
        self.list_calls: list[str | None] = []

    def list_artifacts(self, _run_id: str, path: str | None = None) -> list[Artifact]:
        self.list_calls.append(path)
        if self.list_error:
            raise self.list_error
        return self.tree.get(path, [])

    def download_artifacts(self, _run_id: str, path: str, destination: str) -> str:
        if self.download_error:
            raise self.download_error
        local_path = Path(destination) / Path(path).name
        local_path.write_text(json.dumps(self.payloads[path]), encoding="utf-8")
        return str(local_path)


class FakeMlflow:
    def __init__(self, client: FakeClient, runs: list[SimpleNamespace]) -> None:
        self.client = client
        self.runs = runs
        self.calls: list[str] = []

    def set_tracking_uri(self, uri: str) -> None:
        self.calls.append(f"tracking:{uri}")

    def get_experiment_by_name(self, _name: str) -> SimpleNamespace:
        self.calls.append("experiment")
        return SimpleNamespace(experiment_id="e1", name="/Experiments/example")

    def get_experiment(self, _name: str) -> None:
        return None

    def MlflowClient(self) -> FakeClient:
        self.calls.append("client")
        return self.client

    def search_runs(self, **_kwargs: object) -> list[SimpleNamespace]:
        self.calls.append("search")
        return self.runs


def model_info(uri: str) -> SimpleNamespace:
    return SimpleNamespace(
        signature={"uri": uri},
        saved_input_example_info={
            "artifact_path": "input_example.json",
            "type": "json_object",
        },
    )


def input_example(_uri: str) -> object:
    return {}


def good_payloads(stage: str = "prototype") -> dict[str, object]:
    payloads: dict[str, object] = {
        "feature_list.json": {"features": ["feature_a"]},
        "label_mapping.json": {"ham": 0, "spam": 1},
        "metric_formulas.json": {
            "precision": {
                "canonical_metric": "precision",
                "formula": "tp/(tp+fp)",
                "class_label": "spam",
                "averaging": "binary",
                "denominator": "tp+fp",
                "threshold": 0.5,
            }
        },
        "confusion_matrix.json": [[1, 0], [0, 1]],
    }
    if stage in AUDITOR.RUN_STAGES[1:]:
        payloads.update(
            {
                "job_parameter_contract.json": {"entrypoint": "train.py", "parameters": {"at_timestamp": "2025-01-01T00:00:00Z"}},
                "job_smoke.json": {"command": "python train.py --at-timestamp 2025-01-01T00:00:00Z"},
            }
        )
    if stage in AUDITOR.RUN_STAGES[2:]:
        payloads.update(
            {
                "selected_model.json": {"objective": "roc_auc", "run_id": "run-123", "registered_model_name": "catalog.schema.model"},
                "promotion_handoff.json": {"run_id": "run-123", "registered_model_name": "catalog.schema.model"},
            }
        )
    if stage in AUDITOR.RUN_STAGES[3:]:
        payloads["serving_contract.json"] = {
            "run_id": "run-123",
            "registered_model_name": "catalog.schema.model",
            "model_uri": "models:/catalog.schema.model@champion",
            "input_schema": "feature_a: double",
            "output_schema": "score: double",
            "null_policy": "short_circuit_unscorable",
        }
    if stage == "batch-inference-dependency":
        payloads.update(
            {
                "batch_input_contract.json": {"run_id": "run-123", "registered_model_name": "catalog.schema.model", "source_table": "catalog.schema.source", "input_schema": "feature_a: double"},
                "batch_output_contract.json": {"run_id": "run-123", "registered_model_name": "catalog.schema.model", "output_schema": "prediction: int"},
            }
        )
    return payloads


def make_client(stage: str = "prototype") -> FakeClient:
    payloads = good_payloads(stage)
    root = [Artifact("models", is_dir=True)]
    root.extend(Artifact(path, size=len(json.dumps(value).encode("utf-8"))) for path, value in payloads.items())
    if stage in AUDITOR.RUN_STAGES[1:]:
        root.append(Artifact("inference_stub.py", size=10))
    tree = {
        None: list(reversed(root)),
        "models": [Artifact("models/second", is_dir=True), Artifact("models/first", is_dir=True)],
        "models/first": [Artifact("models/first/MLmodel", size=10)],
        "models/second": [Artifact("models/second/MLmodel", size=10)],
    }
    return FakeClient(tree, payloads)


def make_run(*, status: str = "FINISHED", stage: str = "prototype", params: object | None = None, metrics: object | None = None, run_id: str = "run-123") -> SimpleNamespace:
    base_params: dict[str, str] = {
        "source_table": " catalog.schema.source ",
        "dataset_version": "7",
        "experiment_path": "/Experiments/example",
        "workspace_path": "/Workspace/example",
        "registered_model_name": "catalog.schema.model",
        "train_rows": "100",
        "val_rows": "10",
        "AT_TIMESTAMP": "2025-01-01T00:00:00Z",
        "timezone": "UTC",
        "scd2_predicate": "valid_from <= at_timestamp AND (valid_to > at_timestamp OR valid_to IS NULL)",
        "TRAIN_START_OFFSET_IN_DAYS": "30",
        "TRAIN_END_OFFSET_IN_HOURS": "1",
        "null_policy": "short_circuit_unscorable",
        "skipped_null_rows": "0",
        "source_freshness_checked_at": "2025-01-02T00:00:00Z",
    }
    if stage in AUDITOR.RUN_STAGES[1:]:
        base_params.update({"entrypoint": "train.py", "job_parameters": "--at-timestamp"})
    if stage in AUDITOR.RUN_STAGES[2:]:
        base_params["selected_model_objective"] = "auc"
    if stage in AUDITOR.RUN_STAGES[3:]:
        base_params["inference_loader"] = "models:/catalog.schema.model@champion"
    return SimpleNamespace(
        info=SimpleNamespace(run_id=run_id, run_name=" example ", status=status, start_time=10),
        data=SimpleNamespace(
            params=base_params if params is None else params,
            metrics={"accuracy": 0.9, "f1": 0.8, "precision": 0.8, "recall": 0.8, "auc": 0.9} if metrics is None else metrics,
        ),
    )


EXPERIMENT = SimpleNamespace(experiment_id="e1", name="/Experiments/example")


class MlflowRunAuditorTests(unittest.TestCase):
    def audit(self, *, stage: str = "prototype", run: SimpleNamespace | None = None, client: FakeClient | None = None, **kwargs: object) -> object:
        loader = kwargs.pop("model_info_loader", model_info)
        example_loader = kwargs.pop("input_example_loader", input_example)
        return AUDITOR.audit_run(
            run or make_run(stage=stage),
            client=client or make_client(stage),
            experiment=EXPERIMENT,
            run_stage=stage,
            model_info_loader=loader,
            input_example_loader=example_loader,
            **kwargs,
        )

    def test_each_stage_is_clean_only_when_finished(self) -> None:
        for stage in AUDITOR.RUN_STAGES:
            with self.subTest(stage=stage):
                audit = self.audit(stage=stage)
                self.assertTrue(audit.complete)
                self.assertTrue(audit.is_clean)
                self.assertEqual(audit.model_uris, ["runs:/run-123/models/first", "runs:/run-123/models/second"])
        for status in ("FAILED", "RUNNING"):
            with self.subTest(status=status):
                audit = self.audit(run=make_run(status=status))
                self.assertTrue(audit.complete)
                self.assertFalse(audit.is_clean)
                envelope = AUDITOR.build_envelope(requested_count=1, audits=[audit], code_scan=AUDITOR.CodeScanResult("not_requested"))
                self.assertEqual(envelope["decision"], "findings")
                self.assertEqual(AUDITOR.exit_code_for_envelope(envelope), 2)

    def test_whitespace_nan_and_decoys_fail_safe(self) -> None:
        client = make_client()
        client.tree[None] = [entry for entry in client.tree[None] if entry.path != "metric_formulas.json"]
        client.tree[None].append(Artifact("not_metric_formulas.json", size=2))
        audit = self.audit(
            run=make_run(params={"source_table": "   "}, metrics={"auc": math.nan}),
            client=client,
        )
        self.assertFalse(audit.complete)
        self.assertIn("malformed metrics map", audit.incomplete_reasons)
        self.assertIn("missing metric_formulas JSON artifact", audit.missing_artifacts)
        self.assertFalse(audit.is_clean)

    def test_every_actual_mlmodel_is_checked(self) -> None:
        seen: list[str] = []

        def loader(uri: str) -> SimpleNamespace:
            seen.append(uri)
            return SimpleNamespace(
                signature={} if uri.endswith("first") else None,
                saved_input_example_info={"artifact_path": "input_example.json", "type": "json_object"},
            )

        audit = AUDITOR.audit_run(
            make_run(),
            client=make_client(),
            experiment=EXPERIMENT,
            run_stage="prototype",
            model_info_loader=loader,
            input_example_loader=input_example,
        )
        self.assertEqual(seen, ["runs:/run-123/models/first", "runs:/run-123/models/second"])
        self.assertIn("model signature for runs:/run-123/models/second", audit.missing_metadata)
        self.assertNotIn("models/first/not-MLmodel", audit.artifacts_present)

    def test_input_example_metadata_must_be_loadable(self) -> None:
        valid_empty_user_example = MappingProxyType(
            {"artifact_path": "input_example.json", "type": "json_object"}
        )
        self.assertTrue(
            AUDITOR.has_loadable_input_example_metadata(valid_empty_user_example)
        )
        self.assertTrue(
            self.audit(
                model_info_loader=lambda _uri: SimpleNamespace(
                    signature={}, saved_input_example_info=valid_empty_user_example
                ),
                input_example_loader=lambda _uri: {},
            ).is_clean
        )

        invalid_metadata = (
            None,
            [],
            {},
            {"type": "dataframe"},
            {"artifact_path": "   ", "type": "dataframe"},
            {"artifact_path": 1, "type": "dataframe"},
            {"artifact_path": "input_example.json"},
            {"artifact_path": "input_example.json", "type": ""},
            {"artifact_path": "input_example.json", "type": 1},
            {"artifact_path": "input_example.json", "type": "tensor"},
        )
        for metadata in invalid_metadata:
            with self.subTest(metadata=metadata):
                self.assertFalse(AUDITOR.has_loadable_input_example_metadata(metadata))
                audit = self.audit(
                    model_info_loader=lambda _uri, value=metadata: SimpleNamespace(
                        signature={}, saved_input_example_info=value
                    )
                )
                self.assertFalse(audit.is_clean)
                self.assertEqual(
                    audit.missing_metadata,
                    [
                        "input example for runs:/run-123/models/first",
                        "input example for runs:/run-123/models/second",
                    ],
                )

    def test_input_example_artifact_must_load_when_metadata_is_valid(self) -> None:
        for name, loader, expected_reason in (
            ("missing artifact", lambda _uri: (_ for _ in ()).throw(FileNotFoundError("C:/private/input_example.json")), "unable to load model input example"),
            ("loader exception", lambda _uri: (_ for _ in ()).throw(RuntimeError("C:/private/loader failure")), "unable to load model input example"),
            ("loader returns none", lambda _uri: None, "model input example loader returned no example"),
        ):
            with self.subTest(name=name):
                audit = self.audit(input_example_loader=loader)
                self.assertFalse(audit.complete)
                self.assertFalse(audit.is_clean)
                self.assertEqual(audit.incomplete_reasons, [expected_reason])
                self.assertEqual(audit.missing_metadata, ["input example could not be loaded"] * 2)
                self.assertNotIn("private", json.dumps(audit.to_dict()))

        for name, loader in (("empty object", lambda _uri: {}), ("empty list", lambda _uri: [])):
            with self.subTest(name=name):
                audit = self.audit(input_example_loader=loader)
                self.assertTrue(audit.complete)
                self.assertTrue(audit.is_clean)

    def test_input_example_loader_stays_within_artifact_inventory_bounds(self) -> None:
        client = make_client()
        client.tree["models/first"].append(
            Artifact(
                "models/first/input_example.json",
                size=AUDITOR.MAX_ARTIFACT_BYTES + 1,
            )
        )
        calls: list[str] = []
        audit = self.audit(
            client=client,
            input_example_loader=lambda uri: calls.append(uri),
        )
        self.assertEqual(calls, [])
        self.assertFalse(audit.complete)
        self.assertFalse(audit.is_clean)
        self.assertIn("artifact byte limit reached", audit.incomplete_reasons)
        self.assertIn(
            "artifact inventory limit prevents input example verification",
            audit.incomplete_reasons,
        )
        self.assertEqual(
            audit.missing_metadata,
            ["input example cannot be verified within artifact limits"],
        )

    def test_injected_mlflow_audit_does_not_create_cwd_tracking_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original_directory = Path.cwd()
            os.chdir(directory)
            try:
                fake = FakeMlflow(make_client(), [make_run()])
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = AUDITOR.main(
                        ["example", "--profile", "test-profile", "--json", "--last", "1"],
                        mlflow_module=fake,
                        model_info_loader=model_info,
                        input_example_loader=input_example,
                    )
                self.assertEqual(exit_code, 0)
                self.assertFalse((Path.cwd() / "mlflow.db").exists())
                self.assertEqual(
                    fake.calls,
                    [
                        "tracking:databricks://test-profile",
                        "experiment",
                        "client",
                        "search",
                    ],
                )
            finally:
                os.chdir(original_directory)

    def test_profile_is_required_before_mlflow_import_or_cwd_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original_directory = Path.cwd()
            imports: list[str] = []
            original_import = __import__

            def record_import(name: str, *args: object, **kwargs: object) -> object:
                imports.append(name)
                return original_import(name, *args, **kwargs)

            os.chdir(directory)
            try:
                output = io.StringIO()
                with patch("builtins.__import__", side_effect=record_import):
                    with contextlib.redirect_stdout(output):
                        exit_code = AUDITOR.main(["example", "--json"])
                self.assertEqual(exit_code, 1)
                self.assertEqual(json.loads(output.getvalue())["decision"], "operational_error")
                self.assertNotIn("mlflow", imports)
                self.assertFalse((Path.cwd() / "mlflow.db").exists())
            finally:
                os.chdir(original_directory)

    def test_profile_is_nonblank_bounded_and_control_free(self) -> None:
        for profile in (
            "",
            " \t ",
            "x" * (AUDITOR.MAX_PROFILE_LENGTH + 1),
            "test\nprofile",
            "test profile",
            "name?query",
            "name/path",
            "name#fragment",
            "name:prefix",
            "naïve",
            ".leading",
            "_leading",
            "-leading",
        ):
            with self.subTest(profile=repr(profile)):
                fake = FakeMlflow(make_client(), [])
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = AUDITOR.main(
                        ["example", "--profile", profile, "--json"],
                        mlflow_module=fake,
                    )
                self.assertEqual(exit_code, 1)
                self.assertEqual(json.loads(output.getvalue())["decision"], "operational_error")
                self.assertEqual(fake.calls, [])

        for profile in ("DEFAULT", "profile.name_with-hyphen9"):
            with self.subTest(profile=profile):
                self.assertEqual(AUDITOR._parse_profile(profile), profile)

    def test_invalid_or_unavailable_json_evidence_is_incomplete(self) -> None:
        client = make_client()
        client.payloads["feature_list.json"] = {"wrong": []}
        audit = self.audit(client=client)
        self.assertIn("invalid feature_list JSON artifact", audit.missing_artifacts)
        client = make_client()
        client.download_error = RuntimeError("Authorization" + ": Bearer very-" + "secret-value")
        audit = self.audit(client=client)
        self.assertFalse(audit.complete)
        self.assertNotIn("very-secret-value", json.dumps(audit.to_dict()))

    def test_artifact_traversal_is_iterative_deduplicated_and_bounded(self) -> None:
        normal = FakeClient(
            {None: [Artifact("b", is_dir=True), Artifact("a", is_dir=True)], "a": [Artifact("a/file", size=2)], "b": [Artifact("b/file", size=1)]},
            {},
        )
        inventory = AUDITOR.list_artifacts_recursive(normal, "run")
        self.assertTrue(inventory.complete)
        self.assertEqual([entry.path for entry in inventory.entries], ["a", "a/file", "b", "b/file"])
        self.assertEqual(normal.list_calls, [None, "a", "b"])

        cycle = FakeClient({None: [Artifact("loop", is_dir=True)], "loop": [Artifact("loop", is_dir=True)]}, {})
        inventory = AUDITOR.list_artifacts_recursive(cycle, "run")
        self.assertTrue(inventory.complete)
        self.assertIn("artifact directory cycle or duplicate ignored", inventory.warnings)

        limited = AUDITOR.list_artifacts_recursive(normal, "run", max_count=1)
        self.assertFalse(limited.complete)
        self.assertIn("artifact count limit reached", limited.incomplete_reasons)
        byte_limited = AUDITOR.list_artifacts_recursive(normal, "run", max_bytes=1)
        self.assertFalse(byte_limited.complete)
        self.assertIn("artifact byte limit reached", byte_limited.incomplete_reasons)

        errored = AUDITOR.list_artifacts_recursive(FakeClient({}, {}, list_error=RuntimeError("Bearer hidden")), "run")
        self.assertFalse(errored.complete)
        self.assertNotIn("hidden", json.dumps(errored.warnings))

    def test_code_scan_tristate_and_parameter_content(self) -> None:
        self.assertEqual(AUDITOR.scan_code_path(None, ["catalog.schema.model"]).status, "not_requested")
        self.assertEqual(AUDITOR.scan_code_path("Z:/does-not-exist", ["catalog.schema.model"]).status, "failed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module.py").write_text("MODEL = 'catalog.schema.model'\nOLD = 'catalog.old.model'", encoding="utf-8")
            scan = AUDITOR.scan_code_path(str(root), ["catalog.schema.model", "catalog.old.model"])
            self.assertEqual(scan.status, "complete")
            self.assertEqual(scan.matches["catalog.old.model"], ["module.py"])
            audit = self.audit(
                run=make_run(params={**make_run().data.params, "notes": "replaced catalog.old.model yesterday"}),
                expected_registered_model_name="catalog.schema.model",
                stale_model_names=["catalog.old.model"],
                code_scan=scan,
            )
            self.assertIn("stale model name found in MLflow parameter content", audit.registry_drift)
            self.assertIn("stale model name found in scanned code", audit.registry_drift)
            original_limit = AUDITOR.MAX_CODE_SCAN_FILES
            AUDITOR.MAX_CODE_SCAN_FILES = 1
            try:
                (root / "other.py").write_text("pass", encoding="utf-8")
                self.assertEqual(AUDITOR.scan_code_path(str(root), ["catalog.schema.model"]).status, "failed")
            finally:
                AUDITOR.MAX_CODE_SCAN_FILES = original_limit
        audit = self.audit(expected_registered_model_name="catalog.schema.model", code_scan=AUDITOR.CodeScanResult("not_requested"))
        self.assertNotIn("expected registered model name is absent from scanned code", audit.registry_drift)

    def test_malformed_maps_and_counts_cannot_clean(self) -> None:
        audit = self.audit(run=make_run(params=["bad"], metrics=["bad"]))
        self.assertFalse(audit.complete)
        self.assertFalse(audit.is_clean)
        audit = self.audit(run=make_run(metrics={"auc": "0.9"}))
        self.assertIn("malformed metrics map", audit.incomplete_reasons)
        audit = self.audit(run=make_run(params={**make_run().data.params, "train_rows": "-1"}))
        self.assertIn("train_rows must be a nonnegative integer", audit.missing_metadata)

    def test_cli_envelope_exit_codes_and_last_bounds(self) -> None:
        fake = FakeMlflow(make_client(), [])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = AUDITOR.main(["example", "--profile", "test-profile", "--json", "--last", "1"], mlflow_module=fake, model_info_loader=model_info, input_example_loader=input_example)
        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["decision"], "no_qualifying_runs")
        self.assertEqual(set(("decision", "complete", "requested_count", "found_count", "clean_count", "runs")), set(result).intersection({"decision", "complete", "requested_count", "found_count", "clean_count", "runs"}))
        for last in ("0", "1001", "not-a-number"):
            with self.subTest(last=last):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = AUDITOR.main(["example", "--profile", "test-profile", "--json", "--last", last], mlflow_module=fake)
                self.assertEqual(exit_code, 1)
                self.assertEqual(json.loads(output.getvalue())["decision"], "operational_error")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(AUDITOR.main(["example", "--json", "--help"], mlflow_module=fake), 1)
        self.assertEqual(json.loads(output.getvalue())["decision"], "operational_error")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                AUDITOR.main(
                    ["example", "--profile", "test-profile", "--json", "--code-path", "Z:/does-not-exist"],
                    mlflow_module=fake,
                ),
                1,
            )
        self.assertEqual(json.loads(output.getvalue())["decision"], "incomplete")

    def test_cli_complete_findings_and_deterministic_json(self) -> None:
        fake = FakeMlflow(make_client(), [make_run(run_id="z"), make_run(run_id="a")])
        outputs: list[str] = []
        for _ in range(2):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = AUDITOR.main(["example", "--profile", "test-profile", "--json", "--last", "2"], mlflow_module=fake, model_info_loader=model_info, input_example_loader=input_example)
            self.assertEqual(exit_code, 0)
            outputs.append(output.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(json.loads(outputs[0])["clean_count"], 2)

    def test_exception_redaction_is_central_and_bounded(self) -> None:
        secret = "do-not-show-this"
        payload = AUDITOR.redact_data({"message": "Authorization" + f": Bearer {secret}", "nested": [f"token={secret}"]})
        encoded = json.dumps(payload)
        self.assertNotIn(secret, encoded)
        self.assertIn("<redacted>", encoded)
        self.assertNotIn(secret, AUDITOR.safe_error(RuntimeError(f"Bearer {secret}"))["message"])

    def test_local_policy_rejects_typed_table_and_json_repros(self) -> None:
        table_cases = {
            "bad timestamp": ({"AT_TIMESTAMP": "2025-01-01"}, "AT_TIMESTAMP must be a fixed timezone-aware ISO timestamp"),
            "bad timezone": ({"timezone": "Mars/Olympus"}, "timezone must be an IANA timezone"),
            "offset ordering": ({"TRAIN_START_OFFSET_IN_DAYS": "1", "TRAIN_END_OFFSET_IN_HOURS": "48"}, "training window offsets are in the wrong order"),
            "validation offset ordering": ({"VAL_START_OFFSET_IN_DAYS": "0", "VAL_END_OFFSET_IN_HOURS": "1"}, "validation window offsets are in the wrong order"),
            "bad null policy": ({"null_policy": "best_effort"}, "null policy is outside the local readiness policy"),
            "bad table": ({"source_table": "schema.table"}, "source table is not a local three-part identifier"),
        }
        base = make_run().data.params
        for name, (override, finding) in table_cases.items():
            with self.subTest(name=name):
                audit = self.audit(run=make_run(params={**base, **override}))
                self.assertIn(finding, audit.data_semantics_risk)
                self.assertFalse(audit.is_clean)

        invalid_json = {
            "label_mapping.json": {"ham": 1, "spam": 1},
            "metric_formulas.json": {"precision": {"canonical_metric": "precision", "formula": "x", "class_label": "spam", "averaging": "bad", "denominator": "d", "threshold": 2.0}},
            "confusion_matrix.json": [[1, 0, 0], [0, 1, 0]],
        }
        for path, payload in invalid_json.items():
            with self.subTest(path=path):
                client = make_client()
                client.payloads[path] = payload
                audit = self.audit(client=client)
                requirement = {"label_mapping.json": "label_map", "metric_formulas.json": "metric_formulas", "confusion_matrix.json": "confusion_matrix"}[path]
                self.assertIn(f"invalid {requirement} JSON artifact", audit.missing_artifacts)

        client = make_client()
        client.payloads["feature_list.json"] = {"features": [float("nan")]}
        audit = self.audit(client=client)
        self.assertFalse(audit.complete)
        self.assertIn("unable to inspect feature_list JSON artifact", audit.incomplete_reasons)

    def test_handoff_artifacts_bind_to_run_model_and_table(self) -> None:
        cases = (
            ("promotion-candidate", "promotion_handoff.json", "run_id", "wrong-run"),
            ("serving-candidate", "serving_contract.json", "model_uri", "models:/catalog.other.model@champion"),
            ("batch-inference-dependency", "batch_input_contract.json", "source_table", "catalog.schema.other"),
        )
        for stage, path, key, value in cases:
            with self.subTest(stage=stage, path=path):
                client = make_client(stage)
                client.payloads[path][key] = value
                audit = self.audit(stage=stage, client=client)
                requirement = {"promotion_handoff.json": "promotion_handoff", "serving_contract.json": "serving_contract", "batch_input_contract.json": "batch_input_contract"}[path]
                self.assertIn(f"{requirement} JSON artifact identity does not match the logged run", audit.inconsistent_values)
                self.assertFalse(audit.is_clean)

    def test_serving_model_identity_uses_structural_exact_uri_matching(self) -> None:
        client = make_client("serving-candidate")
        client.payloads["serving_contract.json"]["model_uri"] = "models:/catalog.schema.model-evil@champion"
        audit = self.audit(stage="serving-candidate", client=client)
        self.assertIn("serving_contract JSON artifact identity does not match the logged run", audit.inconsistent_values)
        self.assertFalse(audit.is_clean)

        params = dict(make_run(stage="serving-candidate").data.params)
        params["inference_loader"] = "models:/catalog.schema.model-evil@champion"
        audit = self.audit(stage="serving-candidate", run=make_run(stage="serving-candidate", params=params), client=make_client("serving-candidate"))
        self.assertIn("inference loader model name does not exactly match the logged registered model", audit.registry_drift)
        self.assertFalse(audit.is_clean)

        for uri in ("models:/catalog.schema.model@candidate", "models:/catalog.schema.model/7"):
            with self.subTest(uri=uri):
                client = make_client("serving-candidate")
                client.payloads["serving_contract.json"]["model_uri"] = uri
                params = dict(make_run(stage="serving-candidate").data.params)
                params["inference_loader"] = uri
                audit = self.audit(stage="serving-candidate", run=make_run(stage="serving-candidate", params=params), client=client)
                self.assertTrue(audit.is_clean)

    def test_job_requirements_use_the_job_readiness_category(self) -> None:
        params = dict(make_run(stage="job-ready-training").data.params)
        del params["entrypoint"]
        audit = self.audit(stage="job-ready-training", run=make_run(stage="job-ready-training", params=params))
        self.assertIn("missing entrypoint", audit.job_readiness_risk)
        self.assertIn("job entrypoint parameter contract and smoke evidence", audit.recommended_patch_location)

    def test_envelope_contains_property_and_run_failures_without_traceback(self) -> None:
        client = FakeClient({None: [ExplosiveArtifact()]}, {})
        audit = self.audit(client=client)
        self.assertFalse(audit.complete)
        self.assertNotIn("file-info-secret", json.dumps(audit.to_dict()))

        class ExplosiveRun:
            @property
            def info(self) -> object:
                secret = "run-object-secret"
                raise RuntimeError(f"Bearer {secret}")

        fake = FakeMlflow(make_client(), [make_run(), ExplosiveRun()])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(AUDITOR.main(["example", "--profile", "test-profile", "--json", "--last", "2"], mlflow_module=fake, model_info_loader=model_info, input_example_loader=input_example), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["decision"], "incomplete")
        self.assertEqual(result["found_count"], 2)
        self.assertNotIn("run-object-secret", output.getvalue())

    def test_recursive_mapping_and_uri_redaction(self) -> None:
        secret = "secret-value-never-output"
        payload = AUDITOR.redact_data(
            MappingProxyType({
                "nested": {
                    "Authorization" + f": Bearer {secret}": "safe",
                    "reader-url": "https://reader@example.invalid/model",
                    "writer-url": "https://writer:password@example.invalid/model",
                }
            })
        )
        encoded = json.dumps(payload)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("reader@", encoded)
        self.assertNotIn("writer:password@", encoded)
        self.assertIn("https://<redacted>@example.invalid", encoded)

    def test_adversarial_iterators_are_bounded_before_materialization(self) -> None:
        consumed = 0

        def endless_artifacts() -> object:
            nonlocal consumed
            while True:
                consumed += 1
                yield Artifact(f"item-{consumed}")

        inventory = AUDITOR.list_artifacts_recursive(FakeClient({None: endless_artifacts()}, {}), "run", max_count=1)
        self.assertFalse(inventory.complete)
        self.assertIn("artifact count limit reached", inventory.incomplete_reasons)
        self.assertEqual(consumed, 2)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumed_paths = 0

            def endless_paths(_self: Path, _pattern: str) -> object:
                nonlocal consumed_paths
                while True:
                    consumed_paths += 1
                    yield root / f"missing-{consumed_paths}.txt"

            original_limit = AUDITOR.MAX_CODE_SCAN_WALK_ENTRIES
            AUDITOR.MAX_CODE_SCAN_WALK_ENTRIES = 1
            try:
                with patch.object(AUDITOR.Path, "rglob", endless_paths):
                    scan = AUDITOR.scan_code_path(str(root), ["catalog.schema.model"])
            finally:
                AUDITOR.MAX_CODE_SCAN_WALK_ENTRIES = original_limit
            self.assertEqual(scan.status, "failed")
            self.assertEqual(consumed_paths, 2)


if __name__ == "__main__":
    unittest.main()
