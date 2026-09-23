import { PiControlClient } from "../../src/m3/harness/pi_extension/extension.ts";

const session = process.env.M3_PI_CONTROL_SESSION;
const token = process.env.M3_PI_CONTROL_TOKEN;
const host = process.env.M3_PI_CONTROL_HOST;
const port = Number(process.env.M3_PI_CONTROL_PORT);

function pending(clientSession: string, messageId: string, generation: string, turnSequence: number, logicalOperationId: string, roundId: string): Record<string, any> {
  return {
    type: "pending",
    session_id: clientSession,
    message_id: messageId,
    generation,
    turn_sequence: turnSequence,
    execution_id: "execution-1",
    logical_operation_id: logicalOperationId,
    round_id: roundId,
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

function terminal(clientSession: string, messageId: string, generation: string, turnSequence: number, logicalOperationId: string, roundId: string): Record<string, any> {
  return {
    type: "terminal",
    session_id: clientSession,
    message_id: messageId,
    generation,
    turn_sequence: turnSequence,
    execution_id: "execution-1",
    logical_operation_id: logicalOperationId,
    round_id: roundId,
    state: "delivered",
  };
}

export default async function actionResetExtension(): Promise<void> {
  if (!session || !token || !host || !Number.isInteger(port)) throw new Error("control environment is unavailable");
  const client = new PiControlClient(host, port, session, token);
  client.onFrame = (frame: any) => {
    void (async () => {
      if (frame.type !== "response") return;
      if (frame.round_id === "r1") {
        await client.send(terminal(session, "terminal-1", "g1", 2, "op-1", "r1"));
        await new Promise((resolve) => setTimeout(resolve, 50));
        client.beginAction("g2", 3);
        await client.send(pending(session, "pending-2", "g2", 3, "op-2", "r2"));
      } else if (frame.round_id === "r2") {
        await client.send(terminal(session, "terminal-2", "g2", 3, "op-2", "r2"));
      }
    })().catch((error) => console.error(error));
  };
  await client.connect();
  client.beginAction("g1", 2);
  await client.send(pending(session, "pending-1", "g1", 2, "op-1", "r1"));
}
