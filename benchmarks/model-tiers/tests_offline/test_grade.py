import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

pytestmark = pytest.mark.m3(suite_name="model-tiers-offline")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import conftest as root_conftest
from _lib import grade, telemetry
from _lib.grade import Check, classify, parse_status_line


@pytest.mark.parametrize(
    "text,expected",
    [
        ("blah\nSTATUS: DONE 4 issues", ("DONE", "4 issues")),
        ("**STATUS: DONE** x", ("DONE", "x")),
        ("`STATUS: FAILED not found`", ("FAILED", "not found")),
        ("STATUS: DONE a\nSTATUS: FAILED b", ("FAILED", "b")),
        ("no status", (None, "")),
        (None, (None, "")),
    ],
)
def test_status(text, expected):
    assert parse_status_line(text) == expected


def C(*, success=False, sub=None, ops=("update_issue",)):
    return Check(success, sub or {}, list(ops))


def run(outcome="completed", calls=(("search", {}),), **kw):
    kw.setdefault("check", C())
    return classify(outcome, tracker_calls=list(calls), **kw)


def test_order():
    assert run("failed") == "harness_error"
    assert run(error="x", calls=()) == "harness_error"
    assert run("timed_out") == "timeout"
    assert run(native_total=1, check=C(success=True)) == "escaped"
    assert run(check=C(success=True)) == "success"
    assert run(calls=[("a", {})] * 3) == "looped"
    assert run(calls=[("a", {"i": i}) for i in range(121)]) == "looped"
    assert run(calls=[("a", {"i": i}) for i in range(60)]) != "looped"
    assert run(status="DONE") == "hallucinated"
    assert run() == "wrong_tool"
    assert run(outputs=[("e", True)]) == "bad_args"
    assert run(mutations=[{"op": "update_issue"}]) == "wrong_target"
    assert (
        run(
            mutations=[{"op": "update_issue"}],
            check=C(sub={"ok": True, "no_side_effects": False}),
        )
        == "side_effects"
    )
    assert run(check=C(ops=[]), status="FAILED") == "wrong_target"
    # gave_up: FAILED, no target op hit, with or without attempts / error responses
    assert run(status="FAILED") == "gave_up"
    assert run(status="FAILED", outputs=[("e", True)]) == "gave_up"
    assert run(calls=[], status="FAILED") == "gave_up"
    # FAILED but the op did succeed -> wrong_target, not gave_up
    assert run(status="FAILED", mutations=[{"op": "update_issue"}]) == "wrong_target"
    # DONE stays hallucinated even after errors
    assert run(status="DONE", outputs=[("e", True)]) == "hallucinated"


def test_op_of_table():
    from _server.surfaces import VARIANTS, build_surface

    naive = build_surface("naive")
    assert naive.op_of("issues_get") == naive.op_of("get_issue_v2_beta") == "get_issue"
    assert naive.op_of("issues_patch") == naive.op_of("issues_put") == "update_issue"
    assert naive.op_of("issues_bulk_patch") == "bulk_update_issues"
    assert naive.op_of("issues_list") == "search_issues"
    assert naive.op_of("issue_relations_create") == "link_issues"
    assert naive.op_of("add_dependency") is None
    assert (
        naive.op_of("webhooks_list") is None
        and naive.op_of("teams_members_list") == "find_users"
    )
    assert naive.op_of("nope") is None
    good = build_surface("good")
    assert [good.op_of(t["name"]) for t in good.tools] == [
        t["name"] for t in good.tools
    ]
    for v in (
        VARIANTS
    ):  # every advertised tool is in the table; non-None ops are real Store ops
        sf = build_surface(v)
        for t in sf.tools:
            assert t["name"] in sf._ops
        assert {sf.op_of(t["name"]) for t in sf.tools} - {None} <= {
            "search_issues",
            "get_issue",
            "update_issue",
            "bulk_update_issues",
            "create_issue",
            "add_comment",
            "link_issues",
            "find_users",
            "list_labels",
            "list_cycles",
            "list_projects",
        }


def test_hit_uses_op_of_not_names():
    from _server.surfaces import build_surface

    op_of = build_surface("naive").op_of
    get = Check(False, {}, ["get_issue"])
    kw = dict(check=get, op_of=op_of)
    # naive REST name counts as a hit via the table
    assert (
        classify(
            "completed",
            tracker_calls=[("issues_get", {})],
            outputs=[("{}", False)],
            status="DONE",
            **kw,
        )
        == "wrong_target"
    )
    # an errored call is not a hit
    assert (
        classify(
            "completed",
            tracker_calls=[("issues_get", {})],
            outputs=[("404 Not Found", True)],
            status="DONE",
            **kw,
        )
        == "hallucinated"
    )
    # a name that merely contains the op (or a distractor) is not a hit
    assert (
        classify(
            "completed",
            tracker_calls=[("get_issue_history_x", {})],
            outputs=[("{}", False)],
            status="DONE",
            **kw,
        )
        == "hallucinated"
    )
    assert (
        classify(
            "completed",
            tracker_calls=[("issues_history_list", {})],
            outputs=[("{}", False)],
            status="FAILED",
            **kw,
        )
        == "gave_up"
    )
    # deprecated add_dependency never counts for link_issues
    link = Check(False, {}, ["link_issues"])
    assert (
        classify(
            "completed",
            tracker_calls=[("add_dependency", {})],
            outputs=[("400 Bad Request", True)],
            check=link,
            op_of=op_of,
        )
        == "bad_args"
    )
    st = grade.target_op_stats(
        op_of,
        ["get_issue"],
        [("issues_get", {}), ("get_issue_v2_beta", {}), ("x", {})],
        [("e", True), ("{}", False), ("{}", False)],
    )
    assert st == {"get_issue": {"attempted": 2, "errored": 1, "succeeded": 1}}


def test_record_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(grade, "INBOX", tmp_path / "r.jsonl")
    result = NS()  # everything missing -> nulls, no raise
    agent = NS(harness="h", model="m", trial=1)
    assert (
        grade.record(
            agent,
            result,
            task="T01",
            band="b",
            variant="good",
            nonce="N",
            state="other",
        )
        == "other"
    )
    rec = json.loads((tmp_path / "r.jsonl").read_text())
    for k in (
        "task band variant harness model observed_model version digest trial nonce "
        "execution_id outcome error state subgoals status_line duration_ms turn_count "
        "tool_calls usage mutations calls final_text recorded_at"
    ).split():
        assert k in rec, k
    assert set(rec["usage"]) >= {
        "input_tokens",
        "per_turn_input_tokens",
        "reported_cost_usd",
    }
    assert set(rec["tool_calls"]) == {
        "tracker_total",
        "tracker_errors",
        "native_total",
        "max_result_chars",
    }


def _entry(seq, corr, server, tool, args=None, text=None):
    obs = lambda v: NS(value=v)
    res = NS(value=NS(content=[NS(text=text)])) if text is not None else NS(value=None)
    reported = NS(value=NS(server=obs(server), tool=obs(tool)))
    return NS(
        sequence_start=seq,
        sequence_end=seq,
        correlation=NS(value=corr),
        server=obs(server),
        tool=obs(tool),
        arguments=obs(args or {}),
        tool_status=NS(value="success"),
        result=res,
        reported=reported,
    )


def test_telemetry_order_and_native():
    entries = [
        _entry(5, "wire_only", "tracker", "b", {"x": 1}, "yy"),
        _entry(1, "correlated", "tracker", "a", {}, "xxxx"),
        _entry(3, "reported_only", None, "Bash"),
    ]
    result = NS(trace_view=NS(tool_calls=entries, timeline=[], messages=[]))
    assert [c[0] for c in telemetry.wire_tracker_calls(result)] == ["a", "b"]
    s = telemetry.tool_call_stats(result)
    assert (s["tracker_total"], s["native_total"], s["max_result_chars"]) == (2, 1, 4)


def test_variants(monkeypatch):
    assert root_conftest.parse_variants(None) == ["good"]
    assert root_conftest.parse_variants("good, naive") == ["good", "naive"]
    with pytest.raises(ValueError):
        root_conftest.parse_variants("bogus")


def test_status_marker_without_newline() -> None:
    # Codex joins message chunks with no separator before the STATUS line.
    text = "PAY-142 is todo with audit code AC-1.STATUS: DONE todo AC-1"
    assert grade.parse_status_line(text) == ("DONE", "todo AC-1")
