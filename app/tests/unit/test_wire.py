from __future__ import annotations

import json

from _local_client import TestClient
from fastapi import Response

from m3_app.api.app import create_app
from m3_app.api.wire import (
    internalize_request,
    neutralize_openapi,
    neutralize_response,
)
from m3_app.settings import Settings


def test_wire_mapping_is_field_aware_and_preserves_user_values() -> None:
    internal = {
        "trace": {"schema_id": "m3.trace_view"},
        "events": [
            {
                "event_id": "ev-1",
                "sequence": 0,
                "kind": "diagnostic",
                "schema": "m3.event",
            }
        ],
        "manifest": {"schema_version": "m3.harness.v1", "args": ["m3.event"]},
        "evaluations": [{"name": "m3.output.has_text.v1"}],
        "provenance": {"source": "m3.direct.stream"},
        "metadata": {
            "m3.matrix.case_id": "m3.output.has_text.v1",
            "m3.run_id": "run-m3-user-value",
            "arbitrary": "m3.output.has_text.v1",
        },
        "payload": {"schema": "m3.event", "text": "m3.output.has_text.v1"},
        "evidence": {"agent": "m3.output.has_text.v1"},
    }
    wire = neutralize_response("/api/v2/executions/e1/report", internal)
    assert wire["trace"]["schema_id"] == "trace_view"
    assert wire["events"][0]["schema"] == "event"
    assert wire["manifest"]["schema_version"] == "harness.v1"
    assert wire["evaluations"][0]["name"] == "output.has_text.v1"
    assert wire["provenance"]["source"] == "direct.stream"
    assert wire["metadata"] == {
        "matrix.case_id": "m3.output.has_text.v1",
        "run_id": "run-m3-user-value",
        "arbitrary": "m3.output.has_text.v1",
    }
    assert wire["payload"] == internal["payload"]
    assert wire["evidence"] == internal["evidence"]

    restored = internalize_request("/api/v2/executions/e1/report", wire)
    assert restored == internal


def test_colliding_identifiers_and_nested_payloads_are_not_reinterpreted() -> None:
    internal = {
        "filters": {"evaluator": "event"},
        "evaluations": [{"name": "event"}],
        "metadata": {
            "trace_view": "m3.event",
            "custom.m3.matrix.case_id": "m3.event",
            "foreign-looking": "m3.harness.v1",
        },
        "tool_arguments": {
            "metadata": {"trace_view": "m3.event"},
            "evaluator": "event",
            "schema": "m3.event",
            "source": "m3.harness",
        },
        "tool_result": {
            "results": [{"metadata": {"m3.run_id": "user-value"}}],
            "value": {"evaluator": "event", "schema": "m3.event"},
        },
        "mcp_json": {
            "metadata": {"evaluator": "event", "schema": "m3.event"},
            "source": "m3.harness",
        },
        "session_config": {"policy": {"metadata": {"trace_view": "m3.event"}}},
    }
    wire = neutralize_response("/api/v2/executions/e1/report", internal)
    assert wire["filters"] == internal["filters"]
    assert wire["evaluations"] == internal["evaluations"]
    assert wire["metadata"] == internal["metadata"]
    assert wire["tool_arguments"] == internal["tool_arguments"]
    assert wire["tool_result"] == internal["tool_result"]
    assert wire["mcp_json"] == internal["mcp_json"]
    assert wire["session_config"] == internal["session_config"]
    assert internalize_request("/api/v2/executions/e1/report", wire) == internal


def test_harness_manifest_schema_version_maps_without_rewriting_manifest_data() -> None:
    wire = {
        "manifest": {
            "schema_version": "harness.v1",
            "args": ["m3.event"],
            "env": {"VALUE": "m3.event"},
        }
    }
    internal = internalize_request("/api/v2/harnesses", wire)
    assert internal["manifest"]["schema_version"] == "m3.harness.v1"
    assert internal["manifest"]["args"] == ["m3.event"]
    assert internal["manifest"]["env"] == {"VALUE": "m3.event"}
    assert neutralize_response("/api/v2/harnesses", internal) == wire


def test_aggregate_evaluator_and_metadata_filters_cross_boundary() -> None:
    wire = {
        "filters": {
            "evaluator": ["output.has_text.v1"],
            "metadata.matrix.case_id": ["case-1"],
        },
        "group_by": ["evaluator", "metadata.matrix.case_id"],
    }
    internal = internalize_request("/api/v2/evaluations/aggregate", wire)
    assert internal == {
        "filters": {
            "evaluator": ["m3.output.has_text.v1"],
            "metadata.m3.matrix.case_id": ["case-1"],
        },
        "group_by": ["evaluator", "metadata.m3.matrix.case_id"],
    }
    assert neutralize_response("/api/v2/evaluations/aggregate", internal) == wire


def test_feedback_evaluator_names_map_without_changing_evidence() -> None:
    internal = {
        "feedback": {
            "evaluation_stats": {"m3.output.has_text.v1": {"evaluation_count": 1}},
            "failures": [
                {
                    "evaluator": "m3.output.has_text.v1",
                    "details": {"evaluator": "m3.output.has_text.v1"},
                }
            ],
            "comparison": {
                "evaluation_changes": [{"evaluator": "m3.output.has_text.v1"}],
                "failures": [{"evaluator": "m3.output.has_text.v1"}],
            },
        }
    }
    wire = neutralize_response("/api/v2/feedback/run-1", internal)
    feedback = wire["feedback"]
    assert list(feedback["evaluation_stats"]) == ["output.has_text.v1"]
    assert feedback["failures"][0]["evaluator"] == "output.has_text.v1"
    assert feedback["failures"][0]["details"] == {"evaluator": "m3.output.has_text.v1"}
    assert feedback["comparison"]["evaluation_changes"][0]["evaluator"] == (
        "output.has_text.v1"
    )
    assert feedback["comparison"]["failures"][0]["evaluator"] == ("output.has_text.v1")
    assert internalize_request("/api/v2/feedback/run-1", wire) == internal


def test_colliding_metadata_keys_preserve_both_values() -> None:
    metadata = {"m3.run_id": "internal-looking", "run_id": "user-owned"}
    wire = {"spec": {"metadata": metadata}}
    internal = internalize_request("/api/v2/executions", wire)
    assert internal["spec"]["metadata"] == metadata
    assert neutralize_response("/api/v2/executions", internal) == wire


def test_openapi_has_neutral_product_contract() -> None:
    schema = neutralize_openapi(
        {
            "info": {"title": "MCP Testing Platform"},
            "components": {
                "schemas": {
                    "Event": {
                        "$id": "https://m3.local/schemas/m3.event.v0.2.schema.json",
                        "properties": {
                            "schema": {"const": "m3.event", "default": "m3.event"},
                            "evaluator": {"enum": ["m3.output.has_text.v1"]},
                            "name": {"default": "m3.output.has_text.v1"},
                        },
                        "examples": {"sample": "m3.output.has_text.v1"},
                    }
                }
            },
        }
    )
    text = json.dumps(schema)
    assert schema["info"]["title"] == "Test Results API"
    assert "m3.event.v0.2.schema.json" not in text
    assert (
        schema["components"]["schemas"]["Event"]["properties"]["schema"]["const"]
        == "event"
    )
    assert (
        schema["components"]["schemas"]["Event"]["examples"]["sample"]
        == "output.has_text.v1"
    )
    assert (
        schema["components"]["schemas"]["Event"]["properties"]["name"]["default"]
        == "m3.output.has_text.v1"
    )


def test_only_owned_identifiers_are_normalized() -> None:
    value = {
        "filters": {
            "evaluator": [
                "m3.output.has_text.v1",
                "m3.matcher.equals.v1",
                "foreign.custom-evaluator",
            ]
        },
        "metadata": {
            "m3.matrix.case_id": "foreign-user-value",
            "m3.matrix.trial_count": 3,
            "foreign.custom.matrix.case_id": "foreign-user-value",
        },
        "payload": {"text": "foreign-user-value"},
    }
    mapped = neutralize_response("/api/v2/evaluations/aggregate", value)
    assert mapped["filters"]["evaluator"] == [
        "output.has_text.v1",
        "matcher.equals.v1",
        "foreign.custom-evaluator",
    ]
    assert mapped["metadata"] == {
        "matrix.case_id": "foreign-user-value",
        "matrix.trial_count": 3,
        "foreign.custom.matrix.case_id": "foreign-user-value",
    }
    assert mapped["payload"] == value["payload"]


def test_wire_boundary_preserves_non_json_response_body(tmp_path) -> None:
    application = create_app(
        Settings(database_path=str(tmp_path / "wire-fallback.sqlite"))
    )

    @application.get("/api/v2/raw")
    def raw_response() -> Response:
        return Response(content=b"not-json", media_type="application/json")

    with TestClient(application) as client:
        response = client.get("/api/v2/raw")
    assert response.status_code == 200
    assert response.content == b"not-json"
