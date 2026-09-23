import { PiControlClient } from "../../src/m3/harness/pi_extension/extension.ts";

const session = process.env.M3_PI_CONTROL_SESSION;
const token = process.env.M3_PI_CONTROL_TOKEN;
const host = process.env.M3_PI_CONTROL_HOST;
const port = Number(process.env.M3_PI_CONTROL_PORT);

function pending(clientSession: string, index: number): Record<string, any> {
  return {
    type: "pending",
    session_id: clientSession,
    message_id: `pending-${index}`,
    generation: "g1",
    turn_sequence: 2,
    execution_id: "execution-1",
    logical_operation_id: `op-${index}`,
    round_id: `r-${index}`,
    round_index: 0,
    round_limit: 256,
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

function terminal(clientSession: string, index: number): Record<string, any> {
  return {
    type: "terminal",
    session_id: clientSession,
    message_id: `terminal-${index}`,
    generation: "g1",
    turn_sequence: 2,
    execution_id: "execution-1",
    logical_operation_id: `op-${index}`,
    round_id: `r-${index}`,
    state: "delivered",
  };
}

export default async function replayExtension(): Promise<void> {
  if (!session || !token || !host || !Number.isInteger(port)) throw new Error("control environment is unavailable");
  const client = new PiControlClient(host, port, session, token);
  client.onFrame = (frame: any) => {
    if (frame.type !== "response") return;
    const index = Number(String(frame.round_id).slice(2));
    void (async () => {
      if (index >= 129) return;
      await client.send(terminal(session, index));
      await client.send(pending(session, index + 1));
    })().catch((error) => console.error(error));
  };
  await client.connect();
  await client.send(pending(session, 0));
}
