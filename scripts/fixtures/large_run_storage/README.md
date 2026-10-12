# Concurrent results-store repro

From the repository root, use a Python environment containing the workspace
SDK, CLI, pytest and pytest-xdist dependencies:

```sh
.venv/bin/python scripts/repro_large_run_storage.py --output /tmp/m3-storage-repro
```

The output directory must not already exist. The driver starts six actual
`m3 test` processes with four pytest workers each. Every test uses a real stdio
echo MCP server for 16 tool calls and a deterministic fake agent adapter for
eight turns. It persists normal run manifests, executions, sessions, turns
and events without calling model providers.

Five cases cover a cold shared store, warm shared store, separate stores,
interruption of an unfinished agent trial, and recovery against the same shared
store.
The driver reads events and turns while writers run, reloads execution entries
and feedback, then checks SQLite integrity, foreign keys, event sequences,
record counts and completed-run status. The terminated run is recorded
separately; its incomplete records are not treated as completed trials.
Each invocation has a private fixture descriptor beside its unique pytest
`--basetemp` directory. The pytest controller and four workers establish live
socket leases before writes; the driver validates their PID/process-group
identity. This is fixture coordination, not an m3 option or configuration
setting. The victim is always invocation 0: trial 0 holds its open agent session
after two persisted turns, while its other workers hold before writes. The
other five invocations continue normally. The driver checks the victim's
persisted session, run, two turn IDs and zero completed trial results before
termination; it requires five complete survivor runs with exact per-run counts.
The checkpoint holds an unfinished trial, not a SQLite transaction or lock.

Termination requires POSIX. Normal shutdown first uses the CLI's SIGTERM
cleanup. If the CLI cannot run its handler, escalation targets only process
groups anchored by retained live leases and fresh group-identity checks. The
driver resumes a suspended CLI after signalling its pytest group so the CLI
can reap its own child, then kills/reaps remaining CLI children. Cleanup verifies
peer EOF and group disappearance; a timeout remains a failure. The driver does
not claim to reap grandchildren. A real four-worker fault test suspends the CLI
at the unfinished-session checkpoint and verifies this escalation.

Each case writes process logs and a JSON report beneath the output directory.
The report includes new record counts so a warm or recovery case cannot pass
solely because earlier records exist. Fixture-generated reports under `.m3`
are ignored by Git.

This is a storage contention and recovery repro. It does not reproduce native
harness startup, assert provider behavior or implement the statistical release
performance gate. A successful run means contention was not reproduced under
this workload; it does not establish that every host or filesystem is safe.
