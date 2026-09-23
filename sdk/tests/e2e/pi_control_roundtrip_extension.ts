import { PiControlClient } from "../../src/m3/harness/pi_extension/extension.ts";

const session = process.env.M3_PI_CONTROL_SESSION;
const token = process.env.M3_PI_CONTROL_TOKEN;
const host = process.env.M3_PI_CONTROL_HOST;
const port = Number(process.env.M3_PI_CONTROL_PORT);

function pending(clientSession: string, messageId: string, roundId: string, roundIndex: number): Record<string, any> {
  return {
    type: "pending",
    session_id: clientSession,
    message_id: messageId,
    generation: "g1",
    turn_sequence: 2,
    execution_id: "execution-1",
    logical_operation_id: "op-1",
    round_id: roundId,
    round_index: roundIndex,
    round_limit: 4,
    server: "orders",
    operation_kind: "tool",
    operation_name: "book",
    request_state: roundIndex === 0 ? "opaque-state" : "",
    requests: { address: { mode: "form", request_key: "address" } },
    created_at: "2026-09-22T10:00:00+00:00",
    deadline: "2026-09-22T11:00:00+00:00",
    operation_parameters: { weight_kg: 2 },
  };
}

export default async function roundTripExtension(): Promise<void> {
  if (!session || !token || !host || !Number.isInteger(port)) throw new Error("control environment is unavailable");
  const client = new PiControlClient(host, port, session, token);
  client.onFrame = (frame: any) => {
    void (async () => {
      if (frame.type !== "response") return;
      if (frame.round_id === "r1") {
        await client.send(pending(session, "pending-2", "r2", 1));
      } else if (frame.round_id === "r2") {
        await client.send({
          type: "terminal",
          session_id: session,
          message_id: "terminal-1",
          generation: "g1",
          turn_sequence: 2,
          execution_id: "execution-1",
          logical_operation_id: "op-1",
          round_id: "r2",
          state: "delivered",
        });
      }
    })().catch((error) => console.error(error));
  };
  await client.connect();
  await client.send(pending(session, "pending-1", "r1", 0));
}
