// Private MCP Pal Pi extension. It speaks a bounded JSONL protocol to the
// package-owned Python bridge; no Pi-global configuration is modified.
import { spawn } from "node:child_process";

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
  const command = process.env.MCP_PAL_PI_BRIDGE_COMMAND;
  if (!command) return;
  const argv = JSON.parse(process.env.MCP_PAL_PI_BRIDGE_ARGV ?? "[]") as string[];
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
  const catalog = await request("list_tools");
  if (!catalog.ok || !Array.isArray(catalog.tools)) throw new Error("MCP Pal bridge catalog unavailable");
  const tools = catalog.tools;
  for (const descriptor of tools) {
    pi.registerTool({
      name: descriptor.name,
      label: descriptor.label ?? descriptor.name,
      description: descriptor.description ?? `MCP tool ${descriptor.server ?? ""}/${descriptor.tool ?? descriptor.name}`,
      parameters: await piSchema(descriptor.inputSchema ?? { type: "object", properties: {} }),
      execute: async (_id: string, args: unknown) => {
        const result = await request("call_tool", { server: descriptor.server, tool: descriptor.tool, arguments: args });
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
  pi.on?.("session_shutdown", () => { stopped = true; for (const resolve of pending.values()) resolve({ ok: false, error: "bridge stopped" }); pending.clear(); child.kill(); });
}
