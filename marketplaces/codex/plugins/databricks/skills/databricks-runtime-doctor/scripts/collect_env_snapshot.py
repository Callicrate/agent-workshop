#!/usr/bin/env python3
"""Collect bounded, credential-safe runtime evidence for Databricks ML failures."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import signal
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from runtime_safety import MAX_TEXT_LENGTH, SafeArgumentParser, redact_structure, redact_text, safe_error

DEFAULT_PACKAGES = (
    "torch",
    "transformers",
    "tokenizers",
    "accelerate",
    "sympy",
    "typing_extensions",
    "mlflow",
)
DEFAULT_ENV_KEYS = (
    "ACCELERATE_LOG_LEVEL",
    "TRANSFORMERS_VERBOSITY",
    "HF_HOME",
    "TRANSFORMERS_CACHE",
    "HF_DATASETS_CACHE",
)
DATABRICKS_METADATA_KEYS = (
    "DATABRICKS_RUNTIME_VERSION",
    "DATABRICKS_RUNTIME_VERSION_DETAILS",
    "DATABRICKS_CLUSTER_ID",
    "DB_CLUSTER_ID",
    "DATABRICKS_JOB_ID",
    "DATABRICKS_RUN_ID",
    "DATABRICKS_TASK_KEY",
    "DATABRICKS_TASK_ATTEMPT_NUMBER",
    "DATABRICKS_ROOT_RUN_ID",
    "DATABRICKS_WORKSPACE_URL",
)

MAX_OPTION_ITEMS = 24
MAX_IDENTIFIER_LENGTH = 96
MAX_NVIDIA_OUTPUT_BYTES = 16 * 1024
MAX_CUDA_DEVICES = 32
PROCESS_STOP_TIMEOUT_SECONDS = 2
DRAIN_JOIN_SECONDS = 2
NVIDIA_SMI_ARGS = (
    "--query-gpu=name,driver_version,memory.total,memory.free",
    "--format=csv,noheader,nounits",
)


def split_csv(value: str | None, *, max_items: int = MAX_OPTION_ITEMS) -> list[str]:
    """Split a bounded comma-separated option without altering item values."""
    if not value:
        return []
    items: list[str] = []
    start = 0
    while len(items) < max_items:
        delimiter = value.find(",", start)
        item = value[start:] if delimiter == -1 else value[start:delimiter]
        item = item.strip()
        if item:
            items.append(item)
        if delimiter == -1:
            break
        start = delimiter + 1
    return items


def is_top_level_identifier(value: str) -> bool:
    """Return whether a value is safe to pass to importlib.find_spec."""
    return bool(value) and value.isidentifier() and "." not in value and len(value) <= MAX_IDENTIFIER_LENGTH


def parse_package_option(value: str) -> str:
    """Accept only a bounded list of allowlisted package probe identifiers."""
    packages = split_csv(value, max_items=MAX_OPTION_ITEMS + 1)
    if not packages or len(packages) > MAX_OPTION_ITEMS or any(
        not is_top_level_identifier(package) for package in packages
    ):
        raise argparse.ArgumentTypeError("invalid package probe list")
    return value


def package_distribution_name(
    module_name: str,
    package_distributions: dict[str, list[str]] | None = None,
) -> str:
    """Return a distribution name using one caller-provided metadata snapshot."""
    distributions = (package_distributions or {}).get(module_name)
    if distributions:
        return distributions[0]

    overrides = {
        "cv2": "opencv-python",
        "PIL": "Pillow",
        "pydantic_ai": "pydantic-ai",
        "sklearn": "scikit-learn",
        "typing_extensions": "typing_extensions",
    }
    return overrides.get(module_name, module_name.replace("_", "-"))


def get_package_distributions() -> dict[str, list[str]]:
    """Read package-to-distribution metadata once, recovering into safe output."""
    try:
        return importlib.metadata.packages_distributions()
    except Exception:
        return {}


def get_pkg_version(
    module_name: str,
    package_distributions: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Return one safe package probe without importing optional or heavy modules."""
    if not is_top_level_identifier(module_name):
        return {
            "module": "[INVALID_IDENTIFIER]",
            "distribution": None,
            "version": "unknown",
            "importable": False,
            "origin": None,
            "status": "invalid",
            "error_type": "ValueError",
            "reason": "package probe requires an allowlisted top-level Python identifier",
        }

    distribution_name = package_distribution_name(module_name, package_distributions)
    result: dict[str, Any] = {
        "module": module_name,
        "distribution": distribution_name,
        "version": "unknown",
        "importable": False,
        "origin": None,
        "status": "complete",
        "error_type": None,
        "reason": "package metadata and module spec collected",
    }
    try:
        result["version"] = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        result["version"] = "not installed"
    except Exception as exc:
        result.update({"status": "partial", **safe_error("package metadata probe", exc)})

    try:
        # The input is strictly top-level, so find_spec cannot import a dotted parent.
        spec = importlib.util.find_spec(module_name)
        result["importable"] = spec is not None
        result["origin"] = getattr(spec, "origin", None) if spec else None
        if spec is None and result["status"] == "complete":
            result["status"] = "missing"
            result["reason"] = "module spec not found"
    except Exception as exc:
        result["importable"] = False
        result.update({"status": "error", **safe_error("module spec probe", exc)})
    return redact_structure(result)


def get_cuda_info() -> dict[str, Any]:
    """Collect CUDA facts while preserving a count/list consistency contract."""
    info: dict[str, Any] = {
        "probe_status": "complete",
        "cuda_available": False,
        "device_count": 0,
        "devices": [],
        "error_type": None,
        "reason": "CUDA probe completed",
    }
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        info.update({"probe_status": "error", **safe_error("torch import", exc)})
        return redact_structure(info)

    try:
        info["torch_cuda_version"] = getattr(getattr(torch, "version", None), "cuda", None)
        info["cuda_available"] = bool(torch.cuda.is_available())
        reported_count = int(torch.cuda.device_count())
        if reported_count < 0:
            raise ValueError("negative CUDA device count")
    except Exception as exc:
        info.update({"probe_status": "error", **safe_error("CUDA availability probe", exc)})
        return redact_structure(info)

    device_count = min(reported_count, MAX_CUDA_DEVICES)
    if not info["cuda_available"] and reported_count:
        info.update(
            {
                "probe_status": "partial",
                "device_count": 0,
                "devices": [],
                "error_type": "CudaInvariantError",
                "reason": "CUDA availability and device count disagree",
            }
        )
        return redact_structure(info)
    if info["cuda_available"] and reported_count == 0:
        info.update(
            {
                "probe_status": "partial",
                "error_type": "CudaInvariantError",
                "reason": "CUDA is available but no devices were reported",
            }
        )
        return redact_structure(info)
    info["device_count"] = device_count
    if reported_count > MAX_CUDA_DEVICES:
        info["probe_status"] = "partial"
        info["device_count_limited"] = True
        info["reason"] = "CUDA device list capped for bounded diagnostics"

    for index in range(device_count):
        device: dict[str, Any] = {
            "index": index,
            "status": "complete",
            "error_type": None,
            "reason": "device probe completed",
        }
        try:
            device["name"] = torch.cuda.get_device_name(index)
            device["capability"] = torch.cuda.get_device_capability(index)
        except Exception as exc:
            device.update({"status": "error", **safe_error("CUDA device probe", exc)})
            info["probe_status"] = "partial"
        info["devices"].append(device)

    # Keep the public count truthful even if a device probe fails.
    info["device_count"] = len(info["devices"])
    if not info["cuda_available"] and info["probe_status"] == "complete":
        info["reason"] = "CUDA is not available in this Python runtime"
    return redact_structure(info)


def _drain_stream(
    stream: Any,
    sink: list[bytes],
    errors: list[dict[str, str]],
    overflow: threading.Event,
    limit: int,
) -> None:
    """Read a subprocess stream continuously, retaining no more than ``limit`` bytes."""
    retained = 0
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            remaining = limit - retained
            if remaining > 0:
                kept = chunk[:remaining]
                sink.append(kept)
                retained += len(kept)
            if len(chunk) > max(remaining, 0):
                overflow.set()
    except Exception as exc:
        errors.append(safe_error("nvidia-smi output drain", exc))
    finally:
        try:
            stream.close()
        except Exception:
            pass


def process_tree_popen_kwargs() -> dict[str, Any]:
    """Create an isolated process tree that can be stopped with its descendants."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def process_tree_alive(process: subprocess.Popen[bytes]) -> bool:
    """Best-effort check that the isolated POSIX group or Windows leader remains live."""
    if not isinstance(getattr(process, "pid", None), int):
        return process.poll() is None
    if os.name == "nt":
        return process.poll() is None
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def request_process_tree_stop(process: subprocess.Popen[bytes], *, force: bool) -> bool:
    """Request cross-platform process-tree termination without exposing command output."""
    if not isinstance(getattr(process, "pid", None), int):
        try:
            (process.kill if force else process.terminate)()
        except (OSError, subprocess.SubprocessError):
            return False
        return True
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=PROCESS_STOP_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0 or process.poll() is not None
    try:
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


def _stop_process(process: subprocess.Popen[bytes]) -> tuple[bool, bool, bool]:
    """Terminate the isolated process tree and prove that it and its group exited."""
    terminated = False
    killed = False
    if process.poll() is None:
        terminated = True
        if not request_process_tree_stop(process, force=False):
            return terminated, killed, True
        try:
            process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            killed = True

    if process_tree_alive(process):
        killed = True
        if not request_process_tree_stop(process, force=True):
            return terminated, killed, True
        try:
            process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return terminated, killed, True
    return terminated, killed, process_tree_alive(process)


def _decode_output(chunks: list[bytes]) -> str:
    """Decode retained subprocess output without trusting its contents."""
    return redact_text(b"".join(chunks).decode("utf-8", errors="replace"), limit=MAX_TEXT_LENGTH)


def get_nvidia_smi(timeout_seconds: int) -> dict[str, Any]:
    """Collect bounded nvidia-smi output with timeout and overflow cleanup."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
        return {
            "available": False,
            "error_type": "ValueError",
            "reason": "nvidia-smi timeout must be a whole number from 1 through 120 seconds",
            "output_limited": False,
        }

    executable = shutil.which("nvidia-smi")
    if not executable:
        return {
            "available": False,
            "error_type": None,
            "reason": "nvidia-smi not found on PATH",
            "output_limited": False,
        }

    try:
        process = subprocess.Popen(
            [executable, *NVIDIA_SMI_ARGS],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            **process_tree_popen_kwargs(),
        )
    except Exception as exc:
        return redact_structure({"available": False, "output_limited": False, **safe_error("nvidia-smi launch", exc)})

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    drain_errors: list[dict[str, str]] = []
    overflow = threading.Event()
    stdout_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stdout, stdout_chunks, drain_errors, overflow, MAX_NVIDIA_OUTPUT_BYTES),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stderr, stderr_chunks, drain_errors, overflow, MAX_NVIDIA_OUTPUT_BYTES),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    terminated = False
    killed = False
    cleanup_incomplete = False
    deadline = time.monotonic() + timeout_seconds
    try:
        while process.poll() is None:
            if overflow.is_set():
                terminated, killed, cleanup_incomplete = _stop_process(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                terminated, killed, cleanup_incomplete = _stop_process(process)
                break
            time.sleep(0.01)
    finally:
        if process.poll() is None or process_tree_alive(process):
            final_terminated, final_killed, final_cleanup_incomplete = _stop_process(process)
            terminated = terminated or final_terminated
            killed = killed or final_killed
            cleanup_incomplete = cleanup_incomplete or final_cleanup_incomplete
        stdout_thread.join(timeout=DRAIN_JOIN_SECONDS)
        stderr_thread.join(timeout=DRAIN_JOIN_SECONDS)

    drain_threads_alive = stdout_thread.is_alive() or stderr_thread.is_alive()
    descendants_alive = process_tree_alive(process)
    cleanup_incomplete = cleanup_incomplete or drain_threads_alive or descendants_alive

    result: dict[str, Any] = {
        "available": True,
        "returncode": process.returncode,
        "stdout": _decode_output(stdout_chunks),
        "stderr": _decode_output(stderr_chunks),
        "timed_out": timed_out,
        "terminated": terminated,
        "killed": killed,
        "cleanup_incomplete": cleanup_incomplete,
        "drain_threads_alive": drain_threads_alive,
        "descendants_alive": descendants_alive,
        "drain_errors": drain_errors[:2],
        "output_limited": overflow.is_set(),
        "error_type": None,
        "reason": "nvidia-smi probe completed",
    }
    if cleanup_incomplete:
        result.update(
            {
                "error_type": "ProcessCleanupError",
                "reason": "nvidia-smi process could not be confirmed stopped",
            }
        )
    elif timed_out:
        result.update({"error_type": "TimeoutExpired", "reason": "nvidia-smi probe timed out"})
    elif overflow.is_set():
        result.update({"error_type": "OutputLimitExceeded", "reason": "nvidia-smi output exceeded the diagnostic cap"})
    elif process.returncode not in (0, None):
        result.update({"error_type": "SubprocessError", "reason": "nvidia-smi returned a nonzero exit code"})
    elif drain_errors:
        result.update(
            {
                "error_type": drain_errors[0]["error_type"],
                "reason": "nvidia-smi output drain failed",
            }
        )
    return redact_structure(result)


def get_dbr_version() -> str:
    """Return DBR version from known environment metadata or the active Spark session."""
    for key in ("DATABRICKS_RUNTIME_VERSION", "DATABRICKS_RUNTIME_VERSION_DETAILS"):
        value = os.environ.get(key)
        if value:
            return redact_text(value)
    try:
        pyspark_sql = importlib.import_module("pyspark.sql")
        spark_session = getattr(pyspark_sql, "SparkSession")
        spark = spark_session.getActiveSession()
        if spark is None:
            return "unknown"
        for key, value in spark.sparkContext.getConf().getAll():
            if key.lower().startswith("spark.databricks.clusterusagetags.sparkversion"):
                return redact_text(value)
    except Exception:
        return "unknown"
    return "unknown"


def detect_source(requested_source: str) -> str:
    """Infer whether the snapshot is local, Databricks live, from-spec, or inferred."""
    if requested_source != "auto":
        return requested_source
    if any(os.environ.get(key) for key in DATABRICKS_METADATA_KEYS):
        return "databricks-live"
    return "local"


def collect_databricks_metadata() -> dict[str, str | None]:
    """Collect explicitly allowlisted Databricks metadata from the environment."""
    return {key: os.environ.get(key) for key in DATABRICKS_METADATA_KEYS}


def valid_nltk_resource(resource: str) -> bool:
    """Allow NLTK resource names, optionally under exactly one resource family."""
    parts = resource.split("/")
    return len(parts) <= 2 and all(part.replace("_", "a").replace("-", "a").isalnum() for part in parts)


def check_nltk_resources(resources: list[str]) -> dict[str, Any]:
    """Check bounded NLTK data resources without downloading them or leaking errors."""
    if not resources:
        return {}
    safe_resources = resources[:MAX_OPTION_ITEMS]
    try:
        nltk = importlib.import_module("nltk")
    except Exception as exc:
        failure = safe_error("NLTK import", exc)
        return {
            resource if valid_nltk_resource(resource) else "[INVALID_RESOURCE]": {
                "available": False,
                "status": "error",
                "path": None,
                "checked_candidates": [],
                **failure,
            }
            for resource in safe_resources
        }

    results: dict[str, Any] = {}
    for resource in safe_resources:
        if not valid_nltk_resource(resource):
            results["[INVALID_RESOURCE]"] = {
                "available": False,
                "status": "invalid",
                "path": None,
                "checked_candidates": [],
                "error_type": "ValueError",
                "reason": "NLTK resource must be an allowlisted relative name",
            }
            continue
        candidates = [resource] if "/" in resource else [f"tokenizers/{resource}", f"corpora/{resource}"]
        found_path: str | None = None
        errors: list[dict[str, str]] = []
        for candidate in candidates:
            try:
                found_path = str(nltk.data.find(candidate))
                break
            except Exception as exc:
                errors.append(safe_error("NLTK resource probe", exc))
        last_error = errors[-1] if errors else {"error_type": None, "reason": "NLTK resource was not found"}
        resource_missing = found_path is None and all(error["error_type"] == "LookupError" for error in errors)
        results[resource] = {
            "available": found_path is not None,
            "status": "complete" if found_path is not None else ("missing" if resource_missing else "error"),
            "path": found_path,
            "checked_candidates": candidates,
            "errors": errors[:2],
            "error_type": None if found_path is not None else last_error["error_type"],
            "reason": (
                "NLTK resource found"
                if found_path is not None
                else ("NLTK resource was not found" if resource_missing else "NLTK resource probe failed")
            ),
        }
    return redact_structure(results)


def build_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    """Build a bounded, redacted runtime snapshot from expected probe failures."""
    extras = split_csv(args.packages)
    package_names = list(dict.fromkeys([*DEFAULT_PACKAGES, *extras]))[:MAX_OPTION_ITEMS]
    package_distributions = get_package_distributions()
    nltk_resources = split_csv(args.nltk_data)
    snapshot = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": detect_source(args.source),
        "source_note": args.source_note or "",
        "dbr": get_dbr_version(),
        "python": platform.python_version(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "packages": {
            package: get_pkg_version(package, package_distributions) for package in package_names
        },
        "nltk_data": check_nltk_resources(nltk_resources),
        "cuda": get_cuda_info(),
        "nvidia_smi": get_nvidia_smi(args.nvidia_smi_timeout),
        "databricks": collect_databricks_metadata(),
        "env": {key: os.environ.get(key) for key in DEFAULT_ENV_KEYS},
    }
    return redact_structure(snapshot)


def positive_timeout(value: str) -> int:
    """Parse the nvidia-smi timeout with an explicit bounded integer contract."""
    try:
        timeout = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a whole number from 1 through 120") from exc
    if not 1 <= timeout <= 120:
        raise argparse.ArgumentTypeError("must be a whole number from 1 through 120")
    return timeout


def build_parser() -> argparse.ArgumentParser:
    """Build the collector CLI parser."""
    parser = SafeArgumentParser(description="Collect Databricks ML runtime evidence")
    parser.add_argument(
        "--packages",
        type=parse_package_option,
        help="Comma-separated optional top-level modules to probe",
    )
    parser.add_argument("--nltk-data", help="Comma-separated NLTK resources to probe without downloading")
    parser.add_argument(
        "--source",
        choices=["auto", "local", "databricks-live", "from-spec", "inferred"],
        default="auto",
        help="Evidence source label for freshness tracking",
    )
    parser.add_argument("--source-note", help="Bounded note describing the command, cluster, or spec used")
    parser.add_argument(
        "--nvidia-smi-timeout",
        type=positive_timeout,
        default=10,
        help="Timeout in seconds for nvidia-smi (1 through 120)",
    )
    return parser


def main() -> int:
    """Run the snapshot collector and emit only redacted JSON."""
    args = build_parser().parse_args()
    sys.stdout.write(json.dumps(build_snapshot(args), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
