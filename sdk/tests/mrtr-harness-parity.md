# Pi-to-Codex MRTR parity inventory

This is the exhaustive crosswalk for existing Pi MRTR unit tests, adapter
tests, real-binary tests, managed-input tests, and runnable examples. It names
each Pi-specific test so a missing Codex case is visible. Shared API tests are
listed separately: the direct MCP clients do not depend on a harness and must
not be cloned just to create a Codex-named test.

## Status and reading the crosswalk

The original four Codex CLI 0.156.1 native, managed, and example suites passed
all 40 tests on 2026-09-25 with a local deterministic provider. The current
required jobs also include the two tests in the approval-spoof suite, added
after that 40-test run; this historical result does not claim a result for
those additions. The rows below identify verified evidence, Pi-only
mechanisms, and Codex behaviors outside the tested scope. Missing or
mismatched Codex fails the suites instead of skipping them. The canonical
limits and ownership model are in the
[Codex App Server section of the API reference](../docs/elicitation-api.md#codex-app-server-support-and-limitations).

Run the local Codex gates with no paid provider call:

```bash
npm install --global @openai/codex@0.156.1
test "$(codex --version)" = "codex-cli 0.156.1"
uv run --project sdk --all-extras pytest -q \
  sdk/tests/e2e/test_real_codex_native_mrtr.py \
  sdk/tests/e2e/test_real_codex_managed_mrtr.py \
  sdk/examples/tests/test_modern_mrtr_codex.py \
  sdk/examples/tests/test_modern_mrtr_codex_action_scopes.py \
  sdk/examples/tests/test_modern_mrtr_codex_approval_spoof.py
```

The binary must report `codex-cli 0.156.1` by default. The native suite
characterizes Codex; the managed suite exercises M3's real action and storage
path; the Codex example suite exercises the same plans shown in the public
guide; the action-scope suite covers response actions and repeated planned
session turns; the approval-spoof suite checks that elicitation metadata cannot
impersonate native tool approval. All five use the local deterministic
provider. The installed Codex MRTR fixtures currently configure MCP over
stdio. The e2e
[README](e2e/README.md) describes version overrides and setup.

## Verified protocol differences that drive the gaps

These are observed against unmodified Codex 0.156.1 by
[`test_real_codex_native_mrtr.py`](e2e/test_real_codex_native_mrtr.py):

| Codex behavior | Consequence for a Pi-to-Codex port |
| --- | --- |
| Native requests omit the MCP request key, `requestState`, and logical round ID. | Correlate only by original MCP capture plus server identity and native fields Codex exposes. Never invent a key, infer from timing, or zip by arrival order. |
| Simultaneous prompts are all emitted before a response, and observed native order is reversed from the MCP map order. | Treat one round as an unordered prompt multiset and wait for the whole prompt group before sending any answers. |
| Identical exposed prompts may map to multiple keyed requests. | If all candidate keys have equal responses, a multiset answer is safe; unequal responses are ambiguous and must fail before any response. An exposed metadata field can distinguish requests only when Codex actually preserves it. |
| Form schemas are JSON values; native `_meta:null` represents absent server metadata. | Canonicalize object member ordering but keep types and array order significant. Match actual schema JSON, not semantic JSON Schema equivalence. Treat only this observed null/absent metadata representation as equivalent. |
| Accepted URL response is retried with `content:{}`. Decline/cancel retry with action and optional `_meta`, with content omitted. | **Verified by the 40-test gate:** M3 planned accept/decline/cancel responses for form and URL preserve the native wire shape, including omitted `content` and retained response metadata for non-accept actions. The URL is never visited. |
| An empty request map is automatically retried using `requestState`, with no native prompt and no `inputResponses`. | Do not consume a plan step or fabricate a prompt for state-only `input_required`; observe and verify Codex's exact retry. |
| Codex allows nine MRTR prompts/rounds; its tenth is rejected before a tenth native prompt is surfaced. | The effective Codex limit must not exceed nine, regardless of the common API's Pi default. Pi's ten-round success is not portable. |
| Tool approval is a separate native request marked `_meta.codex_approval_kind=mcp_tool_call`. | Leave tool approval and policy decisions with Codex; never answer approval from an elicitation plan. |
| Codex App Server 0.156.1 exposes no native request-to-item ID on MCP tool-approval frames. A second same-server approval while an earlier approved item is active is ambiguous with forged server elicitation metadata. | M3 fails closed and rejects the overlapping same-server approval. Concurrent same-server native approvals are unsupported until Codex exposes a reliable association field. |
| Cancelling with a native elicitation outstanding interrupts the turn; Codex sends no MCP retry/cancel notification and no resolution for that request. | M3 must let Codex own cancellation and terminalize the pending action without manufacturing a response, retry, or cancel notification. |

## Shared public API tests (no per-harness clone)

[`test_direct_client_mrtr.py`](integration/test_direct_client_mrtr.py) is
harness-independent. It covers sync and async direct form retries and
protocol override; server qualifiers; URL elicitation for tools, prompts, and
resources without visiting the URL; current-round response scoping; argument
preservation; fresh JSON-RPC IDs; malformed continuations; form schema
validation; positive round limits, ten-round support, and eleventh-round
rejection; action binding; manual `allow_input_required`; multi-request
`round_of`; decline and cancel; sampling and roots callbacks; mixed rounds;
and client usability after expectation failure. These tests remain the source
of truth for the common direct API.

Shared unit coverage also remains shared rather than copied: helper and plan
validation in `test_elicitation_plans.py`; public action binding and session
semantics in `test_agent_session.py`; the full MCP trace correlation contract
in `test_mrtr_trace_projection.py`; and generic wire capture, redaction, and
pass-through in `test_capture_proxy.py`. Codex action and association tests
cover its added native boundary without weakening those common contracts.

The direct example
[`test_modern_mrtr_direct.py`](../examples/tests/test_modern_mrtr_direct.py)
is intentionally harness-agnostic. The following `pi`-named examples are
harness-specific and are mapped below.

## Pi bridge unit cases

Source: [`test_pi_mrtr_bridge.py`](unit/test_pi_mrtr_bridge.py). Pi implements
MRTR through a generated bridge tool and a Pi extension; Codex must not copy
that mechanism. Codex's counterparts use Codex's native App Server request
and M3's passive MCP observer. Each row records a tested Codex counterpart or
the reason a Pi mechanism does not apply to Codex.

| Pi case(s) | Codex counterpart or gap | Required disposition |
| --- | --- | --- |
| `test_channel_round_trips_generation_and_plan`; `test_bridge_rejects_stale_generation`; `test_channel_rejects_unknown_status_and_invalid_permissions`; `test_failed_generation_is_sticky_and_finalize_cannot_overwrite_it` | Codex has no bridge channel. It uses action-local App Server IDs and a passive capture subscription. | **Implementation distinction, covered by Codex lifecycle tests:** `test_ordinary_codex_turn_does_not_start_mrtr_observation`, `test_confirmed_turn_interrupt_keeps_codex_process_reusable`, terminal-barrier action tests, and managed process-loss coverage. No Pi-style channel is added. |
| `test_bridge_retries_form_with_current_state_and_unchanged_args`; `test_bridge_retry_uses_only_current_round_responses_and_exact_state`; `test_bridge_limit_allows_ten_rounds_and_final_retry`; `test_bridge_eleventh_round_fails_without_an_extra_retry` | Native retry shape and the nine/ten boundary are in `test_real_codex_native_mrtr.py`; M3 action, managed, and trace checks are in `test_codex_mrtr_action.py` and `test_real_codex_managed_mrtr.py`. | **Covered by the passed gate.** Codex uses only the current keyed responses, preserves operation arguments/state, and rejects the tenth round; its effective limit is at most nine. Pi's ten-round success is not portable. |
| `test_bridge_supports_multi_request_round`; `test_bridge_round_of_accepts_form_and_url_together`; `test_bridge_mixed_round_merges_form_and_sampling_responses`; `test_bridge_sampling_only_and_roots_only_rounds_are_retried` | Native all-prompts-first is in `test_real_codex_sends_each_native_request_for_a_multi_request_round`; batching is in `test_action_batches_all_native_prompts_before_sending_any_answer`; the installed `round_of` plus later URL path is in `test_codex_two_addresses_in_one_round_then_url` and the managed multi-round test. | **Form and URL batching are covered by the passed gate.** Codex App Server has no verified sampling/roots callback route; mixed or callback-only sampling/roots rounds are outside supported Codex harness scope. The action validates the full form prompt group before answering. |
| `test_managed_bridge_pauses_and_continues_with_parent_keyed_response`; `test_managed_round_indices_track_elicitation_rounds_per_operation`; `test_managed_round_limit_includes_rounds_before_continuation`; `test_managed_bridge_delivers_two_rounds_with_current_keyed_responses` | `test_installed_codex_real_managed_async_preserves_multi_round_trace` and `test_installed_codex_adapter_enforces_round_limit_and_native_cap`. | **Covered by the passed gate:** managed continuation persists keyed rounds, preserves one logical operation and its attempt history, and enforces the native nine-round ceiling. |
| `test_managed_bridge_preserves_url_request_identity`; `test_bridge_url_is_asserted_and_not_visited` | Native URL shape is in `test_real_codex_surfaces_url_and_empty_form_requests`; managed URL identity and retry are in `test_installed_codex_real_managed_async_preserves_url_round`; the composed examples also assert no navigation. | **Covered by the passed gate.** URL identity is retained, accepted URL content is normalized, and the URL is never visited. |
| `test_bridge_decline_is_forwarded_without_form_content` | Native form/URL decline and cancel are characterized by `test_real_codex_forwards_non_accept_form_and_url_responses`; M3 responses are in `test_codex_planned_non_accept_response_omits_wire_content_and_keeps_meta` and `test_modern_mrtr_codex_action_scopes.py`. | **Verified by the 40-test pinned gate:** planned form and URL decline/cancel omit `content` and preserve response metadata. |
| `test_bridge_url_mismatch_and_schema_mismatch_fail_before_retry`; `test_bridge_form_schema_mismatch_fails_before_retry` | `test_codex_mrtr_association.py` covers schema value differences, exposed metadata, URL identity, server identity, and identical prompts; `test_installed_codex_fails_safely_for_identical_unkeyed_prompts` exercises fail-before-answer on the installed binary. | **Covered by unit and installed tests.** Schema values are exact JSON apart from object-key ordering; unequal answers for indistinguishable prompts fail before any response. |
| `test_bridge_rejects_unexpected_input_without_plan`; `test_malformed_empty_input_required_fails_without_retry`; `test_non_mapping_arguments_are_rejected` | `test_native_elicitation_without_plan_fails_without_taking_over_codex`, `test_state_only_input_required_uses_codex_auto_retry_without_prompt`, malformed-prompt action cases, and native state-only characterization. | **Covered by action and native tests.** Unplanned prompts fail without answering; malformed observations fail closed; Codex's empty-map state-only retry remains harness-owned. |
| `test_state_only_round_does_not_consume_pi_elicitation_plan` | `test_state_only_input_required_uses_codex_auto_retry_without_prompt` starts with an unconsumed expected form and verifies that the state-only retry leaves the action healthy and sends no fabricated answer. | **Covered for state-only auto-retry and plan preservation.** The more specific sequence of a state-only retry followed by a later native prompt in the same operation is not a separate installed-binary case. |
| `test_managed_bridge_releases_claim_for_sequential_operation`; `test_managed_continue_failure_cleans_claim_and_is_terminal`; `test_bridge_cancellation_marks_generation_failed_and_cannot_be_reused`; `test_bridge_rejects_second_same_target_eliciting_invocation`; `test_concurrent_eliciting_calls_cancel_first_and_fail_generation` | Codex does not use Pi's extension generation claim. `test_concurrent_eliciting_operation_fails_before_late_native_answers` now covers action-unit handling when a second input-required operation arrives before the active one completes. Existing tests also cover an ordinary turn followed by a planned turn, successive planned turns, interruption, terminalization, and process loss: `test_ordinary_codex_turn_does_not_start_mrtr_observation`, `test_codex_session_attaches_plan_only_to_second_turn`, `test_same_codex_session_uses_fresh_scope_for_two_planned_turns`, `test_confirmed_turn_interrupt_keeps_codex_process_reusable`, and `test_installed_codex_process_loss_fails_pending_round_without_replay`. `test_optional_plan_waits_for_each_native_tool_terminal_observation` checks terminal-evidence accounting with a fake capture source. | **Unit coverage is verified.** The action unit test rejects a simulated overlapping eliciting operation before answering late prompts. There is still no installed real-Codex test for overlapping operations or direct test of concurrent managed handles, so Pi's installed concurrency behavior is not claimed. |
| `test_bridge_requires_plan_completion_at_action_finalize` | `test_terminal_barrier_drains_published_keyed_retry_before_plan_completion` and installed managed multi-round/trace tests. | **Covered by the passed gate.** The manager barrier drains accepted observations; the adapter then waits within a bound for the exact retry/plan terminal evidence before finalizing. |
| `test_tool_catalog_uses_pi_json_schema_aliases` | Codex presents server tools under its native `mcp__server::tool` form; see managed fixture assertion in `test_real_codex_managed_mrtr.py`. | **Implementation distinction.** Do not expose Pi bridge catalog aliases in Codex; verify Codex native tool catalog and observed MCP server/tool identity instead. |
| `test_jsonl_dispatch_can_overlap_non_eliciting_calls`; `test_jsonl_many_completed_requests_are_reaped` | No Codex extension JSONL dispatcher. Codex owns its App Server and MCP dispatch. | **Implementation distinction.** Test observer concurrency and bounded cleanup only where M3 owns the transport; do not duplicate Pi's generated tool dispatcher. |

## Pi control-channel unit cases

Source: [`test_pi_control.py`](unit/test_pi_control.py). This authenticated
parent/extension channel is Pi-specific because the Pi extension is the MRTR
bridge. Codex has no equivalent M3-owned control extension; adding one would
cross the harness ownership boundary. Each row is intentionally marked as an
implementation distinction, with the relevant Codex native or M3 observer
coverage identified where one exists.

| Pi control case(s) | Codex relation |
| --- | --- |
| `test_envelope_validation_rejects_unknown_fields_and_stale_scope`; `test_envelope_validation_rejects_invalid_request_and_response_keys`; `test_pending_preserves_opaque_state_and_operation_identity_fields`; `test_pending_timestamp_and_operation_kind_are_strict`; `test_parent_direction_rejects_delivery_terminal` | **Pi-channel-only.** Codex owns native JSON-RPC IDs and M3 correlates observed standard fields. Codex equivalents: native prompt identity and response tests in `test_real_codex_native_mrtr.py`, plus `test_codex_mrtr_association.py`. |
| `test_terminal_delivery_requires_response_acceptance`; `test_authenticated_pending_response_terminal_round_trip`; `test_pi_managed_control_delivery_continues_across_turns` | **Pi-channel-only mechanism.** Codex managed delivery uses normal `agent.submit`/`ExecutionHandle.respond_elicitation`; installed managed keyed-round, multi-round, and URL tests passed. |
| `test_pi_managed_pending_round_fails_when_peer_exits` (both `pi_exit` and `control_disconnect` cases); `test_pi_control_disconnect_fails_durable_managed_round` | No Pi peer or control socket exists. `test_installed_codex_process_loss_fails_pending_round_without_replay` covers Codex process loss and terminalizes the managed pending round without retry/replay. |
| `test_pi_liveness_watchers_stop_after_response_is_delivered`; `test_cancellation_closes_active_scope_and_rejects_late_response` | **Pi-channel-only mechanism.** Codex action-local subscriptions are covered by action lifecycle tests; native interruption and managed process loss passed the installed gate. Codex has no Pi liveness watcher or late control-channel response. |
| `test_channel_constructor_bounds_timeout_and_queue`; `test_envelope_validation_rejects_oversized_frame`; `test_unterminated_oversized_frame_and_full_queue_are_terminal` | No separate Codex control channel. M3 transport observer bounds and incomplete signaling are shared in `test_capture_proxy.py`; Codex app-server framing remains Codex-owned. |
| `test_authenticated_channel_replay_cache_allows_long_action`; `test_bad_auth_and_duplicate_message_close_connection`; `test_eof_is_visible_and_no_files_are_used` | **Pi-channel-only.** There is no cross-process Codex extension channel to authenticate. M3's stdio observer already uses an authenticated bounded channel; keep its transport tests in `test_capture_proxy.py`. |

## Pi adapter tests

Source: the Pi cases in [`test_codex_pi_adapters.py`](unit/test_codex_pi_adapters.py).
The module name is shared because it tests both native adapters, but these
cases specifically exercise Pi. Codex tests must assert its own App Server
interaction and must not copy Pi extension behavior.

| Pi adapter case(s) | Codex counterpart or gap |
| --- | --- |
| `test_pi_managed_round_limit_reaches_action_context`; `test_pi_completed_provider_turn_fails_when_required_plan_is_unused`; `test_pi_extension_finalizes_only_at_settled_action_boundary` | `test_lower_m3_round_limit_fails_immediately_before_native_cap`, `test_codex_tenth_round_failure_uses_native_item_without_interrupt`, the installed native-cap managed cases, and `test_terminal_barrier_drains_published_keyed_retry_before_plan_completion`. | **Covered:** action limit and settled-boundary plan completion are tested; the Codex-specific effective maximum is nine, not Pi's control-channel limit. |
| `test_pi_qualified_names_are_readable_safe_and_bounded`; `test_pi_bridge_catalog_names_are_stable_and_calls_are_routed`; `test_pi_bridge_qualified_names_are_safe_bounded_and_metadata_cannot_override` | **Implementation distinction.** Codex's native catalog uses its own MCP function names, not generated Pi bridge names. Managed e2e checks the native `mcp__fixture::...` tool. No Pi bridge alias behavior is required. |
| `test_pi_rejects_tampered_dynamic_tool_map`; `test_pi_accepts_bridge_only_dynamic_tool_map` | **Pi-only.** Codex generates its own tool catalog from configured MCP servers; M3 does not inject or trust a Pi dynamic tool map. Check Codex's native server/tool identity in the action and e2e tests. |
| `test_pi_declares_verified_agent_mrtr_capabilities`; `test_pi_preflight_downgrades_mrtr_for_unverified_version`; `test_pi_extension_has_no_request_local_timeout` | Codex checks its installed version before enabling action-bound MRTR and accepts only `codex-cli 0.156.1`; the required gate installs that exact version. Other versions remain unsupported until separately characterized. Codex uses the App Server request lifecycle, not a Pi extension timeout. |
| `test_pi_action_channel_is_turn_scoped_and_cleaned`; `test_pi_open_closes_native_session_when_control_extension_does_not_connect`; `test_bundled_pi_extension_loads_without_starting_a_model_turn` | **Pi-only control extension.** Codex App Server startup/session tests use the native adapter; Codex action tests cover subscription startup and cleanup. No bundled Codex extension is added. |
| `test_native_cancel_sends_pi_abort_before_cleanup`; `test_pi_cancel_marks_inflight_turn_cancelled_and_keeps_process` | `test_real_codex_interrupt_before_answer_prevents_mcp_retry`, `test_confirmed_turn_interrupt_keeps_codex_process_reusable`, and managed process-loss coverage prove Codex-native interruption/cleanup. Do not send Pi abort frames to Codex. |
| `test_pi_streaming_tool_events_do_not_duplicate_execution_observations`; `test_pi_tool_observation_recovers_special_character_identity`; `test_pi_tool_observation_recovers_duplicate_tool_names_per_server` | Codex association uses exact captured JSON-RPC identity plus configured connection/server identity. Action/association and installed trace tests passed with one logical call, distinct attempts, and server/tool attribution. |
| `test_pi_next_frame_preserves_native_frame_when_control_is_ready_together`; `test_pi_next_frame_handles_more_than_recursion_limit_control_frames`; `test_pi_next_frame_discards_native_buffer_when_control_delivery_fails` | **Pi-only frame arbitration** between extension channel and Pi JSONL. Codex is driven by the App Server protocol; its request/response identity remains covered by native characterization, with M3 observer terminal-barrier tests in `test_capture_proxy.py`. |
| `test_native_timeout_closes_process_after_async_cancel` (shared parametrization includes `CodexHarnessAdapter` and `PiHarnessAdapter`) | This is already cross-harness; keep its Codex parametrization. No duplicate Codex-only test needed. |

## Trace projection

The Pi-only trace case is
`test_reported_pi_result_keeps_wire_mrtr_attempts_as_one_logical_call` in
[`test_mrtr_trace_projection.py`](unit/test_mrtr_trace_projection.py). Its
Codex real managed counterparts are
`test_installed_codex_real_managed_async_preserves_multi_round_trace` and
`test_installed_codex_real_managed_sync_persists_keyed_round_and_trace` in
[`test_real_codex_managed_mrtr.py`](e2e/test_real_codex_managed_mrtr.py).
Both passed the pinned Codex gate with one logical call and its observed wire
attempts. All other `test_mrtr_*` trace projection cases are protocol-wide,
not Pi-specific; keep them shared. Codex-specific
assertions must check exact observed retry identity, action association,
one logical tool-call projection, current-round responses, and no merge of
ambiguous or unrelated attempts.

## Real Pi binary gate cases

Source: [`test_real_pi_mrtr_gate.py`](e2e/test_real_pi_mrtr_gate.py). These
tests use installed Pi 0.85.1 and a deterministic local provider.

| Pi real-binary test | Codex test/counterpart | Status or gap |
| --- | --- | --- |
| `test_installed_pi_real_form_round_uses_one_logical_call` | `test_real_codex_negotiates_modern_mcp_and_surfaces_a_form`; `test_installed_codex_real_managed_sync_persists_keyed_round_and_trace` | **Passed:** native prompt and M3 managed keyed response project as one logical call. |
| `test_installed_pi_real_same_session_can_elicit_on_two_turns` | `test_codex_session_attaches_plan_only_to_second_turn` proves an ordinary first turn stays unplanned and the second turn owns its plan; `test_same_codex_session_uses_fresh_scope_for_two_planned_turns` covers two planned turns. | **Verified by the 40-test pinned gate:** ordinary-to-planned scoping and two successive planned actions each use a fresh action scope. |
| `test_installed_pi_real_url_round_asserts_without_visiting` | `test_real_codex_surfaces_url_and_empty_form_requests`; `test_installed_codex_real_managed_async_preserves_url_round` | **Passed:** URL identity, normalized empty accepted content, and no navigation are verified. |
| `test_installed_pi_real_sequential_rounds_preserve_current_responses` | `test_real_codex_keeps_separate_input_required_rounds_separate`; `test_installed_codex_real_managed_async_preserves_multi_round_trace` | **Passed:** the managed multi-round trace preserves round separation, current response keys, and one logical operation. |
| `test_installed_pi_real_same_round_preserves_all_keyed_responses` | `test_real_codex_sends_each_native_request_for_a_multi_request_round`; action batch test in `test_codex_mrtr_action.py`; managed `round_of` example. | **Passed:** both native prompts are matched and answered by key despite Codex reversing their order. |
| `test_installed_pi_real_cancellation_cleans_action_channel` | `test_real_codex_interrupt_before_answer_prevents_mcp_retry`, `test_confirmed_turn_interrupt_keeps_codex_process_reusable`, and `test_installed_codex_process_loss_fails_pending_round_without_replay`. | **Passed for Codex lifecycle:** interruption and process loss terminalize the action without a fabricated MCP retry or replay. |
| `test_installed_pi_real_agent_settled_rejects_unused_required_plan` | Terminal barrier and plan-completion action tests in `test_codex_mrtr_action.py`; `test_installed_codex_fails_when_required_plan_is_unused` covers the installed binary. | **Verified by the 40-test pinned gate:** Codex can finish an action without prompting, and M3 rejects a required plan that was not consumed. |
| `test_installed_pi_real_managed_submit_async_uses_keyed_round_response` | `test_installed_codex_real_managed_async_preserves_multi_round_trace`. | **Passed:** managed async submission persists and resumes the keyed response. |
| `test_installed_pi_real_managed_submit_async_preserves_multi_rounds` | `test_installed_codex_real_managed_async_preserves_multi_round_trace`. | **Passed:** ordinal rounds, one logical operation, and current keyed responses are preserved. |
| `test_installed_pi_real_managed_submit_async_preserves_url_round` | `test_installed_codex_real_managed_async_preserves_url_round`. | **Passed:** managed async URL round retains identity and uses Codex's normalized accepted response. |
| `test_installed_pi_real_managed_submit_sync_uses_keyed_round_response` | `test_installed_codex_real_managed_sync_persists_keyed_round_and_trace`. | **Passed:** managed sync submission persists keyed input and its correlated trace. |

## Codex runnable example counterparts

[`test_modern_mrtr_codex.py`](../examples/tests/test_modern_mrtr_codex.py)
runs the harness-bound examples from
[`elicitation.md`](../docs/elicitation.md) through installed Codex 0.156.1 and
the local deterministic provider. Each test gives the separate Codex tool
approval path an explicit `permission_policy="allow"`, then verifies that the
MRTR plan handles only native elicitation prompts. Execution reaches the
expected MCP retries and Codex turn completion. All seven guide cases and the
action-scope cases passed the 40-test pinned binary gate, including the strict
assertion that each operation is one logical call with its wire attempts
attached. The adjacent `test_modern_mrtr_codex_action_scopes.py` covers four
non-accept response cases, two planned session turns, and unused-plan failure.
The current required workflow also runs
[`test_modern_mrtr_codex_approval_spoof.py`](../examples/tests/test_modern_mrtr_codex_approval_spoof.py),
which checks forged approval metadata with and without a plan. These two cases
were not part of the historical 40-test result described above.

| Elicitation guide case | Codex runnable counterpart | Required check |
| --- | --- | --- |
| Known server and operation on `agent.run` | `test_qualified_codex_agent_retries_one_logical_tool_call` | One logical tool call with input-required and continuation attempts, same arguments, and the keyed accepted form. |
| Model-selected operation on `agent.run` | `test_unqualified_codex_agent_leaves_tool_choice_to_provider` | The plan answers the actual provider-selected MCP tool; no operation name is forced into the plan. |
| `one_of(home, business)` followed by URL | `test_codex_address_choice_then_url_in_one_tool_call[home]`; `[business]` | One selected form is answered, followed by the URL in a later round; no navigation occurs. |
| `optional(one_of(home, business))` followed by URL | `test_codex_optional_address_choice_then_url[none]`; `[home]`; `[business]` | No-address skips only the optional round; selected home/business forms and the later URL preserve current-round response keys. |
| `round_of(home, business)` followed by URL | `test_codex_two_addresses_in_one_round_then_url` | Both keyed forms are answered together before the later URL round, regardless of Codex prompt order. |
| Ordinary session turn followed by one planned turn | `test_codex_session_attaches_plan_only_to_second_turn` | The first turn has no elicitation plan or elicitations; only the second `session.send` receives the plan. |
| Planned asynchronous submission | `test_codex_submit_binds_plan_to_submitted_action` | The submitted action owns the plan and its native form response; the result is checked after completion. |

These counterparts passed the installed binary gate with the
single-logical-call trace assertion enabled.

The managed suite also covers async entry points in
[`test_real_codex_managed_mrtr.py`](e2e/test_real_codex_managed_mrtr.py):
`test_async_agent_run_uses_action_bound_form_plan_with_one_logical_call`
checks a keyed form retry on `agent.run`;
`test_async_agent_submit_uses_maybe_url_plan_and_keyed_retry` checks accepted
`maybe_url` input on `agent.submit` and the normalized `content:{}` retry; and
`test_async_session_send_scopes_plan_to_each_turn_and_skips_maybe_url` checks
plan scoping across async session turns and the no-prompt path for a `maybe_url`
plan. These cases use the local provider and stdio MCP fixture.

## Real Pi control-channel gate cases

Source: [`test_pi_control_real.py`](e2e/test_pi_control_real.py). These are
checks for the bundled Pi extension and its channel, so Codex has no direct
port of the Pi-specific behavior:

| Pi control e2e test | Codex disposition |
| --- | --- |
| `test_real_pi_bundled_extension_connects_to_control_channel` | **Pi-only.** Codex uses its native App Server; no M3 control extension is installed. |
| `test_real_pi_extension_client_round_trip_advances_two_rounds` | Pi-specific channel proof. Codex's native and installed managed multi-round tests passed with keyed responses and trace projection. |
| `test_real_pi_extension_client_cancellation_closes_scope` | Pi-specific channel cleanup. Codex native interrupt, action-local cleanup, process-loss terminalization, and no-replay tests passed. |
| `test_real_pi_extension_client_resets_for_second_action` | No Codex channel. Action-scoped observation is exercised by a normal first turn followed by a planned second turn; the two-turn session example passed. |
| `test_real_pi_extension_client_cancellation_handoff_is_not_dropped` | No Codex handoff. Codex owns interruption; native and managed tests passed without a synthetic retry. |
| `test_real_pi_extension_client_replay_cache_is_bounded` | **Pi-only.** Codex has no M3 replay cache; the separate M3 observer queue and cross-process frames are bounded in `test_capture_proxy.py`. |

## Runnable Pi example cases

Direct example
[`test_modern_mrtr_direct.py::test_direct_form_mrtr_retries_with_keyed_current_response`](../examples/tests/test_modern_mrtr_direct.py)
is shared API coverage and needs no Codex-specific duplicate. Harness-bound
examples below are mapped to the passing Codex cases or an explicit remaining
scope boundary.

| Pi example case | Codex counterpart or gap |
| --- | --- |
| `test_modern_mrtr_pi_composed.py::test_address_choice_then_url_in_one_tool_call` (home and business parameters) | `test_codex_address_choice_then_url_in_one_tool_call[home/business]`. **Passed:** one form choice and a later URL are one logical call with current keyed retries. |
| `test_modern_mrtr_pi_composed.py::test_optional_address_choice_then_url` (skip, home, business parameters) | `test_codex_optional_address_choice_then_url[none/home/business]`. **Passed:** absent optional input skips that plan item; selected form branches and the following URL complete. |
| `test_modern_mrtr_pi_composed.py::test_two_addresses_in_one_round_then_url` | `test_codex_two_addresses_in_one_round_then_url`. **Passed:** both requests are answered by key in one round, followed by the URL round. |
| `test_modern_mrtr_pi_composed.py::test_server_rejects_swapped_address_payloads` | The Codex `round_of` example checks home/business response content against each key; `test_installed_codex_fails_safely_for_identical_unkeyed_prompts` verifies fail-closed behavior when unequal responses cannot be distinguished. **Passed.** |
| `test_modern_mrtr_pi_qualified.py::test_qualified_pi_agent_retries_one_logical_tool_call` | `test_qualified_codex_agent_retries_one_logical_tool_call`. **Passed:** qualified server/tool identity and one logical call with actual attempts. |
| `test_modern_mrtr_pi_unqualified.py::test_unqualified_pi_agent_lets_model_select_the_eliciting_tool` | `test_unqualified_codex_agent_leaves_tool_choice_to_provider`. **Passed:** the provider chooses the native Codex MCP tool; the plan does not force tool selection. |
| `test_modern_mrtr_pi_session.py::test_pi_session_attaches_plan_only_to_second_turn` | `test_codex_session_attaches_plan_only_to_second_turn`. **Passed:** the first turn remains unplanned; the second action alone owns the plan. |

## Pi-only session round-limit cases

Source: [`test_agent_session.py`](unit/test_agent_session.py):
`test_pi_managed_session_forwards_configured_round_limit_to_control`,
`test_pi_managed_session_rejects_round_limit_above_control_cap`, and
`test_pi_planned_elicitation_can_exceed_managed_control_round_cap`. The
Codex adapter has no Pi managed-control round channel. Its action tests and
installed native/managed gate verify that the configured per-action limit is
respected and the effective native plan never exceeds nine prompts. The native
nine-versus-ten behavior is characterized in
`test_real_codex_handles_nine_rounds_and_rejects_a_tenth`.

## Harness capability/version declarations

Source: [`test_harness_characterize.py`](unit/test_harness_characterize.py):
`test_native_harness_interaction_evidence_is_not_inferred_from_help`,
`test_real_adapter_declarations_remain_explicit`,
`test_unverified_harness_rejects_action_bound_elicitation` (Codex,
Claude Code, ACP, and OpenCode parameters), and
`test_installed_binary_version_probe_keeps_explicit_mrtr_declaration` (Pi,
Codex, Claude Code, and OpenCode parameters). These are shared harness
capability gates, not Pi-only features. Codex enables action-bound MRTR only
for the characterized `codex-cli 0.156.1` version; the installed gate checks
that binary, and other versions remain rejected until verified. Pi remains
separately declared.

## Codex M3 completion checklist

The pinned Codex 0.156.1 gate checks the following implementation guarantees:

- [x] Codex action tests start capture before dispatch, associate native
  prompts with exact observed MCP calls without keys/order/timing guesses, and
  validate the complete prompt group before response dispatch. The malformed
  second-prompt case fails before any native response write. A separate action
  unit test injects failure on the second native result and proves the action
  stops without replaying the first result.
- [x] Every subscription event is consumed and acknowledged; the manager
  barrier drains already-accepted events, while a bounded wait proves the
  expected exact keyed retry or terminal failure before plan finalization.
- [x] Form and URL accept, decline, and cancel responses preserve the proven
  Codex/MCP response shape; URL accept uses empty content, non-accept actions
  omit content, and no URL is visited.
- [x] State-only empty input maps preserve plan state; schema matching uses
  exact JSON values with only object key ordering canonicalized and the known
  absent-meta/null representation normalized.
- [x] Identical exposed prompts with unequal answers fail before any native
  response; equal-response indistinguishable prompts are treated as a
  validated multiset, independent of Codex's reversed order.
- [x] Tool approval, tool choice, transport forwarding, retry, and cancellation
  remain Codex-owned. M3 never synthesizes tool calls, retries, approvals, or
  MCP cancellation notifications.
- [x] Codex's effective limit cannot exceed nine native MRTR prompts, and
  configured per-action/session limits fail or clamp consistently before
  Codex reaches its unobservable tenth request.
- [x] Native binary characterization, action/association unit tests, managed
  sync/async e2e round storage, session/action-bound examples, trace projection,
  cancellation/cleanup, and version capability tests pass without any paid
  model provider.

The 40-test gate verifies form and URL accept, decline, and cancel responses;
two planned elicitation actions on successive turns; unused required-plan
failure; and the guide's Codex counterparts. Native prompt/resource
elicitation and sampling/roots callbacks inside Codex tool rounds remain
outside the supported harness scope.

### Codex acceptance-test coverage boundaries

The installed Codex gate does not exercise every scenario through the real
binary. Action-unit tests now cover several failure and correlation paths; the
table distinguishes those unit results from missing installed-binary coverage.
Generic observer tests and sequential Codex tests are not real-binary
equivalents:

| Acceptance case | Current Codex evidence | Remaining scope |
| --- | --- | --- |
| Overlapping eliciting calls and same-target concurrency | `test_concurrent_eliciting_operation_fails_before_late_native_answers` verifies at the action-unit level that a second observed `input_required` operation fails the active action before late native answers are written. Sequential planned turns are covered separately by `test_same_codex_session_uses_fresh_scope_for_two_planned_turns`. | Unit behavior is covered. There is no installed real-Codex overlapping-operation test or direct concurrent-managed-handle test, so do not claim real-binary concurrency coverage. |
| Installed Codex MRTR over Streamable HTTP or in-process MCP | `test_http_proxy_publishes_original_request_and_response`, `test_in_process_loopback_is_observed_without_rewriting_endpoint`, and related `test_capture_proxy.py` cases verify the generic observer transports. | Native and managed Codex MRTR fixtures currently use stdio. There is no real-binary Codex action test proving end-to-end HTTP or in-process prompt association and response delivery. |
| Native result write fails partway through a multi-prompt batch | `test_malformed_second_prompt_fails_without_partial_answer` verifies prevalidation before writes; `test_second_native_response_write_failure_stops_batch_without_replay` injects failure on the second response after the first was written and verifies terminal failure with no replay. | The partial-write failure path is unit-verified. The installed real-binary gate does not inject native pipe failure mid-batch. |
| Timeout while an MRTR prompt or managed round is pending | `test_turn_timeout_aborts_pending_managed_mrtr_round` holds a Codex action in managed MRTR delivery until its turn deadline, then verifies the round is aborted, the process is closed, interruption is sent, and no elicitation result is written. `test_native_timeout_closes_process_after_async_cancel` covers the general blocked-turn timeout. | Pending-round timeout and cleanup are unit-verified using a fake Codex process/runtime; the installed real-binary gate does not time out a live native prompt. |
| Action correlation when JSON-RPC IDs collide by type | `test_action_correlates_integer_and_string_mcp_request_ids_separately` publishes integer ID `1` and string ID `"1"` in one action and verifies they remain distinct through completion. `test_capture_correlates_typed_ids_and_tool_latency` covers the generic capture layer too. | Typed-ID collision handling is action-unit verified. The installed real-binary gate does not exercise this controlled ID collision. |
