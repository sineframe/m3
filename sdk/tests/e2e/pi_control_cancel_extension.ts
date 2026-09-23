import { PiControlClient } from "../../src/m3/harness/pi_extension/extension.ts";

const session = process.env.M3_PI_CONTROL_SESSION;
const token = process.env.M3_PI_CONTROL_TOKEN;
const host = process.env.M3_PI_CONTROL_HOST;
const port = Number(process.env.M3_PI_CONTROL_PORT);

function pending(clientSession: string): Record<string, any> {
  return {
    type: "pending",
    session_id: clientSession,
    message_id: "pending-cancelled",
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

export default async function cancellationExtension(): Promise<void> {
  if (!session || !token || !host || !Number.isInteger(port)) throw new Error("control environment is unavailable");
  const client = new PiControlClient(host, port, session, token);
  let responseSeen = false;
  client.onFrame = (frame: any) => {
    if (frame.type === "response") {
      responseSeen = true;
      return;
    }
    if (frame.type !== "cancel") return;
    if (!responseSeen) return;
    void client.send({
      ...pending(session),
      message_id: "pending-after-cancel",
      logical_operation_id: "op-2",
      round_id: "r2",
    }).catch(() => undefined);
  };
  await client.connect();
  await client.send(pending(session));
}
