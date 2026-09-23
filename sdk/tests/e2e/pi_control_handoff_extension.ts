import { ManagedInputCoordinator, PiControlClient, handoffManagedResponse } from "../../src/m3/harness/pi_extension/extension.ts";

const session = process.env.M3_PI_CONTROL_SESSION;
const token = process.env.M3_PI_CONTROL_TOKEN;
const host = process.env.M3_PI_CONTROL_HOST;
const port = Number(process.env.M3_PI_CONTROL_PORT);

function pending(clientSession: string): Record<string, any> {
  return {
    type: "pending",
    session_id: clientSession,
    message_id: "pending-handoff",
    generation: "g1",
    turn_sequence: 2,
    execution_id: "execution-1",
    logical_operation_id: "op-1",
    round_id: "r1",
    round_index: 0,
    round_limit: 4,
    server: "orders",
    operation_kind: "tool",
    operation_name: "book",
    request_state: "opaque-state",
    requests: { address: { mode: "form", request_key: "address" } },
    created_at: "2026-09-22T10:00:00+00:00",
    deadline: null,
    operation_parameters: {},
  };
}

export default async function handoffExtension(): Promise<void> {
  if (!session || !token || !host || !Number.isInteger(port)) throw new Error("control environment is unavailable");
  let continued = 0;
  let cancelled = 0;
  const managedInputs = new ManagedInputCoordinator();
  const delivered = managedInputs.wait("r1");
  const state = managedInputs.cancellation("r1");
  if (!state) throw new Error("managed cancellation state was not installed");
  const result = handoffManagedResponse(
    delivered,
    state,
    async () => { continued += 1; return { ok: true }; },
    async () => { cancelled += 1; return { ok: false, error: "cancelled" }; },
  );
  managedInputs.handle({
    type: "response",
    round_id: "r1",
    responses: { address: { action: "accept" } },
  });
  managedInputs.handle({ type: "cancel", round_id: "r1", reason: "same-data-event" });
  await result;
  if (continued !== 0 || cancelled !== 1) throw new Error("managed response cancellation handoff failed");

  const client = new PiControlClient(host, port, session, token);
  client.onFrame = (frame: any) => {
    if (frame.type !== "response") return;
    void client.send({
      type: "terminal",
      session_id: session,
      message_id: "terminal-handoff",
      generation: frame.generation,
      turn_sequence: frame.turn_sequence,
      execution_id: frame.execution_id,
      logical_operation_id: frame.logical_operation_id,
      round_id: frame.round_id,
      state: "delivered",
    });
  };
  await client.connect();
  await client.send(pending(session));
}
