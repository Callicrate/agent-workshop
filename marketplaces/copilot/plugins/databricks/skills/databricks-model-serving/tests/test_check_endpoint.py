"""Network-free contract tests for the model-serving endpoint doctor."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import io
import json
import math
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_endpoint.py"
SCHEMA = Path(__file__).parents[1] / "assets" / "endpoint-config-schema.json"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("check_endpoint", SCRIPT)
assert SPEC and SPEC.loader
check_endpoint = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_endpoint
SPEC.loader.exec_module(check_endpoint)

import logical_types  # noqa: E402


def valid_contract() -> dict[str, object]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return schema["examples"][0]


def standard_contract() -> dict[str, object]:
    contract = copy.deepcopy(valid_contract())
    target = (
        "https://example.cloud.databricks.com/serving-endpoints/"
        "risk-model-prod/invocations"
    )
    contract["endpoint"]["route_optimized"] = False  # type: ignore[index]
    contract["endpoint"]["target_url"] = target  # type: ignore[index]
    contract["target_manifest"]["target_url"] = target  # type: ignore[index]
    del contract["production_auth"]["route_optimized_transport"]  # type: ignore[index]
    return contract


def temporal_binary_contract() -> dict[str, object]:
    contract = standard_contract()
    declarations = [
        {"name": "event_date", "logical_type": "date", "nullable": False},
        {"name": "event_timestamp", "logical_type": "timestamp", "nullable": False},
        {"name": "payload_bytes", "logical_type": "binary", "nullable": False},
    ]
    contract["model_handoff"]["signature"]["inputs"] = copy.deepcopy(declarations)  # type: ignore[index]
    contract["model_handoff"]["feature_schema"]["features"] = copy.deepcopy(  # type: ignore[index]
        declarations
    )
    contract["model_handoff"]["input_example"]["columns"] = [  # type: ignore[index]
        declaration["name"] for declaration in declarations
    ]
    contract["request_contract"]["input_schema"] = copy.deepcopy(declarations)  # type: ignore[index]
    return contract


def temporal_binary_output_contract() -> dict[str, object]:
    output = copy.deepcopy(valid_contract()["output_contract"])
    declarations = [
        {"name": "event_date", "logical_type": "date", "nullable": False},
        {"name": "event_timestamp", "logical_type": "timestamp", "nullable": False},
        {"name": "payload_bytes", "logical_type": "binary", "nullable": False},
    ]
    output["response_schema"] = declarations
    output["required_response_fields"] = [
        declaration["name"] for declaration in declarations
    ]
    output["nullable_response_fields"] = []
    output["score_fields"] = []
    output["label_fields"] = []
    output["semantic_assertions"] = [
        {"kind": "predicate", "field": declaration["name"], "operator": "not_null"}
        for declaration in declarations
    ]
    output["minimum_non_fallback_rate"] = 0.0
    output["allow_identical_outputs"] = True
    return output


class State:
    def __init__(
        self, ready: str = "READY", config_update: str = "NOT_UPDATING"
    ) -> None:
        self.ready = ready
        self.config_update = config_update


class Config:
    def __init__(self, routes: list[object] | None = None) -> None:
        self.served_entities = [
            types.SimpleNamespace(
                name="risk-model-v7",
                entity_name="main.ml.risk_model",
                entity_version="7",
                workload_size="Medium",
                workload_type="CPU",
                scale_to_zero_enabled=False,
            )
        ]
        self.config_version = 7
        self.traffic_config = types.SimpleNamespace(
            routes=routes
            or [
                types.SimpleNamespace(
                    name="risk-model-v7",
                    served_entity_name="risk-model-v7",
                    traffic_percentage=100,
                )
            ]
        )


class Endpoint:
    def __init__(
        self,
        ready: str = "READY",
        routes: list[object] | None = None,
        route_optimized: bool = True,
    ) -> None:
        self.name = "endpoint"
        self.state = State(ready)
        self.config = Config(routes)
        self.name = "risk-model-prod"
        self.pending_config = None
        self.creator = "serving-runtime-prod"
        self.route_optimized = route_optimized
        self.endpoint_url = (
            "https://abc123.serving.cloud.databricks.com/123456789/serving-endpoints/risk-model-prod/invocations"
            if route_optimized
            else None
        )
        self.telemetry_config = types.SimpleNamespace(
            table_names=types.SimpleNamespace(
                logs_table="main.observability.risk_model_otel_logs",
                metrics_table="main.observability.risk_model_otel_metrics",
                traces_table="main.observability.risk_model_otel_spans",
            ),
            inference_table_config=types.SimpleNamespace(sampling_fraction=1.0),
            enabled_telemetry_features=[
                "TELEMETRY_FEATURE_LOGS",
                "TELEMETRY_FEATURE_TRACES",
                "TELEMETRY_FEATURE_METRICS",
                "TELEMETRY_FEATURE_INFERENCE_TABLE",
            ],
        )


class Service:
    def __init__(
        self, endpoint: Endpoint | None = None, response: object | None = None
    ) -> None:
        self.endpoint = endpoint or Endpoint()
        self.response = (
            response
            if response is not None
            else {"predictions": [{"score": 0.8, "label": "ok"}]}
        )
        self.query_calls = 0
        self.get_calls = 0

    def get(self, name: str) -> Endpoint:
        self.get_calls += 1
        return self.endpoint

    def query(self, *, name: str, **payload: object) -> object:
        self.query_calls += 1
        return self.response


class Client:
    def __init__(
        self,
        service: Service | None = None,
        host: str = "https://example.cloud.databricks.com",
    ) -> None:
        self.serving_endpoints = service or Service()
        self.config = types.SimpleNamespace(host=host)


def make_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "endpoint_name": "risk-model-prod",
        "version": None,
        "nullable_field": [],
        "score_range": [],
        "forbid_label": [],
        "allowed_label": [],
        "fallback_label": [],
        "required_field": [],
        "label_field": [],
        "expected_non_fallback_rate": None,
        "allow_identical_outputs": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_event_collection_consumes_only_limit_plus_one() -> None:
    consumed: list[int] = []

    def events(*, name: str):
        for value in range(20):
            consumed.append(value)
            yield {"message": f"event {value}"}

    service = Service()
    service.list_events = events  # type: ignore[attr-defined]
    result = check_endpoint.list_endpoint_events(Client(service), "endpoint", 2)

    assert consumed == [0, 1, 2]
    assert result["event_count"] == 2
    assert result["truncated"] is True


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        (b'{"x":1,"x":2}', "duplicate"),
        (b'{"x":NaN}', "non-finite"),
        (b"\xff", "UTF-8"),
    ],
)
def test_whole_json_rejects_unsafe_content(
    tmp_path: Path, contents: bytes, expected: str
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(contents)
    with pytest.raises(check_endpoint.SampleInputError, match=expected):
        check_endpoint.load_sample_payload(str(fixture), None)


def test_whole_json_rejects_oversize_and_deep_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(b"{" + b'"a":' + b'"x"' * 50 + b"}")
    monkeypatch.setattr(check_endpoint, "MAX_SAMPLE_BYTES", 4)
    with pytest.raises(check_endpoint.SampleInputError, match="byte"):
        check_endpoint.load_sample_payload(str(fixture), None)

    monkeypatch.setattr(check_endpoint, "MAX_SAMPLE_BYTES", 100_000)
    monkeypatch.setattr(check_endpoint, "MAX_JSON_DEPTH", 2)
    fixture.write_text('{"a":{"b":{"c":1}}}', encoding="utf-8")
    with pytest.raises(check_endpoint.SampleInputError, match="depth"):
        check_endpoint.load_sample_payload(str(fixture), None)


def test_jsonl_streaming_rejects_non_object_duplicate_utf8_and_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_bytes(b'{"a":1}\n[1]\n')
    with pytest.raises(check_endpoint.SampleInputError, match="object"):
        check_endpoint.load_sample_payload(None, str(fixture))

    fixture.write_bytes(b'{"a":1,"a":2}\n')
    with pytest.raises(check_endpoint.SampleInputError, match="duplicate"):
        check_endpoint.load_sample_payload(None, str(fixture))

    fixture.write_bytes(b"\xff\n")
    with pytest.raises(check_endpoint.SampleInputError, match="UTF-8"):
        check_endpoint.load_sample_payload(None, str(fixture))

    monkeypatch.setattr(check_endpoint, "MAX_SAMPLE_RECORDS", 1)
    fixture.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    with pytest.raises(check_endpoint.SampleInputError, match="record-count"):
        check_endpoint.load_sample_payload(None, str(fixture))


def test_jsonl_rejects_per_record_and_total_byte_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "fixture.jsonl"
    monkeypatch.setattr(check_endpoint, "MAX_JSONL_RECORD_BYTES", 8)
    fixture.write_text('{"value":"too long"}\n', encoding="utf-8")
    with pytest.raises(check_endpoint.SampleInputError, match="per-record"):
        check_endpoint.load_sample_payload(None, str(fixture))

    monkeypatch.setattr(check_endpoint, "MAX_JSONL_RECORD_BYTES", 100)
    monkeypatch.setattr(check_endpoint, "MAX_JSONL_TOTAL_BYTES", 10)
    fixture.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
    with pytest.raises(check_endpoint.SampleInputError, match="total-byte"):
        check_endpoint.load_sample_payload(None, str(fixture))


def test_object_to_dict_bounds_deep_and_cyclic_values() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    converted = check_endpoint.object_to_dict(cyclic)
    assert "cycle" in converted[0]

    value: object = "leaf"
    for _ in range(10):
        value = [value]
    converted = check_endpoint.object_to_dict(value, max_depth=2)
    assert "depth" in json.dumps(converted)


def test_report_and_human_output_do_not_contain_response_or_exception_secret() -> None:
    secret = "Bearer cred-should-not-appear"
    response = {"predictions": [{"label": secret, "score": 0.9}]}
    service = Service(response=response)
    checks = check_endpoint.check_semantic_response(
        "endpoint",
        Client(service),
        {"dataframe_records": [{"input": "private"}]},
        make_args(label_field=["label"]),
    )
    checks.append(
        check_endpoint.failed_check("other", "A check failed", RuntimeError(secret))
    )
    events = {"available": False, "error": {"type": "RuntimeError"}}
    report = check_endpoint.build_report(
        "endpoint", "profile", "https://workspace.example", Endpoint(), checks, events
    )

    serialized = json.dumps(report)
    assert "cred-should-not-appear" not in serialized
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        check_endpoint.print_report(report)
    assert "cred-should-not-appear" not in stdout.getvalue()


def test_event_report_is_value_free_for_urls_tokens_and_unicode_content() -> None:
    event = {
        "message": "https://private.example/path?token=super-secret",
        "authorization": "Bearer abc",
        "event_type": "unkeyed-token-βeta-秘密",
    }
    excerpt = check_endpoint.event_excerpt(event)
    assert excerpt == {"kind": "object"}

    def events(*, name: str):
        yield event

    service = Service()
    service.list_events = events  # type: ignore[attr-defined]
    events_report = check_endpoint.list_endpoint_events(Client(service), "endpoint", 1)
    report = check_endpoint.build_report(
        "endpoint",
        "profile",
        "https://workspace.example",
        Endpoint(),
        [check_endpoint.event_check(events_report, False)],
        events_report,
    )
    serialized = json.dumps(report, ensure_ascii=False)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        check_endpoint.print_report(report)
    for value in ("private.example", "super-secret", "abc", "βeta", "秘密"):
        assert value not in serialized
        assert value not in stdout.getvalue()
    assert report["events"]["event_kind_counts"] == {"object": 1}


@pytest.mark.parametrize(
    "payload",
    [
        {"dataframe_records": [{"a": 1}, {"a": 2}]},
        {"dataframe_split": {"data": [[1], [2]]}},
        {"instances": [1, 2]},
        {"inputs": [1, 2]},
        {"messages": [{"role": "user"}, {"role": "assistant"}]},
    ],
)
def test_direct_query_shapes_enforce_total_record_limit_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(check_endpoint, "MAX_SAMPLE_RECORDS", 1)
    with pytest.raises(check_endpoint.SampleInputError, match="record-count"):
        check_endpoint.load_sample_payload(str(fixture), None)


@pytest.mark.parametrize(
    "payload",
    [
        {"dataframe_records": [1]},
        {"dataframe_split": {"data": [1]}},
        {"instances": "not-an-array"},
        {"inputs": []},
        {"messages": ["not-an-object"]},
    ],
)
def test_direct_query_shapes_reject_malformed_records(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(check_endpoint.SampleInputError):
        check_endpoint.load_sample_payload(str(fixture), None)


def test_direct_query_records_enforce_per_object_and_scalar_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "fixture.json"
    monkeypatch.setattr(check_endpoint, "MAX_OBJECT_ITEMS", 1)
    fixture.write_text('{"dataframe_records":[{"a":1,"b":2}]}', encoding="utf-8")
    with pytest.raises(check_endpoint.SampleInputError, match="field budget"):
        check_endpoint.load_sample_payload(str(fixture), None)
    fixture.write_text('{"instances":[[1,2]]}', encoding="utf-8")
    with pytest.raises(check_endpoint.SampleInputError, match="item budget"):
        check_endpoint.load_sample_payload(str(fixture), None)
    fixture.write_text('{"dataframe_split":{"data":[[1]],"extra":0}}', encoding="utf-8")
    with pytest.raises(check_endpoint.SampleInputError, match="field budget"):
        check_endpoint.load_sample_payload(str(fixture), None)


def test_exception_metadata_has_only_type_and_allowlisted_status_fields() -> None:
    class EndpointError(RuntimeError):
        status_code = 503
        error_code = "TEMPORARILY_UNAVAILABLE"

    details = check_endpoint.exception_metadata(EndpointError("Bearer private-value"))
    assert details == {
        "type": "EndpointError",
        "status_code": 503,
        "error_code": "TEMPORARILY_UNAVAILABLE",
    }


def test_query_type_error_is_one_attempt_only() -> None:
    class TypeErrorService(Service):
        def query(self, *, name: str, **payload: object) -> object:
            self.query_calls += 1
            raise TypeError("Bearer not-to-be-retried")

    service = TypeErrorService()
    result = check_endpoint.check_semantic_response(
        "endpoint", Client(service), {"dataframe_records": [{"x": 1}]}, make_args()
    )
    assert service.query_calls == 1
    assert result[0].passed is False
    assert "not-to-be-retried" not in json.dumps(
        check_endpoint.check_to_dict(result[0])
    )


@pytest.mark.parametrize(
    "value", ["score:nan:1", "score:0:inf", "score:2:1", "score:zero:one"]
)
def test_score_range_rejects_nonfinite_or_reversed_bounds(value: str) -> None:
    with pytest.raises(ValueError):
        check_endpoint.parse_score_range(value)


def test_score_and_rate_assertions_reject_bool_nonfinite_and_undeclared_label_rate() -> (
    None
):
    failures = check_endpoint.check_score_ranges(
        [{"score": True}, {"score": math.nan}], [("score", 0.0, 1.0)]
    )
    assert len(failures) == 2
    args = make_args(
        wait_ready=None,
        poll_interval=15,
        event_limit=10,
        http_timeout_seconds=60,
        retry_timeout_seconds=60,
        score_range=[],
        expected_non_fallback_rate=0.5,
        label_field=[],
        profile="profile",
        workspace_host=None,
    )
    with pytest.raises(ValueError, match="label-field"):
        check_endpoint.validate_args(args, True, valid_contract())
    args.label_field = ["label"]
    args.expected_non_fallback_rate = math.nan
    with pytest.raises(ValueError, match="finite"):
        check_endpoint.validate_args(args, True, valid_contract())
    args.expected_non_fallback_rate = 0.5
    args.retry_timeout_seconds = 61
    with pytest.raises(ValueError, match="must not exceed"):
        check_endpoint.validate_args(args, True, valid_contract())


def test_non_fallback_rate_uses_only_declared_label_fields() -> None:
    records = [
        {"label": "OK", "other": "NO_OPINION"},
        {"label": "NO_OPINION", "other": "OK"},
    ]
    assert check_endpoint.non_fallback_rate(records, ["label"], {"no_opinion"}) == 0.5


def test_wait_ready_stops_at_deadline_without_oversleep_or_extra_call() -> None:
    now = [0.0]
    sleeps: list[float] = []
    service = Service(endpoint=Endpoint("PENDING"))

    def monotonic() -> float:
        return now[0]

    def sleep(duration: float) -> None:
        sleeps.append(duration)
        now[0] += duration

    result = check_endpoint.wait_for_ready(
        "endpoint", Client(service), 2, 5, monotonic=monotonic, sleep=sleep
    )
    assert result.passed is False
    assert service.get_calls == 1
    assert sleeps == [2.0]


def test_wait_ready_rejects_nonpositive_direct_durations() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        check_endpoint.wait_for_ready("endpoint", Client(), 0, 1)
    with pytest.raises(ValueError, match="poll_interval_seconds"):
        check_endpoint.wait_for_ready("endpoint", Client(), 1, 0)


def test_build_client_passes_explicit_sdk_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class WorkspaceClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    databricks = types.ModuleType("databricks")
    sdk = types.ModuleType("databricks.sdk")
    sdk.WorkspaceClient = WorkspaceClient  # type: ignore[attr-defined]
    databricks.sdk = sdk  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "databricks", databricks)
    monkeypatch.setitem(sys.modules, "databricks.sdk", sdk)
    args = argparse.Namespace(
        profile="explicit", http_timeout_seconds=12, retry_timeout_seconds=34
    )
    check_endpoint.build_client(args)
    assert captured == {
        "profile": "explicit",
        "http_timeout_seconds": 12,
        "retry_timeout_seconds": 34,
    }


def test_events_are_warning_by_default_and_required_when_requested() -> None:
    unavailable = {"available": False, "error": {"type": "RuntimeError"}}
    optional = check_endpoint.event_check(unavailable, False)
    required = check_endpoint.event_check(unavailable, True)
    report = check_endpoint.build_report(
        "endpoint", None, None, None, [optional], unavailable
    )
    assert optional.passed is True and optional.warning is True
    assert required.passed is False and required.warning is False
    assert report["passed"] is True and report["complete"] is False
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        check_endpoint.print_report(report)
    assert "CHECKS PASSED WITH WARNINGS" in stdout.getvalue()
    assert "ALL CHECKS PASSED" not in stdout.getvalue()


def test_schema_and_exit_codes_are_deterministic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    service = Service(endpoint=Endpoint(route_optimized=False))
    client = Client(service)
    monkeypatch.setattr(check_endpoint, "build_client", lambda args: client)
    monkeypatch.setattr(
        check_endpoint, "load_contract", lambda path: standard_contract()
    )

    code = check_endpoint.main(
        [
            "risk-model-prod",
            "--contract",
            "contract.json",
            "--profile",
            "prod-serving",
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["schema_version"] == 4
    assert report["passed"] is True
    assert report["complete"] is False

    code = check_endpoint.main(
        [
            "risk-model-prod",
            "--contract",
            "contract.json",
            "--profile",
            "prod-serving",
            "--require-events",
            "--json",
        ]
    )
    assert code == 1
    assert (
        check_endpoint.main(
            ["risk-model-prod", "--contract", "contract.json", "--event-limit", "0"]
        )
        == 2
    )


def test_fixture_target_requires_explicit_profile_or_matching_host_manifest() -> None:
    args = make_args(
        wait_ready=None,
        poll_interval=15,
        event_limit=10,
        http_timeout_seconds=60,
        retry_timeout_seconds=60,
        score_range=[],
        expected_non_fallback_rate=None,
        label_field=[],
        profile=None,
        workspace_host=None,
    )
    with pytest.raises(ValueError, match="requires"):
        check_endpoint.validate_args(args, True, valid_contract())
    args.workspace_host = "https://example.cloud.databricks.com"
    check_endpoint.validate_args(args, True, valid_contract())
    with pytest.raises(ValueError, match="does not match"):
        check_endpoint.validate_workspace_target(
            args, "https://another.example", valid_contract()
        )

    args.profile = "DEFAULT"
    args.workspace_host = None
    with pytest.raises(ValueError, match="non-default"):
        check_endpoint.validate_args(args, True, valid_contract())
    args.workspace_host = "https://example.cloud.databricks.com"
    with pytest.raises(ValueError, match="profile argument"):
        check_endpoint.validate_args(args, True, valid_contract())


def test_default_profile_fixture_is_blocked_before_network_or_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"dataframe_records":[{"x":1}]}', encoding="utf-8")
    client_builds: list[object] = []
    monkeypatch.setattr(
        check_endpoint,
        "build_client",
        lambda args: client_builds.append(args),
    )
    monkeypatch.setattr(check_endpoint, "load_contract", lambda path: valid_contract())
    assert (
        check_endpoint.main(
            [
                "risk-model-prod",
                "--contract",
                "contract.json",
                "--profile",
                "DEFAULT",
                "--sample-json",
                str(fixture),
            ]
        )
        == 2
    )
    assert client_builds == []


def test_default_profile_fixture_never_queries_on_host_manifest_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"dataframe_records":[{"x":1}]}', encoding="utf-8")
    service = Service(endpoint=Endpoint(route_optimized=False))
    monkeypatch.setattr(
        check_endpoint,
        "build_client",
        lambda args: Client(service, host="https://other.example"),
    )
    monkeypatch.setattr(
        check_endpoint, "load_contract", lambda path: standard_contract()
    )
    assert (
        check_endpoint.main(
            [
                "risk-model-prod",
                "--contract",
                "contract.json",
                "--profile",
                "DEFAULT",
                "--workspace-host",
                "https://example.cloud.databricks.com",
                "--sample-json",
                str(fixture),
            ]
        )
        == 2
    )
    assert service.query_calls == 0


@pytest.mark.parametrize(
    "mutation,expected_kind",
    [
        (
            lambda endpoint: setattr(endpoint.state, "ready", "NOT_READY"),
            "endpoint_not_ready",
        ),
        (
            lambda endpoint: setattr(endpoint.state, "config_update", "UPDATE_FAILED"),
            "config_update_not_settled",
        ),
        (
            lambda endpoint: setattr(endpoint, "pending_config", object()),
            "pending_config_present",
        ),
        (
            lambda endpoint: setattr(endpoint.config, "config_version", 8),
            "config_version_mismatch",
        ),
        (
            lambda endpoint: setattr(endpoint, "telemetry_config", None),
            "telemetry_manifest_mismatch",
        ),
    ],
)
def test_snapshot_rejects_unsettled_or_manifest_mismatched_state(
    mutation: object, expected_kind: str
) -> None:
    endpoint = Endpoint()
    mutation(endpoint)  # type: ignore[operator]
    result = check_endpoint.check_endpoint_snapshot(endpoint, valid_contract())
    assert result.passed is False
    assert expected_kind in result.details["failure_kinds"]  # type: ignore[index]


def test_snapshot_requires_exact_entity_routes_scale_telemetry_and_url() -> None:
    endpoint = Endpoint()
    endpoint.config.served_entities[0].scale_to_zero_enabled = True
    endpoint.config.traffic_config.routes[0].traffic_percentage = 0
    endpoint.telemetry_config.inference_table_config.sampling_fraction = 0.5
    endpoint.endpoint_url = endpoint.endpoint_url.replace("risk-model-prod", "other")
    result = check_endpoint.check_endpoint_snapshot(endpoint, valid_contract())
    kinds = set(result.details["failure_kinds"])  # type: ignore[index]
    assert {
        "served_entity_manifest_mismatch",
        "traffic_manifest_mismatch",
        "telemetry_manifest_mismatch",
        "route_optimized_url_mismatch",
    }.issubset(kinds)


def test_snapshot_normalizes_documented_route_url_and_implicit_all_telemetry() -> None:
    endpoint = Endpoint()
    endpoint.endpoint_url = endpoint.endpoint_url.removeprefix("https://")
    endpoint.telemetry_config.enabled_telemetry_features = []
    result = check_endpoint.check_endpoint_snapshot(endpoint, valid_contract())
    assert result.passed is True


def test_snapshot_allows_disabled_telemetry_only_when_contract_is_explicit() -> None:
    endpoint = Endpoint()
    endpoint.telemetry_config = None
    contract = valid_contract()
    contract["telemetry"] = {"required": False, "mode": "disabled"}
    result = check_endpoint.check_endpoint_snapshot(endpoint, contract)
    assert result.passed is True

    enabled_endpoint = Endpoint()
    enabled_with_disabled_contract = check_endpoint.check_endpoint_snapshot(
        enabled_endpoint, contract
    )
    assert (
        "telemetry_enabled_but_expected_disabled"
        in enabled_with_disabled_contract.details[  # type: ignore[index]
            "failure_kinds"
        ]
    )

    contradictory = valid_contract()
    contradictory["telemetry"]["required"] = False  # type: ignore[index]
    endpoint.telemetry_config = None
    result = check_endpoint.check_endpoint_snapshot(endpoint, contradictory)
    assert (
        "telemetry_contract_invalid"
        in result.details[  # type: ignore[index]
            "failure_kinds"
        ]
    )

    incomplete = valid_contract()
    del incomplete["telemetry"]["table_names"]  # type: ignore[index]
    result = check_endpoint.check_endpoint_snapshot(Endpoint(), incomplete)
    assert (
        "telemetry_contract_invalid"
        in result.details[  # type: ignore[index]
            "failure_kinds"
        ]
    )


def test_doctor_fetches_exactly_one_post_wait_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    service = Service(endpoint=Endpoint(route_optimized=False))
    client = Client(service)
    monkeypatch.setattr(check_endpoint, "build_client", lambda args: client)
    monkeypatch.setattr(
        check_endpoint, "load_contract", lambda path: standard_contract()
    )
    result = check_endpoint.main(
        [
            "risk-model-prod",
            "--contract",
            "contract.json",
            "--profile",
            "prod-serving",
            "--wait-ready",
            "1",
            "--json",
        ]
    )
    capsys.readouterr()
    assert result == 0
    assert service.get_calls == 2


def test_doctor_without_wait_uses_one_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    service = Service(endpoint=Endpoint(route_optimized=False))
    client = Client(service)
    monkeypatch.setattr(check_endpoint, "build_client", lambda args: client)
    monkeypatch.setattr(
        check_endpoint, "load_contract", lambda path: standard_contract()
    )
    result = check_endpoint.main(
        [
            "risk-model-prod",
            "--contract",
            "contract.json",
            "--profile",
            "prod-serving",
            "--json",
        ]
    )
    capsys.readouterr()
    assert result == 0
    assert service.get_calls == 1


@pytest.mark.parametrize("response", [None, {}, [], ""])
def test_none_and_empty_semantic_responses_fail(response: object) -> None:
    service = Service()
    service.response = response
    result = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {"dataframe_records": [{"amount": 1, "country_code": "US"}]},
        make_args(),
        valid_contract()["output_contract"],  # type: ignore[arg-type,index]
    )
    assert result[0].passed is False
    assert result[0].details["response_shape"]["record_count"] == 0  # type: ignore[index]


def test_semantic_report_never_contains_raw_response_fields_or_values() -> None:
    secret_field = "private_secret_response_field"
    secret_value = "private-secret-response-value"
    service = Service(
        response={
            "predictions": [
                {"label": "ALLOW", "risk_score": 0.2, secret_field: secret_value}
            ]
        }
    )
    checks = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {"dataframe_records": [{"amount": 1, "country_code": "US"}]},
        make_args(),
        valid_contract()["output_contract"],  # type: ignore[arg-type,index]
    )
    rendered = json.dumps([check_endpoint.check_to_dict(item) for item in checks])
    assert secret_field not in rendered
    assert secret_value not in rendered
    assert "risk_score" not in rendered


def test_runtime_response_schema_rejects_string_score() -> None:
    service = Service(
        response={"predictions": [{"label": "ALLOW", "risk_score": "0.2"}]}
    )
    checks = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {"dataframe_records": [{"amount": 1.0, "country_code": "US"}]},
        make_args(),
        valid_contract()["output_contract"],  # type: ignore[arg-type,index]
    )
    assertions = next(check for check in checks if check.name == "semantic_assertions")
    assert assertions.passed is False
    assert (
        "response_schema_type_mismatch"
        in assertions.details[  # type: ignore[index]
            "failure_kinds"
        ]
    )


def test_fixture_cannot_run_without_closed_contract() -> None:
    assert check_endpoint.main(["risk-model-prod", "--profile", "prod-serving"]) == 2


def test_route_optimized_state_and_url_are_exact_in_both_directions() -> None:
    endpoint = Endpoint(route_optimized=False)
    optimized_result = check_endpoint.check_endpoint_snapshot(
        endpoint, valid_contract()
    )
    assert (
        "route_optimized_state_mismatch"
        in optimized_result.details[  # type: ignore[index]
            "failure_kinds"
        ]
    )

    endpoint = Endpoint(route_optimized=True)
    standard_result = check_endpoint.check_endpoint_snapshot(
        endpoint, standard_contract()
    )
    kinds = set(standard_result.details["failure_kinds"])  # type: ignore[index]
    assert {"route_optimized_state_mismatch", "unexpected_endpoint_url"}.issubset(kinds)

    endpoint = Endpoint(route_optimized=False)
    endpoint.endpoint_url = (
        "https://abc123.serving.cloud.databricks.com/123456789/"
        "serving-endpoints/risk-model-prod/invocations"
    )
    unexpected_url = check_endpoint.check_endpoint_snapshot(
        endpoint, standard_contract()
    )
    assert (
        "unexpected_endpoint_url"
        in unexpected_url.details[  # type: ignore[index]
            "failure_kinds"
        ]
    )


def test_route_optimized_transport_is_unverified_and_blocks_fixture_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        '{"dataframe_records":[{"amount":1.0,"country_code":"US"}]}',
        encoding="utf-8",
    )
    service = Service()
    monkeypatch.setattr(check_endpoint, "build_client", lambda args: Client(service))
    monkeypatch.setattr(check_endpoint, "load_contract", lambda path: valid_contract())
    result = check_endpoint.main(
        [
            "risk-model-prod",
            "--contract",
            "contract.json",
            "--profile",
            "prod-serving",
            "--sample-json",
            str(fixture),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    transport = next(
        check for check in report["checks"] if check["name"] == "route_transport"
    )
    assert result == 1
    assert transport["passed"] is False
    assert transport["details"]["verification"] == "unverified"
    assert service.query_calls == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"dataframe_records": [{"amount": 1.0}]},
        {
            "dataframe_records": [
                {"amount": 1.0, "country_code": "US", "extra": "blocked"}
            ]
        },
        {"dataframe_records": [{"amount": None, "country_code": "US"}]},
        {"dataframe_records": [{"amount": 1.0, "country_code": 7}]},
        {
            "dataframe_split": {
                "columns": ["country_code", "amount"],
                "data": [["US", 1.0]],
            }
        },
        {
            "dataframe_split": {
                "columns": ["amount", "country_code"],
                "data": [[1.0]],
            }
        },
        {"instances": [[1.0, "US"]]},
        {"inputs": [[1.0, "US"]]},
        {"messages": [{"role": "user", "content": "opaque"}]},
        {"prompt": "opaque"},
    ],
)
def test_fixture_binding_fails_before_client_construction(
    payload: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    builds: list[object] = []
    monkeypatch.setattr(
        check_endpoint, "load_contract", lambda path: standard_contract()
    )
    monkeypatch.setattr(
        check_endpoint, "build_client", lambda args: builds.append(args)
    )
    assert (
        check_endpoint.main(
            [
                "risk-model-prod",
                "--contract",
                "contract.json",
                "--profile",
                "prod-serving",
                "--sample-json",
                str(fixture),
            ]
        )
        == 2
    )
    assert builds == []


def test_fixture_binding_accepts_exact_records_and_split_shapes() -> None:
    contract = standard_contract()
    records = check_endpoint.bind_fixture_payload(
        {
            "dataframe_records": [
                {"amount": 1.0, "country_code": "US"},
                {"amount": 2, "country_code": "CA"},
            ]
        },
        contract,
    )
    split = check_endpoint.bind_fixture_payload(
        {
            "dataframe_split": {
                "columns": ["amount", "country_code"],
                "data": [[1.0, "US"], [2, "CA"]],
            }
        },
        contract,
    )
    assert records.input_row_count == split.input_row_count == 2
    assert {records.request_shape, split.request_shape} == {
        "dataframe_records",
        "dataframe_split",
    }


def test_rowwise_response_cardinality_rejects_two_inputs_one_output() -> None:
    service = Service(response={"predictions": [{"label": "ALLOW", "risk_score": 0.2}]})
    checks = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {
            "dataframe_records": [
                {"amount": 1.0, "country_code": "US"},
                {"amount": 2.0, "country_code": "CA"},
            ]
        },
        make_args(),
        valid_contract()["output_contract"],  # type: ignore[arg-type,index]
        check_endpoint.FixtureBinding(
            "dataframe_records", 2, "one_output_per_input_row"
        ),
    )
    cardinality = next(
        check for check in checks if check.name == "response_cardinality"
    )
    assert cardinality.passed is False

    without_row_contract = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {"messages": [{"role": "user", "content": "future-shape"}]},
        make_args(),
        valid_contract()["output_contract"],  # type: ignore[arg-type,index]
    )
    assert all(check.name != "response_cardinality" for check in without_row_contract)


@pytest.mark.parametrize(
    "mode,expected_fields,actual_fields",
    [
        (
            "provisioned_concurrency",
            {"min_provisioned_concurrency": 4, "max_provisioned_concurrency": 8},
            {"min_provisioned_concurrency": 400, "max_provisioned_concurrency": 800},
        ),
        (
            "provisioned_throughput",
            {"min_provisioned_throughput": 970, "max_provisioned_throughput": 1960},
            {"min_provisioned_throughput": 9700, "max_provisioned_throughput": 19600},
        ),
        (
            "provisioned_model_units",
            {"provisioned_model_units": 100, "burst_scaling_enabled": False},
            {"provisioned_model_units": 101, "burst_scaling_enabled": True},
        ),
    ],
)
def test_snapshot_compares_every_scaling_mode_field_exactly(
    mode: str,
    expected_fields: dict[str, object],
    actual_fields: dict[str, object],
) -> None:
    contract = valid_contract()
    expected = contract["deployment"]["served_entities"][0]  # type: ignore[index]
    expected.pop("workload_size")
    expected["scaling_mode"] = mode
    expected.update(expected_fields)

    endpoint = Endpoint()
    actual = endpoint.config.served_entities[0]
    delattr(actual, "workload_size")
    for name, value in expected_fields.items():
        setattr(actual, name, value)
    assert check_endpoint.check_endpoint_snapshot(endpoint, contract).passed is True
    for name, value in actual_fields.items():
        setattr(actual, name, value)
    result = check_endpoint.check_endpoint_snapshot(endpoint, contract)
    assert (
        "served_entity_manifest_mismatch"
        in result.details[  # type: ignore[index]
            "failure_kinds"
        ]
    )


def sensitive_numeric_output_contract() -> dict[str, object]:
    output = copy.deepcopy(valid_contract()["output_contract"])
    output["response_schema"] = [
        {"name": "token_count", "logical_type": "integer", "nullable": False},
        {
            "name": "authorization_score",
            "logical_type": "double",
            "nullable": False,
        },
    ]
    output["required_response_fields"] = ["token_count", "authorization_score"]
    output["nullable_response_fields"] = []
    output["score_fields"] = [
        {
            "field": "authorization_score",
            "minimum": 0.0,
            "maximum": 1.0,
            "direction": "higher_is_riskier",
            "units": "probability",
        }
    ]
    output["label_fields"] = []
    output["semantic_assertions"] = [
        {
            "kind": "score_range",
            "field": "authorization_score",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        {
            "kind": "predicate",
            "field": "token_count",
            "operator": ">=",
            "value": 0,
        },
    ]
    output["minimum_non_fallback_rate"] = 0.0
    output["allow_identical_outputs"] = True
    return output


def test_raw_sensitive_named_numeric_fields_pass_before_report_redaction() -> None:
    token_value = 8_675_309
    score_value = 0.731234
    service = Service(
        response={
            "predictions": [
                {
                    "token_count": token_value,
                    "authorization_score": score_value,
                }
            ]
        }
    )
    checks = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {"dataframe_records": [{"amount": 1.0, "country_code": "US"}]},
        make_args(),
        sensitive_numeric_output_contract(),  # type: ignore[arg-type]
    )
    assert all(check.passed for check in checks)
    rendered = json.dumps([check_endpoint.check_to_dict(check) for check in checks])
    assert "token_count" not in rendered
    assert "authorization_score" not in rendered
    assert str(token_value) not in rendered
    assert str(score_value) not in rendered


@pytest.mark.parametrize(
    "observed_count,expected_pass",
    [(99, False), (100, True), (101, False)],
)
def test_cardinality_uses_exact_count_before_any_report_cap(
    observed_count: int, expected_pass: bool
) -> None:
    responses = [
        {"label": "ALLOW", "risk_score": index / 100} for index in range(observed_count)
    ]
    service = Service(response={"predictions": responses})
    checks = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {"dataframe_records": [{"amount": 1.0, "country_code": "US"}]},
        make_args(),
        valid_contract()["output_contract"],  # type: ignore[arg-type,index]
        check_endpoint.FixtureBinding(
            "dataframe_records", 100, "one_output_per_input_row"
        ),
    )
    cardinality = next(
        check for check in checks if check.name == "response_cardinality"
    )
    assert cardinality.passed is expected_pass
    assert cardinality.details["response_record_count"] == observed_count  # type: ignore[index]
    if observed_count == 101:
        assert cardinality.details["response_overflow"] is True  # type: ignore[index]
        semantic = next(
            check for check in checks if check.name == "semantic_assertions"
        )
        assert (
            "response_projection_overflow"
            in semantic.details[  # type: ignore[index]
                "failure_kinds"
            ]
        )


def test_cap_plus_one_and_node_overflow_fail_before_cardinality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(check_endpoint, "MAX_RESPONSE_RECORDS", 2)
    service = Service(
        response={
            "predictions": [
                {"label": "ALLOW", "risk_score": 0.1},
                {"label": "ALLOW", "risk_score": 0.2},
                {"label": "ALLOW", "risk_score": 0.3},
            ]
        }
    )
    checks = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {"dataframe_records": [{"amount": 1.0, "country_code": "US"}]},
        make_args(),
        valid_contract()["output_contract"],  # type: ignore[arg-type,index]
        check_endpoint.FixtureBinding(
            "dataframe_records", 3, "one_output_per_input_row"
        ),
    )
    cardinality = next(
        check for check in checks if check.name == "response_cardinality"
    )
    assert cardinality.passed is False
    assert cardinality.details["response_record_count"] == 3  # type: ignore[index]
    assert cardinality.details["response_overflow"] is True  # type: ignore[index]

    monkeypatch.setattr(check_endpoint, "MAX_RESPONSE_RECORDS", 100)
    monkeypatch.setattr(check_endpoint, "MAX_RESPONSE_NODES", 5)
    service.response = {
        "predictions": [
            {"label": "ALLOW", "risk_score": 0.1},
            {"label": "ALLOW", "risk_score": 0.2},
        ]
    }
    checks = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {"dataframe_records": [{"amount": 1.0, "country_code": "US"}]},
        make_args(),
        valid_contract()["output_contract"],  # type: ignore[arg-type,index]
        check_endpoint.FixtureBinding(
            "dataframe_records", 2, "one_output_per_input_row"
        ),
    )
    cardinality = next(
        check for check in checks if check.name == "response_cardinality"
    )
    assert cardinality.passed is False
    assert cardinality.details["response_record_count"] == 2  # type: ignore[index]
    assert cardinality.details["response_overflow"] is True  # type: ignore[index]


@pytest.mark.parametrize(
    "timestamp,binary_value",
    [
        ("2026-08-29T14:30:00Z", "AQID"),
        ("2026-08-29T14:30:00.123Z", "AQI="),
        ("2026-08-29T14:30:00.123456-04:00", "AQIDBA=="),
    ],
)
def test_canonical_date_timestamp_binary_fixture_and_response_pass(
    timestamp: str, binary_value: str
) -> None:
    record = {
        "event_date": "2026-08-29",
        "event_timestamp": timestamp,
        "payload_bytes": binary_value,
    }
    binding = check_endpoint.bind_fixture_payload(
        {"dataframe_records": [record]}, temporal_binary_contract()
    )
    assert binding.input_row_count == 1
    service = Service(response={"predictions": [record]})
    checks = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {"dataframe_records": [record]},
        make_args(),
        temporal_binary_output_contract(),  # type: ignore[arg-type]
    )
    assert all(check.passed for check in checks)


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_date", "2026-02-30"),
        ("event_date", "2026-8-29"),
        ("event_timestamp", "2026-08-29T14:30:00"),
        ("event_timestamp", "2026-08-29t14:30:00Z"),
        ("event_timestamp", "2026-08-29T14:30:00.1Z"),
        ("event_timestamp", "2026-08-29T24:00:00Z"),
        ("payload_bytes", "AQI"),
        ("payload_bytes", "AQI=\n"),
        ("payload_bytes", "__=="),
        ("payload_bytes", ""),
    ],
)
def test_invalid_date_timestamp_binary_fixture_fails_before_client(
    field: str,
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {
        "event_date": "2026-08-29",
        "event_timestamp": "2026-08-29T14:30:00Z",
        "payload_bytes": "AQID",
    }
    record[field] = value
    with pytest.raises(check_endpoint.SampleInputError):
        check_endpoint.bind_fixture_payload(
            {"dataframe_records": [record]}, temporal_binary_contract()
        )

    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"dataframe_records": [record]}), encoding="utf-8")
    builds: list[object] = []
    monkeypatch.setattr(
        check_endpoint, "load_contract", lambda path: temporal_binary_contract()
    )
    monkeypatch.setattr(
        check_endpoint, "build_client", lambda args: builds.append(args)
    )
    assert (
        check_endpoint.main(
            [
                "risk-model-prod",
                "--contract",
                "contract.json",
                "--profile",
                "prod-serving",
                "--sample-json",
                str(fixture),
            ]
        )
        == 2
    )
    assert builds == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_date", "2026-02-30"),
        ("event_date", "2026-08-29T00:00:00Z"),
        ("event_timestamp", "2026-08-29 14:30:00Z"),
        ("event_timestamp", "2026-08-29T14:30:00.12Z"),
        ("payload_bytes", "AQI"),
        ("payload_bytes", "AQI= "),
    ],
)
def test_invalid_date_timestamp_binary_response_fails_semantics(
    field: str, value: str
) -> None:
    record = {
        "event_date": "2026-08-29",
        "event_timestamp": "2026-08-29T14:30:00Z",
        "payload_bytes": "AQID",
    }
    record[field] = value
    service = Service(response={"predictions": [record]})
    checks = check_endpoint.check_semantic_response(
        "risk-model-prod",
        Client(service),
        {"dataframe_records": [{"amount": 1.0, "country_code": "US"}]},
        make_args(),
        temporal_binary_output_contract(),  # type: ignore[arg-type]
    )
    assertions = next(check for check in checks if check.name == "semantic_assertions")
    assert assertions.passed is False
    assert (
        "response_schema_type_mismatch"
        in assertions.details[  # type: ignore[index]
            "failure_kinds"
        ]
    )


def test_binary_decoded_byte_cap_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logical_types, "MAX_BINARY_DECODED_BYTES", 2)
    assert logical_types.is_canonical_base64("AQI=") is True
    assert logical_types.is_canonical_base64("AQID") is False
