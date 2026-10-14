#!/usr/bin/env python3
"""Inspect installed TrainingArguments keyword support without constructing it."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from runtime_safety import SafeArgumentParser, redact_structure, redact_text, safe_error

DEFAULT_REQUESTED_FIELDS = (
    "eval_strategy",
    "evaluation_strategy",
    "logging_first_step",
)
MAX_REQUESTED_FIELDS = 24
MAX_FIELD_NAME_LENGTH = 96
FIELD_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse_requested_fields(value: str) -> tuple[str, ...]:
    """Parse a bounded list of field names without accepting values or expressions."""

    fields = tuple(field.strip() for field in value.split(",") if field.strip())
    if (
        not fields
        or len(fields) > MAX_REQUESTED_FIELDS
        or any(
            len(field) > MAX_FIELD_NAME_LENGTH
            or FIELD_NAME_PATTERN.fullmatch(field) is None
            for field in fields
        )
    ):
        raise argparse.ArgumentTypeError("fields must be a bounded comma-separated list of Python identifiers")
    return fields


def _normalise_requested_fields(fields: Iterable[str]) -> tuple[str, ...]:
    """Reject malformed programmatic input before it reaches diagnostic output."""

    values = tuple(fields)
    if any(not isinstance(field, str) for field in values):
        raise ValueError("requested fields must be strings")
    return parse_requested_fields(",".join(values))


def _base_result(fields: Sequence[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "complete": False,
        "package": {"name": "transformers", "loaded": False, "version": "unknown"},
        "signature": {"status": "unavailable", "accepts_variadic_keywords": False},
        "requested_fields": {field: "unknown" for field in fields},
        "errors": [],
    }


def _loaded_version(module: object) -> str:
    """Return a bounded package version after the explicit optional import succeeds."""

    try:
        version = getattr(module, "__version__", None)
    except Exception:
        return "unknown"
    return redact_text(version, limit=128) if isinstance(version, str) and version else "unknown"


def probe_training_arguments(
    requested_fields: Iterable[str] = DEFAULT_REQUESTED_FIELDS,
    *,
    module_loader: Callable[[str], Any] = importlib.import_module,
    signature_inspector: Callable[[Any], inspect.Signature] = inspect.signature,
) -> dict[str, Any]:
    """Return signature-only support evidence for requested TrainingArguments fields.

    This explicitly imports ``transformers`` only when the probe runs. It never
    constructs TrainingArguments, Trainer, or a model, and never queries CUDA or
    a network service. An opaque or variadic signature leaves unlisted field
    support unknown because it does not prove the constructor accepts a keyword.
    """

    fields = _normalise_requested_fields(requested_fields)
    result = _base_result(fields)
    try:
        transformers = module_loader("transformers")
    except Exception as exc:
        result["errors"].append(safe_error("transformers import", exc))
        return redact_structure(result)

    result["package"] = {
        "name": "transformers",
        "loaded": True,
        "version": _loaded_version(transformers),
    }
    try:
        training_arguments = getattr(transformers, "TrainingArguments")
        signature = signature_inspector(training_arguments.__init__)
    except Exception as exc:
        result["signature"] = {
            "status": "opaque",
            "accepts_variadic_keywords": False,
        }
        result["errors"].append(safe_error("TrainingArguments signature inspection", exc))
        return redact_structure(result)

    parameters = signature.parameters
    explicit_fields = {
        name
        for name, parameter in parameters.items()
        if name != "self"
        and parameter.kind
        in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    }
    accepts_variadic_keywords = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    result["signature"] = {
        "status": "variadic" if accepts_variadic_keywords else "explicit",
        "accepts_variadic_keywords": accepts_variadic_keywords,
    }
    result["requested_fields"] = {
        field: (
            "supported"
            if field in explicit_fields
            else "unknown"
            if accepts_variadic_keywords
            else "unsupported"
        )
        for field in fields
    }
    if accepts_variadic_keywords:
        result["errors"].append(
            {
                "error_type": "SignatureAmbiguity",
                "reason": "variadic TrainingArguments signature cannot prove unlisted keyword support",
            }
        )
    else:
        result["complete"] = True
    return redact_structure(result)


def select_evaluation_strategy_key(evidence: Mapping[str, Any]) -> str:
    """Prefer an explicit eval spelling or fail instead of guessing from version text."""

    signature = evidence.get("signature")
    fields = evidence.get("requested_fields")
    if (
        evidence.get("complete") is not True
        or not isinstance(signature, Mapping)
        or signature.get("status") != "explicit"
        or signature.get("accepts_variadic_keywords") is not False
        or not isinstance(fields, Mapping)
    ):
        raise RuntimeError("TrainingArguments evaluation alias support is unknown")
    eval_strategy = fields.get("eval_strategy")
    evaluation_strategy = fields.get("evaluation_strategy")
    valid_states = {"supported", "unsupported", "unknown"}
    if (
        not isinstance(eval_strategy, str)
        or not isinstance(evaluation_strategy, str)
        or eval_strategy not in valid_states
        or evaluation_strategy not in valid_states
    ):
        raise RuntimeError("TrainingArguments evaluation alias support is unknown")
    if eval_strategy == "unknown" or evaluation_strategy == "unknown":
        raise RuntimeError("TrainingArguments evaluation alias support is unknown")
    if eval_strategy == "supported":
        return "eval_strategy"
    if evaluation_strategy == "supported":
        return "evaluation_strategy"
    raise RuntimeError(
        "TrainingArguments supports neither eval_strategy nor evaluation_strategy"
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the signature-probe CLI without importing transformers."""

    parser = SafeArgumentParser(
        description="Inspect installed Hugging Face TrainingArguments keyword support"
    )
    parser.add_argument(
        "--fields",
        type=parse_requested_fields,
        default=DEFAULT_REQUESTED_FIELDS,
        help="Comma-separated TrainingArguments field names to inspect",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    module_loader: Callable[[str], Any] = importlib.import_module,
    signature_inspector: Callable[[Any], inspect.Signature] = inspect.signature,
) -> int:
    """Emit bounded JSON; nonzero means constructor support was not fully inspectable."""

    args = build_parser().parse_args(argv)
    result = probe_training_arguments(
        args.fields,
        module_loader=module_loader,
        signature_inspector=signature_inspector,
    )
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
