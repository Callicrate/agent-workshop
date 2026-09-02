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
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import check_notebook_runtime_order as notebook_check  # noqa: E402
import collect_env_snapshot as snapshot  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
