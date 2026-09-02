"""Closed conservative privacy scanner for MLflow input-example columns."""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any

_COLUMN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_COLUMN_NAME_LENGTH = 128
_IP_VERSION = re.compile(r"(?i)ipv([46])")
_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")
_IDENTIFIER_TOKEN = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+[0-9]*|[0-9]+"
)
_TOKEN_ALIASES = {
    "addresses": "address",
    "authn": "authentication",
    "authz": "authorization",
    "contacts": "contact",
    "cookies": "cookie",
    "credentials": "credential",
    "customers": "customer",
    "devices": "device",
    "emails": "email",
    "employees": "employee",
    "grids": "grid",
    "ids": "id",
    "keys": "key",
    "macs": "mac",
    "names": "name",
    "passwords": "password",
    "phones": "phone",
    "rates": "rate",
    "records": "record",
    "scores": "score",
    "secrets": "secret",
    "sessions": "session",
    "tokens": "token",
    "transactions": "transaction",
    "users": "user",
    "uuids": "uuid",
    "valids": "valid",
}
_COMPACT_COMPOUND_TOKENS = {
    "driverlicense": ("driver", "license"),
    "driverlicensenumber": ("driver", "license", "number"),
    "passportnumber": ("passport", "number"),
    "socialsecurity": ("social", "security"),
    "socialsecuritynumber": ("social", "security", "number"),
}
_SENSITIVE_COMPOUND_SEQUENCES = frozenset(
    {
        ("driver", "license"),
        ("driver", "license", "number"),
        ("passport", "number"),
        ("social", "security"),
        ("social", "security", "number"),
    }
)

# Exact raw controls are the only allow decisions. Case is canonicalized, but
# separator and camel spellings are intentionally not rewritten here.
_EXACT_SAFE_RAW_CONTROLS = frozenset(
    {
        "valid",
        "grid",
        "hybrid_score",
        "candidate_id",
        "feature_id",
        "model_id",
        "feature_count",
        "feature_rate",
        "model_count",
        "model_rate",
    }
)
_AGGREGATE_SUFFIXES = frozenset({"count", "rate"})
_SENSITIVE_ROOTS = frozenset(
    {
        "account",
        "access",
        "address",
        "api",
        "auth",
        "authentication",
        "authorization",
        "bearer",
        "birth",
        "contact",
        "cookie",
        "credential",
        "customer",
        "device",
        "dob",
        "email",
        "employee",
        "id",
        "ip",
        "ipv4",
        "ipv6",
        "key",
        "mac",
        "name",
        "national",
        "passwd",
        "password",
        "passport",
        "person",
        "phone",
        "record",
        "refresh",
        "secret",
        "session",
        "ssn",
        "token",
        "transaction",
        "user",
        "uuid",
        "jwt",
    }
)
_SEGMENT_VOCABULARY = frozenset(
    {
        *_SENSITIVE_ROOTS,
        *_TOKEN_ALIASES,
        "candidate",
        "client",
        "contact",
        "count",
        "dest",
        "destination",
        "display",
        "feature",
        "first",
        "full",
        "hash",
        "header",
        "home",
        "hybrid",
        "last",
        "local",
        "mailing",
        "middle",
        "model",
        "number",
        "passport",
        "driver",
        "license",
        "rate",
        "remote",
        "score",
        "source",
        "street",
        "valid",
        "value",
    }
)


def _build_trie(vocabulary: frozenset[str]) -> dict[str, Any]:
    root: dict[str, Any] = {}
    for word in vocabulary:
        node = root
        for character in word:
            node = node.setdefault(character, {})
        node[""] = word
    return root


_SEGMENT_TRIE = _build_trie(_SEGMENT_VOCABULARY)


def _canonical_raw_control(column: str) -> str:
    return column.casefold()


def _tokenize_segment(segment: str) -> tuple[str, ...]:
    compact = segment.casefold()
    if compact in _COMPACT_COMPOUND_TOKENS:
        return _COMPACT_COMPOUND_TOKENS[compact]
    return tuple(
        _TOKEN_ALIASES.get(token.casefold(), token.casefold())
        for token in _IDENTIFIER_TOKEN.findall(segment)
    )


def _normalize_tokens(tokens: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    index = 0
    while index < len(tokens):
        if tokens[index] == "auth" and index + 1 < len(tokens):
            next_token = tokens[index + 1]
            if next_token == "n":
                normalized.append("authentication")
                index += 2
                continue
            if next_token == "z":
                normalized.append("authorization")
                index += 2
                continue
        normalized.append(tokens[index])
        index += 1
    return tuple(normalized)


def _tokenize_column_name(column: str) -> tuple[str, ...]:
    normalized_versions = _IP_VERSION.sub(r"_ipv\1_", column)
    raw_tokens = tuple(
        token
        for segment in _SEPARATOR.split(normalized_versions)
        for token in _tokenize_segment(segment)
    )
    return _normalize_tokens(raw_tokens)


def _is_exact_safe_raw_control(raw_control: str) -> bool:
    if raw_control in _EXACT_SAFE_RAW_CONTROLS:
        return True
    root, separator, suffix = raw_control.partition("_")
    return (
        bool(separator)
        and "_" not in suffix
        and root in _SENSITIVE_ROOTS
        and suffix in _AGGREGATE_SUFFIXES
    )


def _is_safe_control_shape(tokens: tuple[str, ...]) -> bool:
    if tokens in {
        ("valid",),
        ("grid",),
        ("hybrid", "score"),
        ("candidate", "id"),
        ("feature", "id"),
        ("model", "id"),
        ("feature", "count"),
        ("feature", "rate"),
        ("model", "count"),
        ("model", "rate"),
    }:
        return True
    return (
        len(tokens) == 2
        and tokens[0] in _SENSITIVE_ROOTS
        and tokens[1] in _AGGREGATE_SUFFIXES
    )


def _segment_contains_sensitive_root(compact: str) -> bool:
    """Use iterative DP/trie traversal to find root-bearing full segmentations."""

    states: list[set[bool]] = [set() for _ in range(len(compact) + 1)]
    states[0].add(False)
    for start, root_seen_states in enumerate(states[:-1]):
        if not root_seen_states:
            continue
        node = _SEGMENT_TRIE
        for end in range(start, len(compact)):
            child = node.get(compact[end])
            if not isinstance(child, dict):
                break
            node = child
            word = node.get("")
            if not isinstance(word, str):
                continue
            for root_seen in root_seen_states:
                normalized_word = _TOKEN_ALIASES.get(word, word)
                states[end + 1].add(root_seen or normalized_word in _SENSITIVE_ROOTS)
    return True in states[-1]


def _is_sensitive_column_name(column: str) -> bool:
    raw_control = _canonical_raw_control(column)
    if _is_exact_safe_raw_control(raw_control):
        return False
    tokens = _tokenize_column_name(column)
    if _is_safe_control_shape(tokens):
        return True
    if any(sequence == tokens for sequence in _SENSITIVE_COMPOUND_SEQUENCES):
        return True
    if any(token in _SENSITIVE_ROOTS for token in tokens):
        return True
    compact_segments = _SEPARATOR.split(column)
    return any(
        _segment_contains_sensitive_root(segment.casefold())
        for segment in compact_segments
        if segment
    )


def _validate_column_container(columns: Any) -> list[str] | tuple[str, ...]:
    if isinstance(columns, (str, bytes)) or not isinstance(columns, (list, tuple)):
        raise ValueError("input-example columns must be a list or tuple")
    if any(
        not isinstance(column, str)
        or len(column) > _MAX_COLUMN_NAME_LENGTH
        or not _COLUMN_NAME.fullmatch(column)
        for column in columns
    ):
        raise ValueError("input-example column names must use the configured grammar")
    return columns


def find_sensitive_columns(columns: Any) -> tuple[str, ...]:
    """Return columns denied by the closed conservative privacy taxonomy."""

    valid_columns = _validate_column_container(columns)
    return tuple(
        sorted(
            {column for column in valid_columns if _is_sensitive_column_name(column)}
        )
    )


def validate_input_example_columns(columns: Any) -> None:
    """Reject sensitive names before human review of synthetic/redacted values."""

    if find_sensitive_columns(columns):
        raise ValueError(
            "input example columns include direct identifiers or credential-like fields"
        )
