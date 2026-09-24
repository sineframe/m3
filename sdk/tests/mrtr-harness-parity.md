# Pi-to-Codex MRTR parity inventory

This is the exhaustive crosswalk for existing Pi MRTR unit tests, adapter
tests, real-binary tests, managed-input tests, and runnable examples. It names
each Pi-specific test so a missing Codex case is visible. Shared API tests are
listed separately: the direct MCP clients do not depend on a harness and must
not be cloned just to create a Codex-named test.

## Status and reading the crosswalk

The unmodified Codex CLI 0.156.1 native behavior is characterized against
local deterministic fixtures. That proves what Codex does; it does not alone
prove M3's action adapter conforms. M3 Codex rows are marked **pending** until
the corresponding unit/managed test passes with the installed version. A
skipped binary gate is not a pass. The canonical limits and ownership model
are in the [Codex App Server section of the API reference](../docs/elicitation-api.md#codex-app-server-support-and-limitations).

Run the local Codex gates with no paid provider call:

```bash
M3_REQUIRE_CODEX_MRTR=1 \
  uv run --project sdk --all-extras pytest -q \
  sdk/tests/e2e/test_real_codex_native_mrtr.py \
  sdk/tests/e2e/test_real_codex_managed_mrtr.py
```

The binary must report `codex-cli 0.156.1` by default. The native suite
characterizes Codex; the managed suite exercises M3's real action and storage
path. The e2e [README](e2e/README.md) describes version overrides and setup.

## Verified protocol differences that drive the gaps

These are observed against unmodified Codex 0.156.1 by
[`test_real_codex_native_mrtr.py`](e2e/test_real_codex_native_mrtr.py):

| Codex behavior | Consequence for a Pi-to-Codex port |
| --- | --- |
| Native requests omit the MCP request key, `requestState`, and logical round ID. | Correlate only by original MCP capture plus server identity and native fields Codex exposes. Never invent a key, infer from timing, or zip by arrival order. |
| Simultaneous prompts are all emitted before a response, and observed native order is reversed from the MCP map order. | Treat one round as an unordered prompt multiset and wait for the whole prompt group before sending any answers. |
| Identical exposed prompts may map to multiple keyed requests. | If all candidate keys have equal responses, a multiset answer is safe; unequal responses are ambiguous and must fail before any response. An exposed metadata field can distinguish requests only when Codex actually preserves it. |
| Form schemas are JSON values; native `_meta:null` represents absent server metadata. | Canonicalize object member ordering but keep types and array order significant. Match actual schema JSON, not semantic JSON Schema equivalence. Treat only this observed null/absent metadata representation as equivalent. |
| Accepted URL response is retried with `content:{}`. Decline/cancel retry with action and optional `_meta`, with content omitted. | Normalize at the Codex/MCP adapter boundary and test all three actions for form and URL. Never navigate to the URL. |
| An empty request map is automatically retried using `requestState`, with no native prompt and no `inputResponses`. | Do not consume a plan step or fabricate a prompt for state-only `input_required`; observe and verify Codex's exact retry. |
| Codex allows nine MRTR prompts/rounds; its tenth is rejected before a tenth native prompt is surfaced. | The effective Codex limit must not exceed nine, regardless of the common API's Pi default. Pi's ten-round success is not portable. |
| Tool approval is a separate native request marked `_meta.codex_approval_kind=mcp_tool_call`. | Leave tool approval and policy decisions with Codex; never answer approval from an elicitation plan. |
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
pass-through in `test_capture_proxy.py`. The Codex adapter needs its own tests
for the added native boundary and must not weaken those common contracts.

The direct example
[`test_modern_mrtr_direct.py`](../examples/tests/test_modern_mrtr_direct.py)
is intentionally harness-agnostic. The following `pi`-named examples are
harness-specific and are mapped below.

## Pi bridge unit cases

Source: [`test_pi_mrtr_bridge.py`](unit/test_pi_mrtr_bridge.py). Pi implements
MRTR through a generated bridge tool and a Pi extension; Codex must not copy
that mechanism. Codex's counterparts use Codex's native App Server request
and M3's passive MCP observer. Every case below needs an equivalent Codex
action/association test or an explicit “inapplicable” disposition.

| Pi case(s) | Codex counterpart or gap | Required disposition |
| --- | --- | --- |
| `test_channel_round_trips_generation_and_plan`; `test_bridge_rejects_stale_generation`; `test_channel_rejects_unknown_status_and_invalid_permissions`; `test_failed_generation_is_sticky_and_finalize_cannot_overwrite_it` | No Codex bridge channel. Codex uses per-action app-server IDs and observer lifecycle. | **Implementation distinction.** Cover action scoping, terminal failure, and no stale observer reuse in `test_codex_mrtr_action.py`; do not add a Pi-style channel. |
| `test_bridge_retries_form_with_current_state_and_unchanged_args`; `test_bridge_retry_uses_only_current_round_responses_and_exact_state`; `test_bridge_limit_allows_ten_rounds_and_final_retry`; `test_bridge_eleventh_round_fails_without_an_extra_retry` | `test_real_codex_native_mrtr.py` proves exact retry shape and native nine/ten boundary; `test_codex_mrtr_action.py` exercises current-round capture/association. | Native evidence exists. **Pending M3 tests** must prove only current keyed responses and unchanged arguments/state, and cap the Codex effective limit at nine. Do not copy Pi's ten-round expectation. |
| `test_bridge_supports_multi_request_round`; `test_bridge_round_of_accepts_form_and_url_together`; `test_bridge_mixed_round_merges_form_and_sampling_responses`; `test_bridge_sampling_only_and_roots_only_rounds_are_retried` | Native all-prompts-first is in `test_real_codex_native_mrtr.py::test_real_codex_sends_each_native_request_for_a_multi_request_round`; action batching is in `test_codex_mrtr_action.py::test_action_batches_all_native_prompts_before_sending_any_answer`. | Multi-prompt form/URL support is **pending managed M3 proof**. Sampling/roots rounds have no proven Codex native callback route and remain unsupported unless separately implemented and characterized. No partial answers before the full group validates. |
| `test_managed_bridge_pauses_and_continues_with_parent_keyed_response`; `test_managed_round_indices_track_elicitation_rounds_per_operation`; `test_managed_round_limit_includes_rounds_before_continuation`; `test_managed_bridge_delivers_two_rounds_with_current_keyed_responses` | `test_real_codex_managed_mrtr.py::test_installed_codex_real_managed_async_preserves_multi_round_trace`. | **Pending required binary gate.** Check current-round keys, ordinal history across turns, same logical operation, and effective nine-round cap. |
| `test_managed_bridge_preserves_url_request_identity`; `test_bridge_url_is_asserted_and_not_visited` | Native URL shape is in `test_real_codex_native_mrtr.py::test_real_codex_surfaces_url_and_empty_form_requests`; managed path is `test_installed_codex_real_managed_async_preserves_url_round`. | URL matching must preserve URL identity and not perform navigation. Native characterization passes; **managed gate pending**. |
| `test_bridge_decline_is_forwarded_without_form_content` | Native decline/cancel coverage is `test_real_codex_forwards_non_accept_form_and_url_responses` in `test_real_codex_native_mrtr.py`. | **Pending M3 adapter proof** that decline/cancel are sent without `content` for both form and URL, retaining response `_meta`. |
| `test_bridge_url_mismatch_and_schema_mismatch_fail_before_retry`; `test_bridge_form_schema_mismatch_fails_before_retry` | `test_codex_mrtr_association.py::test_schema_value_difference_does_not_match`; `test_identical_prompts_with_different_answers_fail_before_any_answer`; `test_optional_metadata_is_compared_when_native_prompt_exposes_it`. | Association unit cases exist in the worktree. Require the complete action integration to demonstrate fail-before-answer and fail-before-retry; exact JSON schema values, not schema equivalence. |
| `test_bridge_rejects_unexpected_input_without_plan`; `test_malformed_empty_input_required_fails_without_retry`; `test_non_mapping_arguments_are_rejected` | `test_state_only_input_required_uses_codex_auto_retry_without_prompt` in `test_codex_mrtr_action.py`; `test_real_codex_retries_state_only_input_required_without_native_prompt` in native e2e. | State-only empty map must not consume plan state; malformed/non-mapping observation must terminalize and never invent a prompt. M3 action tests are **pending review/gate**. |
| `test_state_only_round_does_not_consume_pi_elicitation_plan` | The corresponding Codex behavior is characterized by the two state-only tests above. | **Pending action assertion** that an auto-retry leaves the plan untouched and that a later actual prompt still matches the first plan step. |
| `test_managed_bridge_releases_claim_for_sequential_operation`; `test_managed_continue_failure_cleans_claim_and_is_terminal`; `test_bridge_cancellation_marks_generation_failed_and_cannot_be_reused`; `test_bridge_rejects_second_same_target_eliciting_invocation`; `test_concurrent_eliciting_calls_cancel_first_and_fail_generation` | Codex action tests and `test_real_codex_interrupt_before_answer_prevents_mcp_retry` characterize native request handling and cancellation. | No Pi generation claim is copied. **Pending M3 lifecycle tests** must prove sequential actions start fresh, failures are sticky, outstanding action cancellation cleans subscriptions, and concurrent same-target elicitation fails closed without cross-action attribution. |
| `test_bridge_requires_plan_completion_at_action_finalize` | `test_terminal_barrier_drains_published_keyed_retry_before_plan_completion` in `test_codex_mrtr_action.py`; managed e2e trace tests. | **Pending full action gate.** Terminal barrier proves only manager-accepted events; bounded exact retry/plan completion wait must catch child delivery lag. |
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
| `test_terminal_delivery_requires_response_acceptance`; `test_authenticated_pending_response_terminal_round_trip`; `test_pi_managed_control_delivery_continues_across_turns` | **Pi-channel-only.** Codex managed delivery is tested through normal `agent.submit`/`ExecutionHandle.respond_elicitation` in `test_real_codex_managed_mrtr.py`; required native gate remains pending. |
| `test_pi_managed_pending_round_fails_when_peer_exits` (both `pi_exit` and `control_disconnect` cases); `test_pi_control_disconnect_fails_durable_managed_round` | No Pi peer or control socket exists. Codex process interruption/turn failure and M3 pending-round terminalization must be covered in Codex action lifecycle tests; currently **pending** beyond native interruption characterization. |
| `test_pi_liveness_watchers_stop_after_response_is_delivered`; `test_cancellation_closes_active_scope_and_rejects_late_response` | **Pi-channel-only.** Codex native resolution and interruption are in native e2e; action scope cancellation and late-event rejection require M3 adapter tests. |
| `test_channel_constructor_bounds_timeout_and_queue`; `test_envelope_validation_rejects_oversized_frame`; `test_unterminated_oversized_frame_and_full_queue_are_terminal` | No separate Codex control channel. M3 transport observer bounds and incomplete signaling are shared in `test_capture_proxy.py`; Codex app-server framing remains Codex-owned. |
| `test_authenticated_channel_replay_cache_allows_long_action`; `test_bad_auth_and_duplicate_message_close_connection`; `test_eof_is_visible_and_no_files_are_used` | **Pi-channel-only.** There is no cross-process Codex extension channel to authenticate. M3's stdio observer already uses an authenticated bounded channel; keep its transport tests in `test_capture_proxy.py`. |

## Pi adapter tests

Source: the Pi cases in [`test_codex_pi_adapters.py`](unit/test_codex_pi_adapters.py).
The module name is shared because it tests both native adapters, but these
cases specifically exercise Pi. Codex tests must assert its own App Server
interaction and must not copy Pi extension behavior.

| Pi adapter case(s) | Codex counterpart or gap |
| --- | --- |
| `test_pi_managed_round_limit_reaches_action_context`; `test_pi_completed_provider_turn_fails_when_required_plan_is_unused`; `test_pi_extension_finalizes_only_at_settled_action_boundary` | Codex action/terminal coverage: `test_codex_mrtr_action.py::test_terminal_barrier_drains_published_keyed_retry_before_plan_completion` and managed e2e. **Pending** exact unused-plan failure and Codex-specific limit clamp (maximum nine). |
| `test_pi_qualified_names_are_readable_safe_and_bounded`; `test_pi_bridge_catalog_names_are_stable_and_calls_are_routed`; `test_pi_bridge_qualified_names_are_safe_bounded_and_metadata_cannot_override` | **Implementation distinction.** Codex's native catalog uses its own MCP function names, not generated Pi bridge names. Managed e2e checks the native `mcp__fixture::...` tool. No Pi bridge alias behavior is required. |
| `test_pi_rejects_tampered_dynamic_tool_map`; `test_pi_accepts_bridge_only_dynamic_tool_map` | **Pi-only.** Codex generates its own tool catalog from configured MCP servers; M3 does not inject or trust a Pi dynamic tool map. Check Codex's native server/tool identity in the action and e2e tests. |
| `test_pi_declares_verified_agent_mrtr_capabilities`; `test_pi_preflight_downgrades_mrtr_for_unverified_version`; `test_pi_extension_has_no_request_local_timeout` | Codex capability declaration and installed-version gate are `test_real_adapter_declarations_remain_explicit` and `test_installed_binary_version_probe_keeps_explicit_mrtr_declaration` in `test_harness_characterize.py`; they must be updated to attest only the verified Codex version/feature set. **Pending until the required M3 gate passes.** |
| `test_pi_action_channel_is_turn_scoped_and_cleaned`; `test_pi_open_closes_native_session_when_control_extension_does_not_connect`; `test_bundled_pi_extension_loads_without_starting_a_model_turn` | **Pi-only control extension.** Codex app-server startup/session tests use the native adapter; action subscription startup and cleanup require dedicated Codex tests. No bundled Codex extension is added. |
| `test_native_cancel_sends_pi_abort_before_cleanup`; `test_pi_cancel_marks_inflight_turn_cancelled_and_keeps_process` | Native counterpart: `test_real_codex_interrupt_before_answer_prevents_mcp_retry`. M3 cancellation/observer cleanup parity is **pending**; do not send Pi abort frames to Codex. |
| `test_pi_streaming_tool_events_do_not_duplicate_execution_observations`; `test_pi_tool_observation_recovers_special_character_identity`; `test_pi_tool_observation_recovers_duplicate_tool_names_per_server` | Codex already has native MCP tool item events, but MRTR association must use exact captured JSON-RPC identity plus configured connection/server identity. The Codex action and association suites must prove one logical call and no cross-server conflation; **pending managed conformance**. |
| `test_pi_next_frame_preserves_native_frame_when_control_is_ready_together`; `test_pi_next_frame_handles_more_than_recursion_limit_control_frames`; `test_pi_next_frame_discards_native_buffer_when_control_delivery_fails` | **Pi-only frame arbitration** between extension channel and Pi JSONL. Codex is driven by the App Server protocol; its request/response identity remains covered by native characterization, with M3 observer terminal-barrier tests in `test_capture_proxy.py`. |
| `test_native_timeout_closes_process_after_async_cancel` (shared parametrization includes `CodexHarnessAdapter` and `PiHarnessAdapter`) | This is already cross-harness; keep its Codex parametrization. No duplicate Codex-only test needed. |

## Trace projection

The Pi-only trace case is
`test_reported_pi_result_keeps_wire_mrtr_attempts_as_one_logical_call` in
[`test_mrtr_trace_projection.py`](unit/test_mrtr_trace_projection.py). Its
Codex real managed counterparts are
`test_installed_codex_real_managed_async_preserves_multi_round_trace` and
`test_installed_codex_real_managed_sync_persists_keyed_round_and_trace` in
[`test_real_codex_managed_mrtr.py`](e2e/test_real_codex_managed_mrtr.py),
which are **pending the pinned gate**. All other `test_mrtr_*` trace projection
cases are protocol-wide, not Pi-specific; keep them shared. Codex-specific
assertions must check exact observed retry identity, action association,
one logical tool-call projection, current-round responses, and no merge of
ambiguous or unrelated attempts.

## Real Pi binary gate cases

Source: [`test_real_pi_mrtr_gate.py`](e2e/test_real_pi_mrtr_gate.py). These
tests use installed Pi 0.85.1 and a deterministic local provider.

| Pi real-binary test | Codex test/counterpart | Status or gap |
| --- | --- | --- |
| `test_installed_pi_real_form_round_uses_one_logical_call` | `test_real_codex_negotiates_modern_mcp_and_surfaces_a_form`; `test_installed_codex_real_managed_sync_persists_keyed_round_and_trace` | Native Codex request is characterized; M3 managed test is **pending gate**. |
| `test_installed_pi_real_same_session_can_elicit_on_two_turns` | None yet. | **Pending:** two action-scoped plans in one Codex session, with no plan leakage across turns. |
| `test_installed_pi_real_url_round_asserts_without_visiting` | `test_real_codex_surfaces_url_and_empty_form_requests`; `test_installed_codex_real_managed_async_preserves_url_round` | URL normalization and no-visit behavior are characterized; M3 managed gate **pending**. |
| `test_installed_pi_real_sequential_rounds_preserve_current_responses` | `test_real_codex_keeps_separate_input_required_rounds_separate`; `test_installed_codex_real_managed_async_preserves_multi_round_trace` | Native round separation is characterized; M3 exact response-scope/trace gate **pending**. |
| `test_installed_pi_real_same_round_preserves_all_keyed_responses` | `test_real_codex_sends_each_native_request_for_a_multi_request_round`; action batch test in `test_codex_mrtr_action.py` | Native all-prompts-first is characterized; **pending managed keyed-response gate**, including reversed native order. |
| `test_installed_pi_real_cancellation_cleans_action_channel` | `test_real_codex_interrupt_before_answer_prevents_mcp_retry` | Codex native cancellation is characterized. M3 action subscription/plan terminal cleanup remains **pending**. |
| `test_installed_pi_real_agent_settled_rejects_unused_required_plan` | Terminal barrier test in `test_codex_mrtr_action.py`; no exact real managed unused-plan counterpart yet. | **Pending:** prove Codex terminal handling rejects an unused required plan after draining captured observations. |
| `test_installed_pi_real_managed_submit_async_uses_keyed_round_response` | `test_installed_codex_real_managed_async_preserves_multi_round_trace` | Similar persisted async path; **pending** installed Codex gate. |
| `test_installed_pi_real_managed_submit_async_preserves_multi_rounds` | Same Codex managed multi-round test. | **Pending** M3 gate; preserve ordinal rounds, shared logical operation ID, keys, and idempotency. |
| `test_installed_pi_real_managed_submit_async_preserves_url_round` | `test_installed_codex_real_managed_async_preserves_url_round` | **Pending** M3 gate, including normalized empty URL content. |
| `test_installed_pi_real_managed_submit_sync_uses_keyed_round_response` | `test_installed_codex_real_managed_sync_persists_keyed_round_and_trace` | **Pending** M3 gate for sync managed submit, keyed storage, and trace. |

## Real Pi control-channel gate cases

Source: [`test_pi_control_real.py`](e2e/test_pi_control_real.py). These are
checks for the bundled Pi extension and its channel, so Codex has no direct
port of the Pi-specific behavior:

| Pi control e2e test | Codex disposition |
| --- | --- |
| `test_real_pi_bundled_extension_connects_to_control_channel` | **Pi-only.** Codex uses its native App Server; no M3 control extension is installed. |
| `test_real_pi_extension_client_round_trip_advances_two_rounds` | Pi-specific channel proof. Codex's native+managed multi-round test pair is the appropriate counterpart and remains gate-pending. |
| `test_real_pi_extension_client_cancellation_closes_scope` | Pi-specific channel cleanup. Codex native interrupt is characterized; M3 action cleanup remains pending. |
| `test_real_pi_extension_client_resets_for_second_action` | No Codex channel. Add/retain action-scope integration proving a second Codex action starts cleanly; **pending**. |
| `test_real_pi_extension_client_cancellation_handoff_is_not_dropped` | No Codex handoff. Codex owns native interruption and must not receive a synthetic retry; native test exists, M3 cleanup gate pending. |
| `test_real_pi_extension_client_replay_cache_is_bounded` | **Pi-only.** Codex has no M3 replay cache; the separate M3 observer queue and cross-process frames are bounded in `test_capture_proxy.py`. |

## Runnable Pi example cases

Direct example
[`test_modern_mrtr_direct.py::test_direct_form_mrtr_retries_with_keyed_current_response`](../examples/tests/test_modern_mrtr_direct.py)
is shared API coverage and needs no Codex-specific duplicate. Harness-bound
examples below define expected Codex M3 behavior; they remain Pi-only runnable
examples until equivalent Codex-marked examples or managed fixture cases pass.

| Pi example case | Codex counterpart or gap |
| --- | --- |
| `test_modern_mrtr_pi_composed.py::test_address_choice_then_url_in_one_tool_call` (home and business parameters) | Native Codex form, URL, and multi-round are characterized separately; **pending** managed composed-plan coverage preserving one logical call. |
| `test_modern_mrtr_pi_composed.py::test_optional_address_choice_then_url` (skip, home, business parameters) | **Pending:** prove optional-plan completion when Codex exposes no first-round prompt, and the selected branch when one appears. |
| `test_modern_mrtr_pi_composed.py::test_two_addresses_in_one_round_then_url` | Native simultaneous prompt group and action batching are tested; **pending** full managed `round_of` response and subsequent URL sequence. |
| `test_modern_mrtr_pi_composed.py::test_server_rejects_swapped_address_payloads` | **Pending real managed test** that wrong prompt-to-key association cannot result in a swapped answer; fail closed for unequal ambiguous prompts. |
| `test_modern_mrtr_pi_qualified.py::test_qualified_pi_agent_retries_one_logical_tool_call` | Codex managed e2e uses `fixture:book_shipment` and native tool catalog. **Pending** Codex-marked example / gate asserting qualified server+tool identity and one logical call. |
| `test_modern_mrtr_pi_unqualified.py::test_unqualified_pi_agent_lets_model_select_the_eliciting_tool` | Codex's native provider tool catalog is characterized; **pending** Codex action test where the model selects the eliciting operation without M3 taking tool choice. |
| `test_modern_mrtr_pi_session.py::test_pi_session_attaches_plan_only_to_second_turn` | **Pending:** Codex session test with an unplanned first turn and action-bound plan only on the later eliciting turn. |

## Pi-only session round-limit cases

Source: [`test_agent_session.py`](unit/test_agent_session.py):
`test_pi_managed_session_forwards_configured_round_limit_to_control`,
`test_pi_managed_session_rejects_round_limit_above_control_cap`, and
`test_pi_planned_elicitation_can_exceed_managed_control_round_cap`. The
Codex adapter has no Pi managed-control round channel. Its counterpart must
clamp the effective native plan to at most nine and keep the generic API's
chosen round limit/action binding consistent. This is **pending a dedicated
Codex limit test**; the native nine-versus-ten behavior is characterized in
`test_real_codex_handles_nine_rounds_and_rejects_a_tenth`.

## Harness capability/version declarations

Source: [`test_harness_characterize.py`](unit/test_harness_characterize.py):
`test_native_harness_interaction_evidence_is_not_inferred_from_help`,
`test_real_adapter_declarations_remain_explicit`,
`test_unverified_harness_rejects_action_bound_elicitation` (Codex,
Claude Code, ACP, and OpenCode parameters), and
`test_installed_binary_version_probe_keeps_explicit_mrtr_declaration` (Pi,
Codex, Claude Code, and OpenCode parameters). These are shared harness
capability gates, not Pi-only features. They must move Codex from unsupported
only when the implementation has passed its required MRTR suite, and must keep
unverified Codex versions gated; Pi remains separately declared.

## Codex M3 completion checklist

The existing mapping above is the specific pending implementation/test list.
Before calling Codex support complete, require all of the following:

- [ ] Codex action tests start capture before dispatch, associate native
  prompts with exact observed MCP calls without keys/order/timing guesses, and
  send no partial response on malformed, missing, repeated, or ambiguous
  prompts.
- [ ] Every subscription event is consumed and acknowledged; the manager
  barrier drains already-accepted events, while a bounded wait proves the
  expected exact keyed retry or terminal failure before plan finalization.
- [ ] Form and URL accept/decline/cancel preserve the proven Codex/MCP response
  shape; URL accept uses empty content, and no URL is visited.
- [ ] State-only empty input maps preserve plan state; schema matching uses
  exact JSON values with only object key ordering canonicalized and the known
  absent-meta/null representation normalized.
- [ ] Identical exposed prompts with unequal answers fail before any native
  response; equal-response indistinguishable prompts are treated as a
  validated multiset, independent of Codex's reversed order.
- [ ] Tool approval, tool choice, transport forwarding, retry, and cancellation
  remain Codex-owned. M3 never synthesizes tool calls, retries, approvals, or
  MCP cancellation notifications.
- [ ] Codex's effective limit cannot exceed nine native MRTR prompts, and
  configured per-action/session limits fail or clamp consistently before
  Codex reaches its unobservable tenth request.
- [ ] Native binary characterization, action/association unit tests, managed
  sync/async e2e round storage, session/action-bound examples, trace projection,
  cancellation/cleanup, and version capability tests pass without any paid
  model provider.
