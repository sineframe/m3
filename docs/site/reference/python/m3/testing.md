---
title: "Testing utilities"
description: "MockMCPServer and ExpectedCall define a controlled protocol interaction. Unexpected calls raise MockExpectationError; invalid mock protocol behavior raises MockProtocolError."
---

# Testing utilities

`MockMCPServer` and `ExpectedCall` define a controlled protocol interaction.
Unexpected calls raise `MockExpectationError`; invalid mock protocol behavior
raises `MockProtocolError`.

`Recording` and `RecordedInteraction` capture reusable interactions.
`ReplayServer` replays them and raises `ReplayMismatch` when a request differs.
`RecordedArtifact` and `ArtifactIntegrityError` protect recorded artifact
identity.

`FaultInjector`, `Gate`, and `VirtualClock` control failure and timing behavior
in deterministic tests. `RedactionBinding` defines redaction applied to a
recording.

`snapshot(value, options=SnapshotOptions(...))` creates stable snapshot data
for supported public values. Review snapshots as test artifacts; do not use
them to avoid meaningful field assertions.
