"""Run one eval (defined by its quirk.yaml) for one agent selection."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from _lib.servers import CONTROL, expand_nonce, load_quirk, quirk_server, run_kwargs
from _lib.trial import record_trial


def run_eval(agent: Any, quirk_dir: Path) -> str:
    spec = load_quirk(quirk_dir)
    nonce = "N" + secrets.token_hex(10) if spec.get("synthetic_nonce") else None
    expectation = expand_nonce(spec["expectation"], nonce)
    server_args = ("--nonce", nonce) if nonce is not None else ()
    markers = tuple(expectation.get("markers", ()))
    # Timeout comes from `m3 test --execution-timeout`.
    result = agent.run(
        spec["prompt"],
        servers=(CONTROL, quirk_server(quirk_dir, spec["server"], args=server_args)),
        **run_kwargs(agent),
    )
    return record_trial(
        agent,
        result,
        quirk=spec["id"],
        backlog_id=spec["backlog_id"],
        tool=expectation["tool"],
        arguments=expectation["arguments"],
        marker=expectation.get("marker"),
        markers=markers,
        expected_status=expectation.get("status", "success"),
        extra_tools=tuple(
            (item["tool"], item["arguments"]) for item in spec.get("extra_tools", ())
        ),
    )
