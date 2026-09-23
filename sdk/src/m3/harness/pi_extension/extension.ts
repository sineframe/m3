// Private M3 Pi extension. It speaks a bounded JSONL protocol to the
// package-owned Python bridge; no Pi-global configuration is modified.
import { spawn } from "node:child_process";
import { connect, Socket } from "node:net";
import { StringDecoder } from "node:string_decoder";

const CONTROL_PROTOCOL_VERSION = 1;
const MAX_CONTROL_FRAME_BYTES = 64 * 1024;
const CONTROL_TIMEOUT_MS = 5000;
const MAX_RECENT_CONTROL_MESSAGE_IDS = 128;
const PARENT_TO_EXTENSION = new Set(["response", "cancel", "close"]);
const EXTENSION_TO_PARENT = new Set(["pending", "cancel", "terminal", "close"]);
const OPERATION_KINDS = new Set(["tool", "prompt", "resource"]);
const MAX_ROUND_LIMIT = 1024;

type ControlScope = { generation: string; turn_sequence: number; execution_id: string; logical_operation_id: string; round_id: string };
type ControlFrame = Record<string, any>;
type ActionIdentity = { generation: string; turn_sequence: number };
export type ManagedCancellation = { requested: boolean; cancel?: () => void };

export class ManagedInputCoordinator {
  private readonly waiters = new Map<string, (value: any) => void>();
  private readonly cancellations = new Map<string, ManagedCancellation>();

  wait(roundId: string): Promise<any> {
    return new Promise<any>((resolve) => {
      const cancellation: ManagedCancellation = { requested: false };
      this.cancellations.set(roundId, cancellation);
      this.waiters.set(roundId, resolve);
    });
  }

  cancellation(roundId: string): ManagedCancellation | undefined {
    return this.cancellations.get(roundId);
  }

  handle(frame: ControlFrame): void {
    if (frame.type !== "response" && frame.type !== "cancel") return;
    const cancellation = this.cancellations.get(frame.round_id);
    if (frame.type === "cancel" && cancellation) {
      cancellation.requested = true;
      cancellation.cancel?.();
    }
    const waiter = this.waiters.get(frame.round_id);
    if (!waiter) return;
    this.waiters.delete(frame.round_id);
    waiter(frame.type === "cancel" ? { cancelled: true, reason: frame.reason } : { responses: frame.responses });
  }

  clear(roundId: string): void {
    this.waiters.delete(roundId);
    this.cancellations.delete(roundId);
  }
}

function controlString(value: any, name: string, maximum = 128): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum) throw new Error(`invalid control ${name}`);
  return value;
}

function controlSequence(value: any): number {
  if (!Number.isInteger(value) || value < 0) throw new Error("invalid control turn sequence");
  return value;
}

function controlMap(value: any, name: string): Record<string, any> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`invalid control ${name}`);
  for (const key of Object.keys(value)) {
    if (key.length === 0 || [...key].length > 256) throw new Error(`invalid control ${name} key`);
  }
  return value;
}

function controlTimestamp(value: any, name: string, optional = false): string | null {
  if (value === null && optional) return null;
  const timestamp = controlString(value, name, 64);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(timestamp) || !Number.isFinite(Date.parse(timestamp))) throw new Error(`invalid control ${name}`);
  return timestamp;
}

function controlScope(frame: ControlFrame): ControlScope {
  return {
    generation: controlString(frame.generation, "generation"),
    turn_sequence: controlSequence(frame.turn_sequence),
    execution_id: controlString(frame.execution_id, "execution id"),
    logical_operation_id: controlString(frame.logical_operation_id, "logical operation id"),
    round_id: controlString(frame.round_id, "round id"),
  };
}

function sameScope(left: ControlScope, right: ControlScope): boolean {
  return left.generation === right.generation && left.turn_sequence === right.turn_sequence && left.execution_id === right.execution_id && left.logical_operation_id === right.logical_operation_id && left.round_id === right.round_id;
}

export async function handoffManagedResponse(
  delivered: Promise<any>,
  cancellation: ManagedCancellation,
  continueTool: () => Promise<any>,
  cancelTool: () => Promise<any>,
): Promise<{ cancelled: true; result: any } | { cancelled: false; result: any }> {
  const value = await delivered;
  if (value?.cancelled || cancellation.requested) {
    return { cancelled: true, result: await cancelTool() };
  }
  const cancelled = new Promise<void>((resolve) => {
    cancellation.cancel = resolve;
  });
  if (cancellation.requested) {
    return { cancelled: true, result: await cancelTool() };
  }
  const outcome = await Promise.race([
    continueTool().then((result) => ({ cancelled: false as const, result })),
    cancelled.then(() => ({ cancelled: true as const })),
  ]);
  if (outcome.cancelled) {
    return { cancelled: true, result: await cancelTool() };
  }
  return { cancelled: false, result: outcome.result };
}

function encodeControl(frame: ControlFrame): Buffer {
  const encoded = Buffer.from(JSON.stringify(frame) + "\n", "utf8");
  if (encoded.length > MAX_CONTROL_FRAME_BYTES) throw new Error("control envelope is too large");
  return encoded;
}

function validateControl(frame: any, sessionId: string, scope?: ControlScope, direction?: "parent_to_extension" | "extension_to_parent"): ControlFrame {
  if (!frame || typeof frame !== "object" || Array.isArray(frame)) throw new Error("invalid control envelope");
  if (frame.type === "hello") {
    const keys = Object.keys(frame).sort().join(",");
    if (keys !== "accepted,session_id,type,version" || frame.version !== CONTROL_PROTOCOL_VERSION || frame.session_id !== sessionId || frame.accepted !== true) throw new Error("invalid control hello");
    return frame;
  }
  if (!["pending", "response", "cancel", "terminal", "close"].includes(frame.type)) throw new Error("unknown control message");
  if (direction === "parent_to_extension" && !PARENT_TO_EXTENSION.has(frame.type)) throw new Error("control message is not valid in this direction");
  if (direction === "extension_to_parent" && !EXTENSION_TO_PARENT.has(frame.type)) throw new Error("control message is not valid in this direction");
  if (frame.session_id !== sessionId) throw new Error("control session mismatch");
  if (frame.type === "close") {
    const keys = Object.keys(frame).sort().join(",");
    if (keys !== "message_id,reason,session_id,type") throw new Error("invalid control close");
    controlString(frame.message_id, "message id");
    controlString(frame.reason, "reason", 512);
    return frame;
  }
  const common = new Set(["type", "session_id", "message_id", "generation", "turn_sequence", "execution_id", "logical_operation_id", "round_id"]);
  const required = new Set(common);
  const optional = new Set<string>();
  if (frame.type === "pending") {
    ["round_index", "round_limit", "server", "operation_kind", "operation_name", "request_state", "requests", "created_at", "deadline", "operation_parameters"].forEach((key) => required.add(key));
  }
  if (frame.type === "response") { required.add("responses"); optional.add("response_idempotency_key"); }
  if (frame.type === "cancel") required.add("reason");
  if (frame.type === "terminal") { required.add("state"); optional.add("error_code"); }
  if (Object.keys(frame).some((key) => !required.has(key) && !optional.has(key)) || [...required].some((key) => !(key in frame))) throw new Error("invalid control envelope");
  controlString(frame.message_id, "message id");
  const current = controlScope(frame);
  if (!scope && frame.type !== "pending") throw new Error("scoped control message has no active scope");
  if (scope && !sameScope(current, scope)) throw new Error("stale control scope");
  if (frame.type === "pending") {
    if (!Number.isInteger(frame.round_index) || frame.round_index < 0) throw new Error("invalid control round index");
    if (!Number.isInteger(frame.round_limit) || frame.round_limit < 1 || frame.round_limit > MAX_ROUND_LIMIT || frame.round_index >= frame.round_limit) throw new Error("invalid control round limit");
    controlString(frame.execution_id, "execution id");
    controlString(frame.server, "server", 256);
    if (!OPERATION_KINDS.has(frame.operation_kind)) throw new Error("invalid control operation kind");
    controlString(frame.operation_name, "operation name", 256);
    if (frame.request_state !== null && (typeof frame.request_state !== "string" || frame.request_state.length > MAX_CONTROL_FRAME_BYTES)) throw new Error("invalid control request state");
    controlMap(frame.requests, "requests");
    if (Object.keys(frame.requests).length === 0) throw new Error("control pending requests cannot be empty");
    controlTimestamp(frame.created_at, "created at");
    controlTimestamp(frame.deadline, "deadline", true);
    controlMap(frame.operation_parameters, "operation parameters");
  } else if (frame.type === "response") {
    controlMap(frame.responses, "responses");
    if (frame.response_idempotency_key !== undefined) controlString(frame.response_idempotency_key, "response idempotency key");
  } else if (frame.type === "cancel") {
    controlString(frame.reason, "reason", 512);
  } else if (frame.type === "terminal") {
    if (!["delivered", "failed", "cancelled"].includes(frame.state)) throw new Error("invalid control terminal state");
    if (frame.error_code !== undefined) controlString(frame.error_code, "error code", 512);
  }
  return frame;
}

export class PiControlClient {
  private socket: Socket | undefined;
  private buffer = "";
  private readonly decoder = new StringDecoder("utf8");
  private scope: ControlScope | undefined;
  private roundIndex: number | undefined;
  private responseSent = false;
  private operationTerminal = false;
  private readonly received = new Set<string>();
  private actionIdentity: ActionIdentity | undefined;
  private closed = false;
  onFrame: ((frame: ControlFrame) => void) | undefined;

  constructor(private readonly host: string, private readonly port: number, private readonly sessionId: string, private readonly token: string) {
    if (host !== "127.0.0.1" || !Number.isInteger(port) || port < 1 || port > 65535) throw new Error("invalid Pi control endpoint");
    controlString(sessionId, "session id");
    controlString(token, "token", 256);
  }

  beginAction(generation: string, turnSequence: number): void {
    const next: ActionIdentity = {
      generation: controlString(generation, "generation"),
      turn_sequence: controlSequence(turnSequence),
    };
    if (
      this.actionIdentity?.generation === next.generation
      && this.actionIdentity.turn_sequence === next.turn_sequence
    ) return;
    if (this.scope && !this.operationTerminal) {
      throw new Error("cannot reset an active control operation");
    }
    this.actionIdentity = next;
    this.clearScope();
  }

  async connect(): Promise<void> {
    if (this.socket) throw new Error("Pi control client is already connected");
    await new Promise<void>((resolve, reject) => {
      const socket = connect(this.port, this.host);
      this.socket = socket;
      let settled = false;
      const timeoutSignal = AbortSignal.timeout(CONTROL_TIMEOUT_MS);
      const onTimeout = () => fail(new Error("Pi control connection timed out"));
      const fail = (error: Error) => {
        if (settled) return;
        settled = true;
        timeoutSignal.removeEventListener("abort", onTimeout);
        socket.destroy();
        reject(error);
      };
      timeoutSignal.addEventListener("abort", onTimeout, { once: true });
      socket.setNoDelay(true);
      socket.on("error", (error) => fail(new Error("Pi control connection failed")));
      socket.on("close", () => {
        this.closed = true;
        if (!settled) fail(new Error("Pi control connection closed"));
      });
      socket.on("data", (chunk: Buffer) => {
        this.buffer += this.decoder.write(chunk);
        let index = -1;
        while ((index = this.buffer.indexOf("\n")) >= 0) {
          const line = this.buffer.slice(0, index);
          this.buffer = this.buffer.slice(index + 1);
          if (Buffer.byteLength(line, "utf8") + 1 > MAX_CONTROL_FRAME_BYTES) {
            fail(new Error("control envelope is too large"));
            return;
          }
          let value: any;
          try { value = JSON.parse(line); } catch (_) { fail(new Error("invalid control envelope")); return; }
          if (!settled) {
            try {
              validateControl(value, this.sessionId);
              if (value.type !== "hello") throw new Error("control hello required");
              settled = true;
              timeoutSignal.removeEventListener("abort", onTimeout);
              resolve();
            } catch (_) { fail(new Error("invalid control hello")); }
            continue;
          }
          try {
            if (value.type === "hello") throw new Error("control hello is only valid during handshake");
            const frame = validateControl(value, this.sessionId, this.scope, "parent_to_extension");
            if (this.operationTerminal && frame.type !== "close") throw new Error("control operation is terminal");
            if (this.responseSent && frame.type === "response") throw new Error("response has already been received for this round");
            if (frame.type !== "close") {
              if (this.received.has(frame.message_id)) throw new Error("duplicate control message");
              this.received.add(frame.message_id);
              // Bound recent replay detection without imposing a session lifetime cap.
              if (this.received.size > MAX_RECENT_CONTROL_MESSAGE_IDS) {
                const oldest = this.received.values().next();
                if (!oldest.done) this.received.delete(oldest.value);
              }
            }
            if (frame.type === "response") this.responseSent = true;
            if (frame.type === "cancel") this.clearScope();
            this.onFrame?.(frame);
          } catch (_) {
            socket.destroy();
            return;
          }
        }
        if (Buffer.byteLength(this.buffer, "utf8") > MAX_CONTROL_FRAME_BYTES) {
          fail(new Error("control envelope is too large"));
        }
      });
      socket.on("connect", () => {
        try {
          socket.write(encodeControl({ type: "hello", version: CONTROL_PROTOCOL_VERSION, session_id: this.sessionId, token: this.token }));
        } catch (_) { fail(new Error("invalid control hello")); }
      });
    });
  }

  async send(frame: ControlFrame): Promise<void> {
    if (this.closed || !this.socket || this.socket.destroyed) throw new Error("Pi control connection is closed");
    const validated = validateControl(frame, this.sessionId, frame.type === "pending" ? undefined : this.scope, "extension_to_parent");
    if (this.operationTerminal && validated.type !== "close" && validated.type !== "pending") throw new Error("control operation is terminal");
    if (this.responseSent && validated.type === "response") throw new Error("response has already been sent for this round");
    let nextScope: ControlScope | undefined;
    if (validated.type === "pending") {
      nextScope = controlScope(validated);
      if (!this.scope) {
        if (validated.round_index !== 0) throw new Error("control round does not start at zero");
      } else if (this.responseSent) {
        if (this.roundIndex === undefined || nextScope.generation !== this.scope.generation || nextScope.turn_sequence !== this.scope.turn_sequence || nextScope.execution_id !== this.scope.execution_id || nextScope.logical_operation_id !== this.scope.logical_operation_id || nextScope.round_id === this.scope.round_id || validated.round_index !== this.roundIndex + 1) throw new Error("control round transition is stale");
      } else if (this.operationTerminal) {
        if (nextScope.generation !== this.scope.generation || nextScope.turn_sequence !== this.scope.turn_sequence || nextScope.execution_id !== this.scope.execution_id || nextScope.logical_operation_id === this.scope.logical_operation_id || validated.round_index !== 0) throw new Error("control operation transition is stale");
      } else {
        throw new Error("control round transition is not acknowledged");
      }
    } else if (validated.type === "terminal") {
      // The bridge must emit delivered only after it accepted and forwarded
      // the response; this client enforces ordering but does not invent that
      // acknowledgement itself.
      if (validated.state === "delivered" && !this.responseSent) throw new Error("delivered terminal lacks response acceptance");
    } else if (validated.type === "cancel") {
      // Cancellation closes the active logical scope; an old response can no
      // longer validate after this write succeeds.
    }
    const encoded = encodeControl(validated);
    await new Promise<void>((resolve, reject) => {
      const socket = this.socket;
      if (!socket || socket.destroyed) { reject(new Error("Pi control connection is closed")); return; }
      const timeoutSignal = AbortSignal.timeout(CONTROL_TIMEOUT_MS);
      let settled = false;
      const done = (error?: Error) => {
        if (settled) return;
        settled = true;
        timeoutSignal.removeEventListener("abort", onTimeout);
        error ? reject(error) : resolve();
      };
      const onTimeout = () => done(new Error("Pi control write timed out"));
      timeoutSignal.addEventListener("abort", onTimeout, { once: true });
      try {
        socket.write(encoded, (error) => done(error ?? undefined));
      } catch (_) {
        done(new Error("Pi control connection is closed"));
      }
    });
    if (validated.type === "pending" && nextScope) {
      this.scope = nextScope;
      this.roundIndex = validated.round_index;
      this.responseSent = false;
      this.operationTerminal = false;
    } else if (validated.type === "response") {
      this.responseSent = true;
    } else if (validated.type === "terminal") {
      this.operationTerminal = true;
      this.responseSent = false;
    } else if (validated.type === "cancel") {
      this.clearScope();
    }
  }

  private clearScope(): void {
    this.scope = undefined;
    this.roundIndex = undefined;
    this.responseSent = false;
    this.operationTerminal = false;
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    const socket = this.socket;
    this.socket = undefined;
    if (socket && !socket.destroyed) socket.end();
  }
}

function controlFromEnvironment(): PiControlClient | undefined {
  const host = process.env.M3_PI_CONTROL_HOST;
  const rawPort = process.env.M3_PI_CONTROL_PORT;
  const session = process.env.M3_PI_CONTROL_SESSION;
  const token = process.env.M3_PI_CONTROL_TOKEN;
  if (!host && !rawPort && !session && !token) return undefined;
  if (!host || !rawPort || !session || !token) throw new Error("incomplete Pi control configuration");
  const port = Number(rawPort);
  return new PiControlClient(host, port, session, token);
}

async function piSchema(schema: any): Promise<any> {
  // The Pi runtime expects a TypeBox TSchema (including its Kind metadata),
  // while MCP advertises ordinary JSON Schema. Resolve TypeBox from Pi's own
  // module graph, never from the Python package directory.
  const moduleName = "typebox";
  const typebox: any = await import(moduleName);
  if (typeof typebox.Unsafe !== "function") throw new Error("Pi TypeBox support unavailable");
  return typebox.Unsafe(schema);
}

function piContent(content: any): any[] {
  if (!Array.isArray(content)) return [{ type: "text", text: JSON.stringify(content ?? null).slice(0, 16384) }];
  return content.map((item: any) => {
    if (item && item.type === "text" && typeof item.text === "string") return { type: "text", text: item.text.slice(0, 16384) };
    if (item && item.type === "image" && typeof item.data === "string") return { type: "image", data: item.data, mimeType: item.mimeType };
    return { type: "text", text: JSON.stringify(item ?? null).slice(0, 16384) };
  });
}

export default async function mcpPalExtension(pi: any) {
  const command = process.env.M3_PI_BRIDGE_COMMAND;
  if (!command) return;
  const argv = JSON.parse(process.env.M3_PI_BRIDGE_ARGV ?? "[]") as string[];
  const control = controlFromEnvironment();
  if (control) await control.connect();
  const controlSession = process.env.M3_PI_CONTROL_SESSION;
  const child = spawn(command, argv, { stdio: ["pipe", "pipe", "ignore"], env: process.env });
  let nextId = 0;
  const pending = new Map<number, (value: any) => void>();
  let stopped = false;
  let buffer = "";
  child.stdout.on("data", (chunk: Buffer) => { buffer += chunk.toString("utf8"); if (buffer.length > 1024 * 1024) { for (const resolve of pending.values()) resolve({ ok: false, error: "bridge frame exceeded safe size" }); pending.clear(); child.kill(); return; } let index = -1; while ((index = buffer.indexOf("\n")) >= 0) { const line = buffer.slice(0, index); buffer = buffer.slice(index + 1); try { const value = JSON.parse(line); const resolve = pending.get(value.id); if (resolve) { pending.delete(value.id); resolve(value); } } catch (_) { for (const resolve of pending.values()) resolve({ ok: false, error: "invalid bridge frame" }); pending.clear(); } } });
  child.on("error", () => { for (const resolve of pending.values()) resolve({ ok: false, error: "bridge unavailable" }); pending.clear(); });
  child.on("exit", () => { for (const resolve of pending.values()) resolve({ ok: false, error: "bridge exited" }); pending.clear(); });
  // Request lifetime is owned by the SDK turn. Its timeout cancels/closes the
  // native Pi process, so a second transport timeout would race cancellation.
  const request = (method: string, params: any = {}) => new Promise<any>((resolve) => { if (stopped) { resolve({ ok: false, error: "bridge stopped" }); return; } const id = ++nextId; pending.set(id, resolve); try { child.stdin.write(JSON.stringify({ id, method, ...params }) + "\n"); } catch (_) { pending.delete(id); resolve({ ok: false, error: "bridge unavailable" }); return; } });
  const managedInputs = new ManagedInputCoordinator();
  let nextControlId = 0;
  if (control) {
    control.onFrame = (frame) => {
      if (frame.type !== "response" && frame.type !== "cancel") return;
      managedInputs.handle(frame);
    };
  }
  const waitForManagedInput = async (pendingFrame: any): Promise<any> => {
    if (!control || !controlSession) throw new Error("managed control is unavailable");
    const roundId = pendingFrame.round_id;
    if (typeof roundId !== "string" || !roundId) throw new Error("managed round identity is invalid");
    const waiting = managedInputs.wait(roundId);
    try {
      await control.send({ ...pendingFrame, type: "pending", session_id: controlSession, message_id: `pending-${++nextControlId}` });
      return await waiting;
    } catch (error) {
      managedInputs.clear(roundId);
      throw error;
    }
  };
  const completeManagedInput = async (pendingFrame: any, value: any): Promise<void> => {
    if (!control || !controlSession) return;
    await control.send({
      type: "terminal",
      session_id: controlSession,
      message_id: `terminal-${++nextControlId}`,
      generation: pendingFrame.generation,
      turn_sequence: pendingFrame.turn_sequence,
      execution_id: pendingFrame.execution_id,
      logical_operation_id: pendingFrame.logical_operation_id,
      round_id: pendingFrame.round_id,
      state: value,
      ...(value === "failed" ? { error_code: "bridge_error" } : {}),
    });
  };
  const callManagedTool = async (params: any): Promise<any> => {
    let result = await request("call_tool", params);
    while (result.ok && result.result && typeof result.result === "object" && result.result.__m3_pending__) {
      const marker = result.result;
      const pendingFrame = marker.__m3_pending__;
      let delivered: any;
      try {
        delivered = await waitForManagedInput({ ...pendingFrame, generation: params.generation, turn_sequence: pendingFrame.turn_sequence ?? params.turn_sequence });
        const cancellation = managedInputs.cancellation(pendingFrame.round_id);
        if (!cancellation) throw new Error("managed cancellation state is unavailable");
        const handoff = await handoffManagedResponse(
          Promise.resolve(delivered),
          cancellation,
          () => request("continue_tool", { generation: params.generation, continuation_id: marker.continuation_id, responses: delivered.responses }),
          () => request("cancel_tool", { generation: params.generation, continuation_id: marker.continuation_id }),
        );
        managedInputs.clear(pendingFrame.round_id);
        if (handoff.cancelled) return handoff.result;
        const resumed = handoff.result;
        const hasNextRound = resumed.ok && resumed.result && typeof resumed.result === "object" && resumed.result.__m3_pending__;
        if (!hasNextRound) await completeManagedInput(pendingFrame, resumed.ok ? "delivered" : "failed");
        result = resumed;
      } catch (_) {
        managedInputs.clear(pendingFrame.round_id);
        await request("cancel_tool", { generation: params.generation, continuation_id: marker.continuation_id });
        try { await completeManagedInput(pendingFrame, "cancelled"); } catch (_) { /* socket is already closing */ }
        throw new Error("managed elicitation delivery failed");
      }
    }
    return result;
  };
  const catalog = await request("list_tools");
  if (!catalog.ok || !Array.isArray(catalog.tools)) throw new Error("M3 bridge catalog unavailable");
  const tools = catalog.tools;
  for (const descriptor of tools) {
    pi.registerTool({
      name: descriptor.name,
      label: descriptor.label ?? descriptor.name,
      description: descriptor.description ?? `MCP tool ${descriptor.server ?? ""}/${descriptor.tool ?? descriptor.name}`,
      parameters: await piSchema(descriptor.inputSchema ?? { type: "object", properties: {} }),
      execute: async (_id: string, args: unknown) => {
        const context = await request("action_context");
        if (!context.ok || typeof context.generation !== "string" || !Number.isInteger(context.turn_sequence) || context.turn_sequence < 0) throw new Error("M3 action context unavailable");
        control?.beginAction(context.generation, context.turn_sequence);
        const result = await callManagedTool({ server: descriptor.server, tool: descriptor.tool, arguments: args, generation: context.generation, turn_sequence: context.turn_sequence });
        if (!result.ok) throw new Error("MCP tool call failed");
        const payload = result.result ?? {};
        if (payload && typeof payload === "object" && Array.isArray(payload.content)) {
          if (payload.isError === true) throw new Error("MCP tool reported an error");
          return { content: piContent(payload.content), details: payload.structuredContent ?? payload.details ?? payload };
        }
        return { content: [{ type: "text", text: typeof payload === "string" ? payload : JSON.stringify(payload) }], details: payload };
      },
    });
  }
  const finalizeAction = async () => {
    const context = await request("action_context");
    if (context.ok && typeof context.generation === "string") await request("finalize_action", { generation: context.generation });
  };
  // Pi's agent_end event may be followed by a retry, compaction, or queued
  // continuation. agent_settled is the versioned action boundary: Pi emits
  // it only after the complete agent run has drained those continuations and
  // awaits extension handlers before publishing the RPC frame.
  pi.on?.("agent_settled", finalizeAction);
  pi.on?.("session_shutdown", () => { stopped = true; for (const resolve of pending.values()) resolve({ ok: false, error: "bridge stopped" }); pending.clear(); child.kill(); void control?.close(); });
}
