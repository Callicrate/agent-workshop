"""Offline behavior tests for bounded runtime and notebook diagnostics."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import check_notebook_runtime_order as notebook_check  # noqa: E402
import collect_env_snapshot as snapshot  # noqa: E402
import probe_training_arguments as training_args_probe  # noqa: E402
from runtime_safety import redact_structure, redact_text  # noqa: E402

TEST_DAPI = "dapi" + ("0" * 16)


class FakeProcess:
    """Minimal bounded-process fake that can model a process needing termination."""

    def __init__(self, stdout: bytes, stderr: bytes, *, running: bool = False) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode: int | None = None if running else 0
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


class RuntimeSnapshotTests(unittest.TestCase):
    """Probe contracts that must remain offline, bounded, and redacted."""

    def test_redactor_covers_nested_runtime_fields(self) -> None:
        secret = TEST_DAPI
        payload = {
            "source_note": f"Bearer {secret}\nnext",
            "env": {"CACHE": f"token={secret}"},
            "workspace": f"https://alice:{secret}@workspace.example/?api_key={secret}",
            "probe": {"errors": [f"Authorization: Basic {secret}"]},
            "nvidia": {"stderr": f"eyJabcdefgh.abcdefgh.abcdefgh {secret}"},
        }
        rendered = json.dumps(redact_structure(payload))
        self.assertNotIn(secret, rendered)
        self.assertNotIn("alice:", rendered)
        self.assertNotIn("\n", redact_text("line-one\nline-two"))

    def test_package_probe_rejects_dotted_names_without_find_spec(self) -> None:
        with mock.patch.object(snapshot.importlib.util, "find_spec") as find_spec:
            result = snapshot.get_pkg_version("pkg.parent")
        self.assertEqual(result["status"], "invalid")
        find_spec.assert_not_called()

    def test_package_identifier_length_is_checked_before_probe(self) -> None:
        allowed = "a" * 96
        rejected = "a" * 97
        with mock.patch.object(
            snapshot.importlib.util, "find_spec", return_value=None
        ) as find_spec:
            accepted_result = snapshot.get_pkg_version(allowed, {})
            rejected_result = snapshot.get_pkg_version(rejected, {})
        self.assertEqual(accepted_result["module"], allowed)
        self.assertEqual(rejected_result["status"], "invalid")
        self.assertEqual(find_spec.call_args_list[0].args[0], allowed)
        self.assertEqual(len(find_spec.call_args_list), 1)

    def test_package_distribution_scan_happens_once(self) -> None:
        calls = 0

        def distributions() -> dict[str, list[str]]:
            nonlocal calls
            calls += 1
            return {}

        args = SimpleNamespace(
            packages="valid_module,pkg.parent",
            nltk_data=None,
            source="local",
            source_note=None,
            nvidia_smi_timeout=1,
        )
        with (
            mock.patch.object(
                snapshot.importlib.metadata,
                "packages_distributions",
                side_effect=distributions,
            ),
            mock.patch.object(
                snapshot.importlib.util, "find_spec", return_value=None
            ) as find_spec,
            mock.patch.object(
                snapshot,
                "get_cuda_info",
                return_value={
                    "probe_status": "complete",
                    "devices": [],
                    "device_count": 0,
                },
            ),
            mock.patch.object(
                snapshot, "get_nvidia_smi", return_value={"available": False}
            ),
        ):
            result = snapshot.build_snapshot(args)
        self.assertEqual(calls, 1)
        self.assertEqual(result["packages"]["pkg.parent"]["status"], "invalid")
        self.assertTrue(
            all("." not in call.args[0] for call in find_spec.call_args_list)
        )

    def test_default_snapshot_does_not_import_transformers(self) -> None:
        args = SimpleNamespace(
            packages=None,
            nltk_data=None,
            source="local",
            source_note=None,
            nvidia_smi_timeout=1,
        )
        with (
            mock.patch.object(snapshot, "get_dbr_version", return_value="unknown"),
            mock.patch.object(snapshot, "get_cuda_info", return_value={}),
            mock.patch.object(snapshot, "get_nvidia_smi", return_value={}),
            mock.patch.object(snapshot.importlib, "import_module") as importer,
        ):
            snapshot.build_snapshot(args)
        importer.assert_not_called()

    def test_training_arguments_probe_reports_signature_evidence_without_construction(self) -> None:
        constructed = False

        class CurrentTrainingArguments:
            def __init__(
                self,
                output_dir: str,
                *,
                eval_strategy: str = "no",
                logging_first_step: bool = False,
            ) -> None:
                nonlocal constructed
                constructed = True
                raise AssertionError("the probe must not construct TrainingArguments")

        module = SimpleNamespace(
            __version__="4.51.3",
            TrainingArguments=CurrentTrainingArguments,
        )
        result = training_args_probe.probe_training_arguments(
            ["evaluation_strategy", "eval_strategy", "logging_first_step", "unrelated_option"],
            module_loader=lambda _name: module,
        )
        self.assertTrue(result["complete"])
        self.assertEqual(result["package"]["version"], "4.51.3")
        self.assertEqual(result["requested_fields"]["eval_strategy"], "supported")
        self.assertEqual(result["requested_fields"]["evaluation_strategy"], "unsupported")
        self.assertEqual(result["requested_fields"]["logging_first_step"], "supported")
        self.assertEqual(result["requested_fields"]["unrelated_option"], "unsupported")
        self.assertFalse(constructed)

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = training_args_probe.main(
                ["--fields", "eval_strategy,logging_first_step"],
                module_loader=lambda _name: module,
            )
        emitted = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(emitted["package"]["version"], "4.51.3")
        self.assertEqual(emitted["requested_fields"]["logging_first_step"], "supported")

    def test_training_arguments_probe_handles_aliases_and_incomplete_signatures(self) -> None:
        class OldTrainingArguments:
            def __init__(self, *, evaluation_strategy: str = "no") -> None:
                raise AssertionError("the probe must not construct TrainingArguments")

        class BothTrainingArguments:
            def __init__(
                self,
                *,
                eval_strategy: str = "no",
                evaluation_strategy: str = "no",
            ) -> None:
                raise AssertionError("the probe must not construct TrainingArguments")

        class NeitherTrainingArguments:
            def __init__(self, *, output_dir: str = "output") -> None:
                raise AssertionError("the probe must not construct TrainingArguments")

        class VariadicTrainingArguments:
            def __init__(self, **_kwargs: object) -> None:
                raise AssertionError("the probe must not construct TrainingArguments")

        class ExplicitAliasVariadicTrainingArguments:
            def __init__(self, *, eval_strategy: str = "no", **_kwargs: object) -> None:
                raise AssertionError("the probe must not construct TrainingArguments")

        requested = ["eval_strategy", "evaluation_strategy", "logging_first_step", "unrelated_option"]
        cases = (
            ("old", OldTrainingArguments, "evaluation_strategy", "unsupported"),
            ("both", BothTrainingArguments, "eval_strategy", "unsupported"),
        )
        for name, training_arguments, expected_key, unrelated_support in cases:
            with self.subTest(name=name):
                module = SimpleNamespace(
                    __version__="test-version",
                    TrainingArguments=training_arguments,
                )
                result = training_args_probe.probe_training_arguments(
                    requested,
                    module_loader=lambda _name, loaded=module: loaded,
                )
                self.assertEqual(
                    training_args_probe.select_evaluation_strategy_key(result),
                    expected_key,
                )
                self.assertEqual(
                    result["requested_fields"]["unrelated_option"], unrelated_support
                )
                self.assertTrue(result["complete"])

        for name, training_arguments, expected_message, unrelated_support, expected_complete in (
            (
                "neither",
                NeitherTrainingArguments,
                "supports neither",
                "unsupported",
                True,
            ),
            (
                "variadic",
                VariadicTrainingArguments,
                "support is unknown",
                "unknown",
                False,
            ),
            (
                "explicit alias plus variadic",
                ExplicitAliasVariadicTrainingArguments,
                "support is unknown",
                "unknown",
                False,
            ),
        ):
            with self.subTest(name=name):
                module = SimpleNamespace(
                    __version__="test-version",
                    TrainingArguments=training_arguments,
                )
                result = training_args_probe.probe_training_arguments(
                    requested,
                    module_loader=lambda _name, loaded=module: loaded,
                )
                with self.assertRaisesRegex(RuntimeError, expected_message):
                    training_args_probe.select_evaluation_strategy_key(result)
                self.assertEqual(
                    result["requested_fields"]["unrelated_option"], unrelated_support
                )
                self.assertEqual(result["complete"], expected_complete)

        for receipt in (
            {
                "complete": False,
                "signature": {
                    "status": "explicit",
                    "accepts_variadic_keywords": False,
                },
                "requested_fields": {"eval_strategy": "supported"},
            },
            {
                "complete": True,
                "signature": None,
                "requested_fields": {"eval_strategy": "supported"},
            },
        ):
            with self.subTest(receipt=receipt):
                with self.assertRaisesRegex(RuntimeError, "support is unknown"):
                    training_args_probe.select_evaluation_strategy_key(receipt)

        receipt_base = {
            "complete": True,
            "signature": {
                "status": "explicit",
                "accepts_variadic_keywords": False,
            },
        }
        for name, requested_fields, expected_message in (
            (
                "aliases unrequested",
                {"logging_first_step": "supported"},
                "support is unknown",
            ),
            (
                "one alias unrequested",
                {"eval_strategy": "unsupported"},
                "support is unknown",
            ),
            (
                "malformed alias state",
                {
                    "eval_strategy": "bogus",
                    "evaluation_strategy": "unsupported",
                },
                "support is unknown",
            ),
            (
                "unknown alias state",
                {
                    "eval_strategy": "unknown",
                    "evaluation_strategy": "unsupported",
                },
                "support is unknown",
            ),
            (
                "both aliases explicitly unsupported",
                {
                    "eval_strategy": "unsupported",
                    "evaluation_strategy": "unsupported",
                },
                "supports neither",
            ),
        ):
            with self.subTest(name=name):
                receipt = {**receipt_base, "requested_fields": requested_fields}
                with self.assertRaisesRegex(RuntimeError, expected_message):
                    training_args_probe.select_evaluation_strategy_key(receipt)

        for malformed_state in ([], {}, None, False, 0, 1.0):
            with self.subTest(malformed_state=repr(malformed_state)):
                receipt = {
                    **receipt_base,
                    "requested_fields": {
                        "eval_strategy": malformed_state,
                        "evaluation_strategy": "unsupported",
                    },
                }
                with self.assertRaisesRegex(RuntimeError, "support is unknown"):
                    training_args_probe.select_evaluation_strategy_key(receipt)

    def test_training_arguments_probe_reports_missing_import_and_opaque_signature(self) -> None:
        requested = ["eval_strategy", "unrelated_option"]
        missing = training_args_probe.probe_training_arguments(
            requested,
            module_loader=lambda _name: (_ for _ in ()).throw(ModuleNotFoundError("transformers")),
        )
        self.assertFalse(missing["complete"])
        self.assertEqual(missing["package"]["loaded"], False)
        self.assertEqual(set(missing["requested_fields"].values()), {"unknown"})

        failed = training_args_probe.probe_training_arguments(
            requested,
            module_loader=lambda _name: (_ for _ in ()).throw(
                RuntimeError(f"Bearer {TEST_DAPI}")
            ),
        )
        self.assertFalse(failed["complete"])
        self.assertEqual(failed["errors"][0]["error_type"], "RuntimeError")
        self.assertNotIn(TEST_DAPI, json.dumps(failed))

        module = SimpleNamespace(__version__="test-version", TrainingArguments=object)
        opaque = training_args_probe.probe_training_arguments(
            requested,
            module_loader=lambda _name: module,
            signature_inspector=lambda _target: (_ for _ in ()).throw(ValueError("opaque")),
        )
        self.assertFalse(opaque["complete"])
        self.assertEqual(opaque["signature"]["status"], "opaque")
        self.assertEqual(set(opaque["requested_fields"].values()), {"unknown"})

    def test_training_arguments_probe_help_does_not_import_transformers(self) -> None:
        with mock.patch.object(training_args_probe.importlib, "import_module") as importer:
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
                training_args_probe.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        importer.assert_not_called()

    def test_nltk_import_failure_is_structured_and_redacted(self) -> None:
        with mock.patch.object(
            snapshot.importlib,
            "import_module",
            side_effect=RuntimeError(f"Bearer {TEST_DAPI}"),
        ):
            result = snapshot.check_nltk_resources(["punkt"])
        entry = result["punkt"]
        self.assertEqual(entry["status"], "error")
        self.assertEqual(entry["error_type"], "RuntimeError")
        self.assertNotIn("dapi", json.dumps(entry))

    def test_nltk_resource_failure_is_not_mislabeled_as_missing(self) -> None:
        nltk = SimpleNamespace(
            data=SimpleNamespace(
                find=mock.Mock(side_effect=RuntimeError(f"Bearer {TEST_DAPI}"))
            )
        )
        with mock.patch.object(snapshot.importlib, "import_module", return_value=nltk):
            result = snapshot.check_nltk_resources(["punkt"])
        entry = result["punkt"]
        self.assertEqual(entry["status"], "error")
        self.assertEqual(entry["error_type"], "RuntimeError")
        self.assertNotIn("dapi", json.dumps(entry))

    def test_cuda_complete_partial_and_error_states(self) -> None:
        class CompleteCuda:
            @staticmethod
            def is_available() -> bool:
                return True

            @staticmethod
            def device_count() -> int:
                return 2

            @staticmethod
            def get_device_name(index: int) -> str:
                return f"GPU-{index}"

            @staticmethod
            def get_device_capability(index: int) -> tuple[int, int]:
                return (8, 0)

        complete_torch = SimpleNamespace(
            cuda=CompleteCuda(), version=SimpleNamespace(cuda="12.6")
        )
        with mock.patch.object(
            snapshot.importlib, "import_module", return_value=complete_torch
        ):
            complete = snapshot.get_cuda_info()
        self.assertEqual(complete["probe_status"], "complete")
        self.assertEqual(complete["device_count"], len(complete["devices"]))

        class PartialCuda(CompleteCuda):
            @staticmethod
            def get_device_name(index: int) -> str:
                if index == 1:
                    raise RuntimeError(f"Bearer {TEST_DAPI}")
                return "GPU-0"

        partial_torch = SimpleNamespace(
            cuda=PartialCuda(), version=SimpleNamespace(cuda="12.6")
        )
        with mock.patch.object(
            snapshot.importlib, "import_module", return_value=partial_torch
        ):
            partial = snapshot.get_cuda_info()
        self.assertEqual(partial["probe_status"], "partial")
        self.assertEqual(partial["devices"][1]["status"], "error")
        self.assertNotIn("dapi", json.dumps(partial))

        with mock.patch.object(
            snapshot.importlib,
            "import_module",
            side_effect=OSError(f"token={TEST_DAPI}"),
        ):
            failed = snapshot.get_cuda_info()
        self.assertEqual(failed["probe_status"], "error")
        self.assertEqual(failed["device_count"], len(failed["devices"]))

        class UnavailableButCountedCuda(CompleteCuda):
            @staticmethod
            def is_available() -> bool:
                return False

        unavailable_torch = SimpleNamespace(
            cuda=UnavailableButCountedCuda(), version=SimpleNamespace(cuda="12.6")
        )
        with mock.patch.object(
            snapshot.importlib, "import_module", return_value=unavailable_torch
        ):
            unavailable = snapshot.get_cuda_info()
        self.assertEqual(unavailable["probe_status"], "partial")
        self.assertEqual(unavailable["error_type"], "CudaInvariantError")
        self.assertFalse(unavailable["cuda_available"])
        self.assertEqual(unavailable["device_count"], 0)
        self.assertEqual(unavailable["devices"], [])

        class AvailableWithoutDevicesCuda(CompleteCuda):
            @staticmethod
            def device_count() -> int:
                return 0

        zero_device_torch = SimpleNamespace(
            cuda=AvailableWithoutDevicesCuda(), version=SimpleNamespace(cuda="12.6")
        )
        with mock.patch.object(
            snapshot.importlib, "import_module", return_value=zero_device_torch
        ):
            zero_device = snapshot.get_cuda_info()
        self.assertEqual(zero_device["probe_status"], "partial")
        self.assertEqual(zero_device["error_type"], "CudaInvariantError")
        for result in (complete, partial, failed, unavailable, zero_device):
            self.assertEqual(result["device_count"], len(result["devices"]))
            if not result["cuda_available"]:
                self.assertEqual(result["devices"], [])

    def test_timeout_bounds_and_fixed_nvidia_launch_shape(self) -> None:
        parser = snapshot.build_parser()
        self.assertEqual(
            parser.parse_args(["--nvidia-smi-timeout", "1"]).nvidia_smi_timeout, 1
        )
        self.assertEqual(
            parser.parse_args(["--nvidia-smi-timeout", "120"]).nvidia_smi_timeout, 120
        )
        for invalid in ("0", "121", "one"):
            with (
                io.StringIO() as stderr,
                redirect_stderr(stderr),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args(["--nvidia-smi-timeout", invalid])

        process = FakeProcess(
            f"GPU Bearer {TEST_DAPI}".encode(), f"token={TEST_DAPI}".encode()
        )
        with (
            mock.patch.object(
                snapshot.shutil, "which", return_value="C:/fake/nvidia-smi"
            ),
            mock.patch.object(
                snapshot.subprocess, "Popen", return_value=process
            ) as popen,
        ):
            result = snapshot.get_nvidia_smi(1)
        kwargs = popen.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertIs(kwargs["stdin"], snapshot.subprocess.DEVNULL)
        self.assertEqual(
            {key: kwargs[key] for key in snapshot.process_tree_popen_kwargs()},
            snapshot.process_tree_popen_kwargs(),
        )
        self.assertNotIn("dapi", json.dumps(result))

    def test_cli_argument_errors_do_not_reflect_secret_values(self) -> None:
        secret_value = f"Bearer {TEST_DAPI}\nsource=secret"
        for parser, arguments in (
            (snapshot.build_parser(), ["--source", secret_value]),
            (snapshot.build_parser(), ["--packages", secret_value]),
            (notebook_check.build_parser(), ["--json", "--source", secret_value]),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                parser.parse_args(arguments)
            self.assertEqual(raised.exception.code, 2)
            rendered = stderr.getvalue()
            self.assertIn("invalid_cli_argument", rendered)
            self.assertNotIn(TEST_DAPI, rendered)
            self.assertNotIn("Bearer", rendered)
            self.assertNotIn("source=secret", rendered)

    def test_json_and_human_cli_stderr_are_value_free(self) -> None:
        secret_value = f"Bearer {TEST_DAPI}\nsource=secret"
        commands = (
            (SCRIPTS / "collect_env_snapshot.py", ["--source", secret_value]),
            (SCRIPTS / "collect_env_snapshot.py", ["--packages", secret_value]),
            (SCRIPTS / "check_notebook_runtime_order.py", ["--unknown", secret_value]),
        )
        for script, arguments in commands:
            result = subprocess.run(
                [sys.executable, str(script), *arguments],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "error: invalid_cli_argument\n")

    def test_nvidia_overflow_terminates_and_marks_output_limited(self) -> None:
        process = FakeProcess(b"x" * 100, b"", running=True)
        with (
            mock.patch.object(
                snapshot.shutil, "which", return_value="C:/fake/nvidia-smi"
            ),
            mock.patch.object(snapshot.subprocess, "Popen", return_value=process),
            mock.patch.object(snapshot, "MAX_NVIDIA_OUTPUT_BYTES", 8),
        ):
            result = snapshot.get_nvidia_smi(1)
        self.assertTrue(result["output_limited"])
        self.assertTrue(process.terminated)
        self.assertEqual(result["error_type"], "OutputLimitExceeded")
        self.assertLessEqual(len(result["stdout"]), 8)

    def test_nvidia_overflow_detects_live_pipe_drain(self) -> None:
        release = threading.Event()

        class BlockingStream:
            def read(self, size: int) -> bytes:
                release.wait(timeout=1)
                return b""

            def close(self) -> None:
                return None

        process = FakeProcess(b"x" * 100, b"", running=True)
        process.stderr = BlockingStream()
        try:
            with (
                mock.patch.object(
                    snapshot.shutil, "which", return_value="C:/fake/nvidia-smi"
                ),
                mock.patch.object(snapshot.subprocess, "Popen", return_value=process),
                mock.patch.object(snapshot, "MAX_NVIDIA_OUTPUT_BYTES", 8),
                mock.patch.object(snapshot, "DRAIN_JOIN_SECONDS", 0.01),
            ):
                result = snapshot.get_nvidia_smi(1)
        finally:
            release.set()
        self.assertTrue(result["output_limited"])
        self.assertTrue(result["drain_threads_alive"])
        self.assertTrue(result["cleanup_incomplete"])

    def test_nvidia_timeout_and_nonzero_exit_are_structured(self) -> None:
        timeout_process = FakeProcess(b"", b"", running=True)
        with (
            mock.patch.object(
                snapshot.shutil, "which", return_value="C:/fake/nvidia-smi"
            ),
            mock.patch.object(
                snapshot.subprocess, "Popen", return_value=timeout_process
            ),
            mock.patch.object(snapshot.time, "monotonic", side_effect=[0, 2]),
            mock.patch.object(snapshot.time, "sleep"),
        ):
            timeout_result = snapshot.get_nvidia_smi(1)
        self.assertTrue(timeout_result["timed_out"])
        self.assertTrue(timeout_process.terminated)
        self.assertEqual(timeout_result["error_type"], "TimeoutExpired")

        nonzero_process = FakeProcess(b"", f"Bearer {TEST_DAPI}".encode())
        nonzero_process.returncode = 1
        with (
            mock.patch.object(
                snapshot.shutil, "which", return_value="C:/fake/nvidia-smi"
            ),
            mock.patch.object(
                snapshot.subprocess, "Popen", return_value=nonzero_process
            ),
        ):
            nonzero_result = snapshot.get_nvidia_smi(1)
        self.assertEqual(nonzero_result["error_type"], "SubprocessError")
        self.assertNotIn("dapi", json.dumps(nonzero_result))

    def test_nvidia_timeout_detects_descendant_marker(self) -> None:
        process = FakeProcess(b"", b"", running=True)
        with (
            mock.patch.object(
                snapshot.shutil, "which", return_value="C:/fake/nvidia-smi"
            ),
            mock.patch.object(snapshot.subprocess, "Popen", return_value=process),
            mock.patch.object(snapshot, "process_tree_alive", return_value=True),
            mock.patch.object(snapshot.time, "monotonic", side_effect=[0, 2]),
            mock.patch.object(snapshot.time, "sleep"),
        ):
            timeout_result = snapshot.get_nvidia_smi(1)
        self.assertTrue(timeout_result["timed_out"])
        self.assertTrue(timeout_result["descendants_alive"])
        self.assertTrue(timeout_result["cleanup_incomplete"])
        self.assertEqual(timeout_result["error_type"], "ProcessCleanupError")

    def test_nvidia_unconfirmed_cleanup_is_explicit(self) -> None:
        class CannotStopProcess(FakeProcess):
            def terminate(self) -> None:
                raise OSError(f"Bearer {TEST_DAPI}")

        process = CannotStopProcess(b"", b"", running=True)
        with (
            mock.patch.object(
                snapshot.shutil, "which", return_value="C:/fake/nvidia-smi"
            ),
            mock.patch.object(snapshot.subprocess, "Popen", return_value=process),
            mock.patch.object(snapshot.time, "monotonic", side_effect=[0, 2]),
            mock.patch.object(snapshot.time, "sleep"),
        ):
            result = snapshot.get_nvidia_smi(1)
        self.assertTrue(result["cleanup_incomplete"])
        self.assertEqual(result["error_type"], "ProcessCleanupError")
        self.assertNotIn("dapi", json.dumps(result))

    def test_nvidia_launch_failure_does_not_expose_exception_text(self) -> None:
        with (
            mock.patch.object(
                snapshot.shutil, "which", return_value="C:/fake/nvidia-smi"
            ),
            mock.patch.object(
                snapshot.subprocess, "Popen", side_effect=OSError(f"token={TEST_DAPI}")
            ),
        ):
            result = snapshot.get_nvidia_smi(1)
        self.assertEqual(result["error_type"], "OSError")
        self.assertNotIn("dapi", json.dumps(result))

        with (
            mock.patch.object(
                snapshot.shutil, "which", return_value="C:/fake/nvidia-smi"
            ),
            mock.patch.object(
                snapshot.subprocess,
                "Popen",
                side_effect=subprocess.SubprocessError(f"Bearer {TEST_DAPI}"),
            ),
        ):
            subprocess_result = snapshot.get_nvidia_smi(1)
        self.assertEqual(subprocess_result["error_type"], "SubprocessError")
        self.assertNotIn("dapi", json.dumps(subprocess_result))

    def test_nvidia_drain_failure_is_structured(self) -> None:
        class BrokenStream:
            def read(self, size: int) -> bytes:
                raise RuntimeError(f"Bearer {TEST_DAPI}")

            def close(self) -> None:
                return None

        process = FakeProcess(b"", b"")
        process.stdout = BrokenStream()
        with (
            mock.patch.object(
                snapshot.shutil, "which", return_value="C:/fake/nvidia-smi"
            ),
            mock.patch.object(snapshot.subprocess, "Popen", return_value=process),
        ):
            result = snapshot.get_nvidia_smi(1)
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertNotIn("dapi", json.dumps(result))


class NotebookRuntimeOrderTests(unittest.TestCase):
    """Notebook success, warning, and hostile-input CLI behavior."""

    def write_notebook(self, directory: Path, name: str, payload: object) -> Path:
        path = directory / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_pass_and_warning_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            good = self.write_notebook(
                directory,
                "good.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": ["import math\n", "math.sqrt(4)\n"],
                        }
                    ]
                },
            )
            warn = self.write_notebook(
                directory,
                "warn.ipynb",
                {"cells": [{"cell_type": "code", "source": "%pip install demo\n"}]},
            )
            self.assertEqual(notebook_check.check_notebook(good), [])
            self.assertTrue(
                any(
                    issue.severity == "warning"
                    for issue in notebook_check.check_notebook(warn)
                )
            )

    def test_all_supported_mixed_install_lines_accept_same_cell_restart(self) -> None:
        prefixes = (
            "%pip install demo",
            "!pip install demo",
            "pip install demo",
            "python -m pip install demo",
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for index, install in enumerate(prefixes):
                with self.subTest(install=install):
                    notebook = self.write_notebook(
                        directory,
                        f"mixed-{index}.ipynb",
                        {
                            "cells": [
                                {
                                    "cell_type": "code",
                                    "source": (
                                        f"{install}\n"
                                        "dbutils.library.restartPython()\n"
                                    ),
                                }
                            ]
                        },
                    )
                    self.assertEqual(notebook_check.check_notebook(notebook), [])

    def test_python_before_mixed_install_keeps_late_install_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "late-mixed-install.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "setup_value = 1\n%pip install demo\n",
                        },
                        {
                            "cell_type": "code",
                            "source": "dbutils.library.restartPython()\n",
                        },
                    ]
                },
            )
            issues = notebook_check.check_notebook(notebook)

        self.assertTrue(
            any(
                issue.severity == "error"
                and issue.cell == 1
                and "%pip or pip install appears after imports or executable code"
                in issue.message
                for issue in issues
            )
        )
        self.assertFalse(
            any("without a later Python restart" in issue.message for issue in issues)
        )

    def test_pip_text_in_python_strings_and_comments_is_inert(self) -> None:
        controls = {
            "ordinary": "payload = '%pip install demo'\n",
            "triple": 'payload = """\n%pip install demo\n"""\n',
            "raw": 'payload = r"""\n%pip install demo\n"""\n',
            "f_string": 'payload = f"""\n%pip install demo\n"""\n',
            "comment": "# %pip install demo\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, prefix in controls.items():
                with self.subTest(name=name):
                    notebook = self.write_notebook(
                        directory,
                        f"inert-{name}.ipynb",
                        {
                            "cells": [
                                {
                                    "cell_type": "code",
                                    "source": (
                                        f"{prefix}run()\ndef run():\n    return 1\n"
                                    ),
                                }
                            ]
                        },
                    )
                    issues = notebook_check.check_notebook(notebook)
                    self.assertTrue(
                        any(
                            issue.severity == "error"
                            and "definition for 'run' appears after first use"
                            in issue.message
                            for issue in issues
                        )
                    )
                    self.assertFalse(
                        any(
                            "without a later Python restart" in issue.message
                            for issue in issues
                        )
                    )

    def test_multiline_string_closing_on_candidate_line_remains_inert(self) -> None:
        prefixes = {
            "ordinary": 'payload = """\n%pip install demo"""\n',
            "raw": 'payload = r"""\n%pip install demo"""\n',
            "f_string": 'payload = f"""\n%pip install demo"""\n',
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, prefix in prefixes.items():
                with self.subTest(name=name):
                    notebook = self.write_notebook(
                        directory,
                        f"closing-string-{name}.ipynb",
                        {
                            "cells": [
                                {
                                    "cell_type": "code",
                                    "source": (
                                        f"{prefix}run()\ndef run():\n    return 1\n"
                                    ),
                                }
                            ]
                        },
                    )
                    issues = notebook_check.check_notebook(notebook)

                    self.assertTrue(
                        any(
                            issue.severity == "error"
                            and "definition for 'run' appears after first use"
                            in issue.message
                            for issue in issues
                        )
                    )
                    self.assertFalse(
                        any(
                            issue.error_type == "UnsupportedCellSyntax"
                            or "without a later Python restart" in issue.message
                            for issue in issues
                        )
                    )

    def test_real_install_quoting_cannot_hide_command_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            quoted = self.write_notebook(
                directory,
                "quoted-install.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": (
                                '%pip install "demo"\n'
                                "dbutils.library.restartPython()\n"
                            ),
                        }
                    ]
                },
            )
            conservative = self.write_notebook(
                directory,
                "quoted-semicolon-install.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": '%pip install "demo;quoted"\n',
                        }
                    ]
                },
            )

            self.assertEqual(notebook_check.check_notebook(quoted), [])
            conservative_issues = notebook_check.check_notebook(conservative)

        self.assertTrue(
            any(
                issue.error_type == "UnsupportedCellSyntax"
                for issue in conservative_issues
            )
        )
        self.assertTrue(
            any(
                "without a later Python restart" in issue.message
                for issue in conservative_issues
            )
        )

    def test_normalization_preserves_multiline_python_and_source_lines(self) -> None:
        source = (
            "before = (\n"
            "    1 +\n"
            "    2\n"
            ")\n"
            "%pip install demo\n"
            "after = (\n"
            "    before +\n"
            "    3\n"
            ")\n"
            "dbutils.library.restartPython()\n"
        )
        normalized = notebook_check.normalize_cell(source)
        tree = notebook_check.ast.parse(normalized.source)

        self.assertEqual([event.line for event in normalized.install_events], [5])
        self.assertEqual([statement.lineno for statement in tree.body], [1, 5, 6, 10])
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "multiline.ipynb",
                {"cells": [{"cell_type": "code", "source": source}]},
            )
            issues = notebook_check.check_notebook(notebook)

        self.assertTrue(
            any(
                issue.severity == "error"
                and "%pip or pip install appears after imports or executable code"
                in issue.message
                for issue in issues
            )
        )
        self.assertFalse(
            any(issue.error_type == "SyntaxError" for issue in issues)
        )

    def test_unsafe_install_candidates_are_observable_and_pending(self) -> None:
        sources = {
            "bracket": "value = (\n%pip install demo\n)\n",
            "continuation": "value = 1 + \\\n%pip install demo\n",
            "indented": "if True:\n    %pip install demo\n",
            "chained": "%pip install demo; echo done\n",
            "restart_then_unsafe": (
                "%pip install one\n"
                "dbutils.library.restartPython()\n"
                "value = (\n"
                "%pip install two\n"
                ")\n"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in sources.items():
                with self.subTest(name=name):
                    notebook = self.write_notebook(
                        directory,
                        f"unsafe-{name}.ipynb",
                        {"cells": [{"cell_type": "code", "source": source}]},
                    )
                    issues = notebook_check.check_notebook(notebook)
                    self.assertEqual(
                        sum(
                            issue.error_type == "UnsupportedCellSyntax"
                            for issue in issues
                        ),
                        1,
                    )
                    self.assertTrue(
                        any(
                            "without a later Python restart" in issue.message
                            for issue in issues
                        )
                    )

    def test_full_cell_parse_failure_never_trusts_restart_prefix(self) -> None:
        source = (
            "%pip install demo\n"
            "dbutils.library.restartPython()\n"
            "if True print('malformed')\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "malformed-tail.ipynb",
                {"cells": [{"cell_type": "code", "source": source}]},
            )
            with mock.patch.object(
                notebook_check.ast,
                "parse",
                wraps=notebook_check.ast.parse,
            ) as parse:
                issues = notebook_check.check_notebook(notebook)

        self.assertEqual(parse.call_count, 1)
        self.assertTrue(any(issue.error_type == "SyntaxError" for issue in issues))
        self.assertTrue(
            any("without a later Python restart" in issue.message for issue in issues)
        )

    def test_python_directive_and_synthetic_pass_are_not_real_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            synthetic = self.write_notebook(
                directory,
                "synthetic-pass.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": (
                                "%python\n"
                                "%pip install one\n"
                                "%pip install two\n"
                                "dbutils.library.restartPython()\n"
                            ),
                        }
                    ]
                },
            )
            user_pass = self.write_notebook(
                directory,
                "user-pass.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": (
                                "pass\n"
                                "%pip install demo\n"
                                "dbutils.library.restartPython()\n"
                            ),
                        }
                    ]
                },
            )
            separate_pip_cells = self.write_notebook(
                directory,
                "separate-pip-placeholders.ipynb",
                {
                    "cells": [
                        {"cell_type": "code", "source": "%pip install one\n"},
                        {"cell_type": "code", "source": "%pip install two\n"},
                        {
                            "cell_type": "code",
                            "source": "dbutils.library.restartPython()\n",
                        },
                    ]
                },
            )

            self.assertEqual(notebook_check.check_notebook(synthetic), [])
            self.assertEqual(notebook_check.check_notebook(separate_pip_cells), [])
            user_issues = notebook_check.check_notebook(user_pass)

        self.assertTrue(
            any(
                issue.severity == "error"
                and "%pip or pip install appears after imports or executable code"
                in issue.message
                for issue in user_issues
            )
        )

    def test_one_analyzer_keeps_monotonic_order_and_state_across_install(self) -> None:
        cases = {
            "first_use": (
                "run()\n%pip install demo\ndef run():\n    return 1\n"
                "dbutils.library.restartPython()\n",
                "definition for 'run' appears after first use",
            ),
            "restart_rebind": (
                "dbutils.library = replacement\n%pip install demo\n"
                "dbutils.library.restartPython()\n",
                "without a later Python restart",
            ),
            "direct_function": (
                "def run():\n    return LATER\n%pip install demo\nrun()\n"
                "LATER = 1\ndbutils.library.restartPython()\n",
                "direct call to 'run' may load global 'LATER'",
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, (source, expected) in cases.items():
                with self.subTest(name=name):
                    notebook = self.write_notebook(
                        directory,
                        f"state-{name}.ipynb",
                        {"cells": [{"cell_type": "code", "source": source}]},
                    )
                    issues = notebook_check.check_notebook(notebook)
                    self.assertTrue(
                        any(expected in issue.message for issue in issues), issues
                    )

            postponed = self.write_notebook(
                directory,
                "state-postponed-annotations.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": (
                                "from __future__ import annotations\n"
                                "%pip install demo\n"
                                "def build(value: Later) -> Later:\n"
                                "    return value\n"
                                "Later = int\n"
                                "dbutils.library.restartPython()\n"
                            ),
                        }
                    ]
                },
            )
            postponed_issues = notebook_check.check_notebook(postponed)

        self.assertFalse(
            any("Later" in issue.message for issue in postponed_issues)
        )

    def test_notebook_code_seen_survives_restart_for_late_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "code-seen-after-restart.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "value = 1\ndbutils.library.restartPython()\n",
                        },
                        {
                            "cell_type": "code",
                            "source": (
                                "%pip install demo\n"
                                "dbutils.library.restartPython()\n"
                            ),
                        },
                    ]
                },
            )
            issues = notebook_check.check_notebook(notebook)

        self.assertTrue(
            any(
                issue.severity == "error"
                and "%pip or pip install appears after imports or executable code"
                in issue.message
                for issue in issues
            )
        )
        self.assertFalse(
            any("without a later Python restart" in issue.message for issue in issues)
        )

    def test_repeated_install_and_restart_order(self) -> None:
        cases = {
            "two_installs": (
                "%pip install one\n%pip install two\n"
                "dbutils.library.restartPython()\n",
                False,
            ),
            "code_between": (
                "%pip install one\nvalue = 1\n%pip install two\n"
                "dbutils.library.restartPython()\n",
                True,
            ),
            "restart_first": (
                "dbutils.library.restartPython()\n%pip install unreachable\n",
                False,
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, (source, expected_late) in cases.items():
                with self.subTest(name=name):
                    notebook = self.write_notebook(
                        directory,
                        f"repeat-{name}.ipynb",
                        {"cells": [{"cell_type": "code", "source": source}]},
                    )
                    issues = notebook_check.check_notebook(notebook)
                    late = any(
                        issue.severity == "error"
                        and "%pip or pip install appears after imports or executable code"
                        in issue.message
                        for issue in issues
                    )
                    self.assertEqual(late, expected_late)
                    self.assertFalse(
                        any(
                            "without a later Python restart" in issue.message
                            for issue in issues
                        )
                    )

    def test_non_python_install_evidence_warns_and_never_restarts_python(self) -> None:
        secret = "private-package-argument"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            unsupported = self.write_notebook(
                directory,
                "non-python.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": (
                                "%sh\n"
                                f"pip install demo --token {secret}\n"
                                "dbutils.library.restartPython()\n"
                            ),
                        }
                    ]
                },
            )
            later_restart = self.write_notebook(
                directory,
                "non-python-later-restart.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "%sh\npip install demo\n",
                        },
                        {
                            "cell_type": "code",
                            "source": "dbutils.library.restartPython()\n",
                        },
                    ]
                },
            )
            restart_text_only = self.write_notebook(
                directory,
                "non-python-restart-text.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "%sh\ndbutils.library.restartPython()\n",
                        }
                    ]
                },
            )
            line_magic_install = self.write_notebook(
                directory,
                "non-python-line-magic-install.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "%sh pip install demo\n",
                        }
                    ]
                },
            )
            unsupported_issues = notebook_check.check_notebook(unsupported)
            later_issues = notebook_check.check_notebook(later_restart)
            restart_text_issues = notebook_check.check_notebook(restart_text_only)
            line_magic_issues = notebook_check.check_notebook(line_magic_install)

        self.assertTrue(
            any(
                issue.error_type == "UnsupportedCellLanguage"
                for issue in unsupported_issues
            )
        )
        self.assertTrue(
            any(
                "without a later Python restart" in issue.message
                for issue in unsupported_issues
            )
        )
        self.assertNotIn(secret, json.dumps([asdict(issue) for issue in unsupported_issues]))
        self.assertTrue(
            any(issue.error_type == "UnsupportedCellLanguage" for issue in later_issues)
        )
        self.assertFalse(
            any("without a later Python restart" in issue.message for issue in later_issues)
        )
        self.assertEqual(restart_text_issues, [])
        self.assertTrue(
            any(
                issue.error_type == "UnsupportedCellLanguage"
                for issue in line_magic_issues
            )
        )
        self.assertTrue(
            any(
                "without a later Python restart" in issue.message
                for issue in line_magic_issues
            )
        )

    def test_unsupported_warning_keeps_cli_schema_and_exit_contract(self) -> None:
        secret = "private-package-argument"
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "unsupported-mixed.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": f"%pip install demo; echo {secret}\n",
                        }
                    ]
                },
            )
            outputs: list[tuple[int, list[dict[str, object]]]] = []
            for warnings_fail in (False, True):
                capture = io.StringIO()
                argv = ["checker", "--json"]
                if warnings_fail:
                    argv.append("--warnings-fail")
                argv.append(str(notebook))
                with (
                    mock.patch.object(sys, "argv", argv),
                    redirect_stdout(capture),
                ):
                    exit_code = notebook_check.main()
                payload = json.loads(capture.getvalue())
                outputs.append((exit_code, next(iter(payload.values()))))

        self.assertEqual([item[0] for item in outputs], [0, 1])
        self.assertEqual(outputs[0][1], outputs[1][1])
        self.assertTrue(
            any(
                finding["error_type"] == "UnsupportedCellSyntax"
                for finding in outputs[0][1]
            )
        )
        for finding in outputs[0][1]:
            self.assertEqual(
                set(finding), {"severity", "cell", "message", "error_type"}
            )
        self.assertNotIn(secret, json.dumps(outputs))

    def test_pip_like_identifiers_and_uv_do_not_add_install_state(self) -> None:
        sources = {
            "identifier": "pipeline = 'pip install demo'\n",
            "percent_prefix": "%pipette install demo\n",
            "bang_prefix": "!pipeline install demo\n",
            "uv": "%uv pip install demo\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in sources.items():
                with self.subTest(name=name):
                    notebook = self.write_notebook(
                        directory,
                        f"excluded-{name}.ipynb",
                        {"cells": [{"cell_type": "code", "source": source}]},
                    )
                    issues = notebook_check.check_notebook(notebook)
                    self.assertFalse(
                        any(
                            "without a later Python restart" in issue.message
                            for issue in issues
                        )
                    )

    def test_function_parameters_and_locals_do_not_become_module_uses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "function_scope.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "def transform(value):\n    local = value + 1\n    return local\n",
                        }
                    ]
                },
            )
            issues = notebook_check.check_notebook(notebook)
        self.assertFalse(
            any(
                "value" in issue.message or "local" in issue.message for issue in issues
            )
        )

    def test_same_cell_late_definition_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "late_definition.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "run()\ndef run():\n    return 1\n",
                        }
                    ]
                },
            )
            issues = notebook_check.check_notebook(notebook)
        self.assertTrue(
            any(
                issue.severity == "error"
                and "definition for 'run' appears after first use in cell 1"
                in issue.message
                for issue in issues
            )
        )

    def test_structural_restart_clears_post_pip_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            restart = self.write_notebook(
                directory,
                "structural_restart.ipynb",
                {
                    "cells": [
                        {"cell_type": "code", "source": "%pip install package\n"},
                        {
                            "cell_type": "code",
                            "source": "dbutils.library.restartPython()\n",
                        },
                    ]
                },
            )
            self.assertFalse(
                any(
                    "without a later Python restart" in issue.message
                    for issue in notebook_check.check_notebook(restart)
                )
            )

    def test_comment_or_string_does_not_count_as_post_pip_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            comment_or_string = self.write_notebook(
                directory,
                "nonstructural_restart.ipynb",
                {
                    "cells": [
                        {"cell_type": "code", "source": "%pip install package\n"},
                        {
                            "cell_type": "code",
                            "source": "# dbutils.library.restartPython()\nmessage = 'restartPython'\n",
                        },
                    ]
                },
            )
            self.assertTrue(
                any(
                    "without a later Python restart" in issue.message
                    for issue in notebook_check.check_notebook(comment_or_string)
                )
            )

    def test_restart_requires_direct_module_expression_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            nonstructural_sources = {
                "function": "def restart():\n    dbutils.library.restartPython()\n",
                "lambda": "restart = lambda: dbutils.library.restartPython()\n",
                "if_false": "if False:\n    dbutils.library.restartPython()\n",
                "comprehension": "[dbutils.library.restartPython() for _ in []]\n",
            }
            for name, source in nonstructural_sources.items():
                with self.subTest(name=name):
                    notebook = self.write_notebook(
                        directory,
                        f"{name}.ipynb",
                        {
                            "cells": [
                                {
                                    "cell_type": "code",
                                    "source": "%pip install package\n",
                                },
                                {"cell_type": "code", "source": source},
                            ]
                        },
                    )
                    issues = notebook_check.check_notebook(notebook)
                    self.assertTrue(
                        any(
                            "without a later Python restart" in issue.message
                            for issue in issues
                        )
                    )

            direct = self.write_notebook(
                directory,
                "direct.ipynb",
                {
                    "cells": [
                        {"cell_type": "code", "source": "%pip install package\n"},
                        {
                            "cell_type": "code",
                            "source": "dbutils.library.restartPython()\n",
                        },
                    ]
                },
            )
            self.assertFalse(
                any(
                    "without a later Python restart" in issue.message
                    for issue in notebook_check.check_notebook(direct)
                )
            )

    def test_restart_reports_prefix_uses_and_resets_later_kernel_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "restart_boundary.ipynb",
                {
                    "cells": [
                        {"cell_type": "code", "source": "%pip install package\n"},
                        {
                            "cell_type": "code",
                            "source": "run()\nbefore()\nvalue = 1\ndbutils.library.restartPython()\ndef run():\n    return 1\n",
                        },
                        {"cell_type": "code", "source": "print(value)\n"},
                    ]
                },
            )
            issues = notebook_check.check_notebook(notebook)
        for name in ("run", "before", "value"):
            with self.subTest(name=name):
                self.assertTrue(
                    any(
                        issue.severity == "warning"
                        and f"symbol '{name}' is used before a visible import or definition"
                        in issue.message
                        for issue in issues
                    )
                )
        self.assertFalse(
            any("without a later Python restart" in issue.message for issue in issues)
        )

    def test_rebound_restart_chain_does_not_satisfy_post_pip_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bypasses = {
                "dbutils_assignment": "dbutils = None\ndbutils.library.restartPython()\n",
                "dbutils_import": "import replacement as dbutils\ndbutils.library.restartPython()\n",
                "dbutils_delete": "del dbutils\ndbutils.library.restartPython()\n",
                "library_assignment": "dbutils.library = None\ndbutils.library.restartPython()\n",
                "restart_assignment": "dbutils.library.restartPython = lambda: None\ndbutils.library.restartPython()\n",
                "restart_delete": "del dbutils.library.restartPython\ndbutils.library.restartPython()\n",
                "reflective_setattr_restart": "setattr(dbutils.library, 'restartPython', None)\ndbutils.library.restartPython()\n",
                "reflective_delattr_restart": "delattr(dbutils.library, 'restartPython')\ndbutils.library.restartPython()\n",
                "reflective_setattr_library": "setattr(dbutils, 'library', None)\ndbutils.library.restartPython()\n",
                "reflective_delattr_library": "delattr(dbutils, 'library')\ndbutils.library.restartPython()\n",
                "vars_restart_slot": "vars(dbutils.library)['restartPython'] = None\ndbutils.library.restartPython()\n",
                "vars_library_slot": "vars(dbutils)['library'] = None\ndbutils.library.restartPython()\n",
                "tuple_target": "(dbutils.library.restartPython, other) = (None, None)\ndbutils.library.restartPython()\n",
                "list_target": "[dbutils.library.restartPython] = [None]\ndbutils.library.restartPython()\n",
                "for_target": "for dbutils.library.restartPython in []:\n    pass\ndbutils.library.restartPython()\n",
                "with_target": "with manager() as dbutils.library.restartPython:\n    pass\ndbutils.library.restartPython()\n",
            }
            for name, source in bypasses.items():
                with self.subTest(name=name):
                    notebook = self.write_notebook(
                        directory,
                        f"{name}.ipynb",
                        {
                            "cells": [
                                {
                                    "cell_type": "code",
                                    "source": "%pip install package\n",
                                },
                                {"cell_type": "code", "source": source},
                            ]
                        },
                    )
                    issues = notebook_check.check_notebook(notebook)
                    self.assertTrue(
                        any(
                            "without a later Python restart" in issue.message
                            for issue in issues
                        )
                    )

            benign = self.write_notebook(
                directory,
                "restart_chain_intact.ipynb",
                {
                    "cells": [
                        {"cell_type": "code", "source": "%pip install package\n"},
                        {
                            "cell_type": "code",
                            "source": "class Namespace:\n    dbutils = None\ndbutils.library.restartPython()\n",
                        },
                    ]
                },
            )
            self.assertFalse(
                any(
                    "without a later Python restart" in issue.message
                    for issue in notebook_check.check_notebook(benign)
                )
            )

    def test_conditional_loop_and_comprehension_bindings_are_not_definite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "conditional_bindings.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "flag = False\nif flag:\n    conditional = 1\n    print(conditional)\nprint(conditional)\nfor record in []:\n    pass\nprint(record)\nvalues = [(captured := value) for value in []]\nprint(captured)\n",
                        }
                    ]
                },
            )
            issues = notebook_check.check_notebook(notebook)
        for name in ("conditional", "record", "captured"):
            with self.subTest(name=name):
                self.assertTrue(
                    any(
                        issue.severity == "warning"
                        and f"symbol '{name}' is used before a visible import or definition"
                        in issue.message
                        for issue in issues
                    )
                )

    def test_cli_exposes_rebound_restart_warning_without_changing_schema(self) -> None:
        capture = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "rebound_restart.ipynb",
                {
                    "cells": [
                        {"cell_type": "code", "source": "%pip install package\n"},
                        {
                            "cell_type": "code",
                            "source": "vars(dbutils)['library'] = None\ndbutils.library.restartPython()\n",
                        },
                    ]
                },
            )
            with (
                mock.patch.object(
                    sys, "argv", ["checker", "--json", "--warnings-fail", str(notebook)]
                ),
                redirect_stdout(capture),
            ):
                code = notebook_check.main()
        self.assertEqual(code, 1)
        payload = json.loads(capture.getvalue())
        findings = next(iter(payload.values()))
        self.assertTrue(
            any(
                "without a later Python restart" in finding["message"]
                for finding in findings
            )
        )

    def test_immediate_definition_expressions_and_class_body_are_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            late = self.write_notebook(
                directory,
                "late_immediate.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "@decorator(decorator_argument)\ndef build(value=default):\n    return value\nlambda_build = lambda value=lambda_default: value\nclass Child(base, metaclass=class_meta):\n    captured = class_late\ndecorator = lambda value: lambda function: function\ndecorator_argument = 1\ndefault = 1\nlambda_default = 1\nbase = object\nclass_meta = type\nclass_late = 1\n",
                        }
                    ]
                },
            )
            issues = notebook_check.check_notebook(late)
            for name in (
                "decorator",
                "decorator_argument",
                "default",
                "lambda_default",
                "base",
                "class_meta",
                "class_late",
            ):
                with self.subTest(name=name):
                    self.assertTrue(
                        any(
                            issue.severity == "error"
                            and f"definition for '{name}' appears after first use"
                            in issue.message
                            for issue in issues
                        )
                    )

            ready = self.write_notebook(
                directory,
                "ready_immediate.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "decorator = lambda value: lambda function: function\ndecorator_argument = 1\ndefault = 1\nlambda_default = 1\nbase = object\nclass_meta = type\nclass_late = 1\n@decorator(decorator_argument)\ndef build(value=default):\n    return value\nlambda_build = lambda value=lambda_default: value\nclass Child(base, metaclass=class_meta):\n    captured = class_late\n",
                        }
                    ]
                },
            )
            ready_issues = notebook_check.check_notebook(ready)
            self.assertFalse(any(issue.severity == "error" for issue in ready_issues))

    def test_comprehension_targets_are_local_but_outer_expressions_are_ordered(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            late = self.write_notebook(
                directory,
                "late_comprehension.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "values = [item for item in iterable if item > lower]\npairs = [inner for outer in groups for inner in outer]\niterable = [1]\nlower = 0\ngroups = [[1]]\n",
                        }
                    ]
                },
            )
            issues = notebook_check.check_notebook(late)
            for name in ("iterable", "lower", "groups"):
                with self.subTest(name=name):
                    self.assertTrue(
                        any(
                            issue.severity == "error"
                            and f"definition for '{name}' appears after first use"
                            in issue.message
                            for issue in issues
                        )
                    )
            self.assertFalse(
                any(
                    "item" in issue.message
                    or "outer" in issue.message
                    or "inner" in issue.message
                    for issue in issues
                )
            )

            ready = self.write_notebook(
                directory,
                "ready_comprehension.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "iterable = [1]\nlower = 0\ngroups = [[1]]\nvalues = [item for item in iterable if item > lower]\npairs = [inner for outer in groups for inner in outer]\n",
                        }
                    ]
                },
            )
            self.assertEqual(notebook_check.check_notebook(ready), [])

    def test_module_bindings_annotation_delete_and_future_annotation_order(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            binding_cases = self.write_notebook(
                directory,
                "bindings.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "declared: int\nprint(declared)\nalive = 1\ndel alive\nprint(alive)\nresult = (bound := factory())\nprint(bound)\nfor record in records:\n    pass\nprint(record)\nwith manager() as handle:\n    pass\nprint(handle)\nfactory = lambda: 1\nrecords = []\nmanager = lambda: None\n",
                        }
                    ]
                },
            )
            issues = notebook_check.check_notebook(binding_cases)
            for name in ("factory", "records", "manager"):
                with self.subTest(name=name):
                    self.assertTrue(
                        any(
                            issue.severity == "error"
                            and f"definition for '{name}' appears after first use"
                            in issue.message
                            for issue in issues
                        )
                    )
            for name in ("declared", "alive", "record"):
                with self.subTest(name=name):
                    self.assertTrue(
                        any(
                            issue.severity == "warning"
                            and f"symbol '{name}' is used before a visible import or definition"
                            in issue.message
                            for issue in issues
                        )
                    )
            for name in ("bound", "handle"):
                with self.subTest(name=name):
                    self.assertFalse(
                        any(f"'{name}'" in issue.message for issue in issues)
                    )

            future = self.write_notebook(
                directory,
                "future_annotations.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "from __future__ import annotations\ndef build(value: Later) -> Result:\n    return value\nLater = int\nResult = int\n",
                        }
                    ]
                },
            )
            future_issues = notebook_check.check_notebook(future)
            self.assertFalse(
                any(
                    "Later" in issue.message or "Result" in issue.message
                    for issue in future_issues
                )
            )

    def test_missing_malformed_and_schema_failures_are_cell_zero_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            malformed = directory / "malformed.ipynb"
            malformed.write_text("{", encoding="utf-8")
            non_utf8 = directory / "non-utf8.ipynb"
            non_utf8.write_bytes(b"\xff")
            schema = self.write_notebook(
                directory,
                "schema.ipynb",
                {"cells": [{"cell_type": "code", "source": 5}]},
            )
            for path, expected in (
                (directory / "missing.ipynb", "FileNotFoundError"),
                (malformed, "JSONDecodeError"),
                (non_utf8, "UnicodeDecodeError"),
                (schema, "NotebookSchemaError"),
            ):
                issues = notebook_check.check_notebook(path)
                self.assertEqual(issues[0].severity, "error")
                self.assertEqual(issues[0].cell, 0)
                self.assertEqual(issues[0].error_type, expected)

    def test_cli_always_emits_valid_json_and_exit_one_for_input_error(self) -> None:
        capture = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.ipynb"
            with (
                mock.patch.object(sys, "argv", ["checker", "--json", str(missing)]),
                redirect_stdout(capture),
            ):
                code = notebook_check.main()
        self.assertEqual(code, 1)
        payload = json.loads(capture.getvalue())
        self.assertEqual(next(iter(payload.values()))[0]["cell"], 0)

    def test_direct_function_call_warns_for_later_global_without_changing_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "direct_function_global.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "def run_cell_011():\n    return OUTPUT_TABLE\n",
                        },
                        {"cell_type": "code", "source": "run_cell_011()\n"},
                        {"cell_type": "code", "source": "OUTPUT_TABLE = 'x'\n"},
                    ]
                },
            )
            issues = notebook_check.check_notebook(notebook)

        findings = [
            issue for issue in issues if "direct call to 'run_cell_011'" in issue.message
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warning")
        self.assertEqual(findings[0].cell, 2)
        self.assertIn("OUTPUT_TABLE", findings[0].message)

    def test_direct_function_ignores_local_annotations_but_keeps_values_and_complex_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "direct_function_annotations.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "def run_cell_011():\n    scratch: OUTPUT_TABLE\n    cached: OTHER = 1\n    result: ANNOTATION = RHS\n    holder[INDEX]: TYPE = VALUE\n    return 1\nrun_cell_011()\n",
                        }
                    ]
                },
            )
            issues = notebook_check.check_notebook(notebook)

        messages = [issue.message for issue in issues if "direct call to" in issue.message]
        for name in ("RHS", "holder", "INDEX", "VALUE"):
            with self.subTest(required=name):
                self.assertTrue(any(name in message for message in messages))
        for name in ("OUTPUT_TABLE", "OTHER", "ANNOTATION", "TYPE"):
            with self.subTest(annotation_only=name):
                self.assertFalse(any(name in message for message in messages))

    def test_direct_function_analysis_skips_unverified_and_invalidated_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notebook = self.write_notebook(
                Path(temporary),
                "direct_function_controls.ipynb",
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "source": "OUTPUT_TABLE = 'ready'\ndef parameter(OUTPUT_TABLE):\n    return OUTPUT_TABLE\ndef local_value():\n    OUTPUT_TABLE = 'local'\n    return OUTPUT_TABLE\ndef comprehension():\n    return [OUTPUT_TABLE for OUTPUT_TABLE in ['local']]\ndef global_write():\n    global OUTPUT_TABLE\n    OUTPUT_TABLE = 'created'\n    return OUTPUT_TABLE\ndef generator():\n    yield LATER\nasync def asynchronous():\n    return LATER\ndef dynamic():\n    return globals()['LATER']\ndef helper():\n    return LATER\nhelper = lambda: None\nparameter('value')\nlocal_value()\ncomprehension()\nglobal_write()\ngenerator()\nasynchronous()\ndynamic()\nhelper()\n",
                        },
                        {
                            "cell_type": "code",
                            "source": "def after_restart():\n    return LATER\ndbutils.library.restartPython()\n",
                        },
                        {"cell_type": "code", "source": "after_restart()\n"},
                    ]
                },
            )
            issues = notebook_check.check_notebook(notebook)

        self.assertFalse(any("direct call to" in issue.message for issue in issues))


if __name__ == "__main__":
    unittest.main()
