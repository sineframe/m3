"""An agent's tool call seen on the wire and in provider history is one call."""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime, timezone

from test_trace_dedup_equivalence import _corpus, _normalized

from m3.events import EventFactory, EventSequence
from m3.observability import CorrelationState, TraceView
from m3.trace.counts import _TOOL_CALL_COUNTS_VERSION, tool_call_count_after
from m3.types import (
    Event,
    EventDirection,
    EventKind,
    EventOrigin,
    EventSource,
    ExecutionOutcome,
    RequestLink,
    TraceResult,
)

CONNECTION = "pairing-connection"
REPORTED = EventSource(origin=EventOrigin.HARNESS_REPORTED, source="claude")


def _events(
    arguments: Sequence[dict[str, object]],
    *,
    reported_order: Sequence[int] | None = None,
    reported: Sequence[int] | None = None,
    errors: frozenset[int] = frozenset(),
    stamp_ids: bool = False,
) -> tuple[Event, ...]:
    """Wire calls in order `0..n`, then provider history for `reported_order`.

    Call `i` returns `out-i`; `stamp_ids` puts its provider id `toolu_i` on
    the wire the way Claude Code does.
    """
    factory = EventFactory(
        "pairing-execution",
        allocator=EventSequence(start=0),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="test.direct",
    )
    events = [
        factory.create(
            EventKind.EXECUTION_CREATED,
            connection_id=CONNECTION,
            payload={"trace_id": "trace-pairing"},
            lifecycle_phase="startup",
        )
    ]

    def result(index: int) -> dict[str, object]:
        return {
            "content": [{"type": "text", "text": f"out-{index}"}],
            "isError": index in errors,
        }

    for index, args in enumerate(arguments):
        jsonrpc_id = 10 + index
        params: dict[str, object] = {"name": "echo", "arguments": args}
        if stamp_ids:
            params["_meta"] = {"claudecode/toolUseId": f"toolu_{index}"}
        for kind, direction, payload in (
            (
                EventKind.TOOL_CALL_REQUESTED,
                EventDirection.CLIENT_TO_SERVER,
                {"method": "tools/call", "params": params},
            ),
            (
                EventKind.TOOL_RESULT_RECEIVED,
                EventDirection.SERVER_TO_CLIENT,
                {"method": "tools/call", "result": result(index)},
            ),
        ):
            events.append(
                factory.create(
                    kind,
                    connection_id=CONNECTION,
                    server_binding="fixture",
                    turn_id="turn-1",
                    correlation=RequestLink(
                        jsonrpc_id=jsonrpc_id,
                        direction=direction,
                        request_sequence=jsonrpc_id,
                    ),
                    payload=payload,
                )
            )

    order = reported if reported is not None else reported_order
    for index in order if order is not None else range(len(arguments)):
        events.append(
            factory.create(
                EventKind.TOOL_CALL_REQUESTED,
                connection_id=CONNECTION,
                provenance=REPORTED,
                server_binding="fixture",
                turn_id="turn-1",
                payload={
                    "call_id": f"toolu_{index}",
                    "tool": "echo",
                    "server": "fixture",
                    "arguments": arguments[index],
                },
            )
        )
        events.append(
            factory.create(
                EventKind.TOOL_RESULT_RECEIVED,
                connection_id=CONNECTION,
                provenance=REPORTED,
                turn_id="turn-1",
                payload={"call_id": f"toolu_{index}", "result": result(index)},
            )
        )
    events.append(
        factory.create(
            EventKind.EXECUTION_FINISHED,
            connection_id=CONNECTION,
            payload={
                "outcome": ExecutionOutcome.COMPLETED.value,
                "completeness": "complete",
                "limitations": [],
            },
        )
    )
    return tuple(events)


def _view(events: tuple[Event, ...]) -> TraceView:
    return TraceResult(
        trace_id="trace-pairing",
        execution_id="pairing-execution",
        completeness="complete",
        highest_sequence=events[-1].sequence,
        events=events,
        limitations=(),
    ).view()


def _assert_paired(events: tuple[Event, ...], calls: int) -> TraceView:
    view = _view(events)
    assert view.summary.tool_call_count == calls
    assert all(
        call.correlation is CorrelationState.CORRELATED for call in view.tool_calls
    )
    # The stored snapshot count joins exactly the calls the trace view joins.
    assert tool_call_count_after(0, events, events) == calls
    return view


def _results_by_provider_id(view: TraceView) -> dict[str, str]:
    return {
        call.provider_call_id.value: call.result.value.content[0].text
        for call in view.tool_calls
    }


def test_repeated_tool_calls_in_one_turn_are_counted_once_each() -> None:
    events = _events([{"text": "a"}, {"text": "b"}], errors=frozenset({1}))

    view = _assert_paired(events, 2)

    assert view.summary.failed_tool_call_count == 1


def test_reordered_provider_history_pairs_by_arguments() -> None:
    events = _events([{"text": "a"}, {"text": "b"}], reported_order=[1, 0])

    view = _assert_paired(events, 2)

    assert _results_by_provider_id(view) == {"toolu_0": "out-0", "toolu_1": "out-1"}


def test_identical_repeated_calls_pair_in_order() -> None:
    _assert_paired(_events([{"text": "a"}, {"text": "a"}]), 2)


def test_wire_stamped_provider_id_pairs_exactly() -> None:
    # Same arguments and reversed history: only the stamped id can tell the
    # calls apart.
    events = _events(
        [{"text": "a"}, {"text": "a"}], reported_order=[1, 0], stamp_ids=True
    )

    view = _assert_paired(events, 2)

    assert _results_by_provider_id(view) == {"toolu_0": "out-0", "toolu_1": "out-1"}


def test_ambiguous_calls_stay_separate() -> None:
    # One report for two identical wire calls: either could be the one.
    events = _events([{"text": "a"}, {"text": "a"}], reported=[0])

    view = _view(events)

    assert view.summary.tool_call_count == 3
    assert tool_call_count_after(0, events, events) == 3
    assert not any(
        call.correlation is CorrelationState.CORRELATED for call in view.tool_calls
    )


def test_partial_history_pairs_the_reported_call_by_arguments() -> None:
    # The provider history only records the second of two different calls.
    events = _events([{"text": "a"}, {"text": "b"}], reported=[1])

    view = _view(events)

    assert view.summary.tool_call_count == 2
    assert tool_call_count_after(0, events, events) == 2
    assert [call.correlation for call in view.tool_calls] == [
        CorrelationState.WIRE_ONLY,
        CorrelationState.CORRELATED,
    ]


# (total, successful) counts the current rules give, pinned to the counts
# version stores stamp on cached counts. Version 1 counted the paired cases
# below twice (for example 4 calls for 2 wire calls plus their provider history).
_PINNED_COUNTS = (
    2,
    {
        "repeated_with_error": (2, 1),
        "reordered_history": (2, 2),
        "identical_repeated": (2, 2),
        "wire_stamped_ids": (2, 2),
        "ambiguous_report": (3, 3),
        "partial_history": (2, 2),
    },
    "9f856f62737e0d7243f1eb4ee022971ced556efcfb17970f0add7b9b6fe9fe45",
)


def test_count_changes_require_a_counts_version_bump() -> None:
    """Stores trust cached counts stamped with the current version.

    If this fails, counting changed: bump ``_TOOL_CALL_COUNTS_VERSION`` so
    stored counts are recomputed, then pin the new counts here.
    """

    def counts(view: TraceView) -> tuple[int, int]:
        return view.summary.tool_call_count, view.summary.successful_tool_call_count

    a, b = {"text": "a"}, {"text": "b"}
    paired = {
        "repeated_with_error": counts(_view(_events([a, b], errors=frozenset({1})))),
        "reordered_history": counts(_view(_events([a, b], reported_order=[1, 0]))),
        "identical_repeated": counts(_view(_events([a, a]))),
        "wire_stamped_ids": counts(
            _view(_events([a, a], reported_order=[1, 0], stamp_ids=True))
        ),
        "ambiguous_report": counts(_view(_events([a, a], reported=[0]))),
        "partial_history": counts(_view(_events([a, b], reported=[1]))),
    }
    corpus = {
        name: list(counts(_normalized(build()).view()))
        for name, build in sorted(_corpus().items())
    }
    digest = hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest()

    assert (_TOOL_CALL_COUNTS_VERSION, paired, digest) == _PINNED_COUNTS
