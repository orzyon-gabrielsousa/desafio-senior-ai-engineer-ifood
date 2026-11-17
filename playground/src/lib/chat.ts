import { applyTool, parseToolEvent } from "./tools";
import type { ToolTrace } from "../types";

export type TurnMessage = {
  role: "user" | "assistant";
  content: string;
};

type StreamHandlers = {
  messages: TurnMessage[];
  cadastroEnviado?: "negocio" | "cozinha";
  sessionId: string | null;
  signal: AbortSignal;
  onSession: (sessionId: string) => void;
  onDelta: (text: string) => void;
  onTool: (apply: (current: ToolTrace[]) => ToolTrace[]) => void;
};

export function explainFailure(status: number, body: string): string {
  if (status === 401) {
    return "A cozinha não aceitou a chave local. Confere o API_SERVER_KEY no .env e sobe de novo.";
  }
  if (status === 403) {
    return "A consultora recusou este acesso. Abre o playground em http://localhost:3000.";
  }
  if (status === 409) {
    return "Ela ainda está terminando a resposta anterior. Espera um instante e manda de novo.";
  }
  if (status >= 500) {
    return "A consultora ainda está acordando — o modelo demora uns 30 segundos na primeira subida. Tenta de novo já já.";
  }
  const snippet = body.replace(/\s+/g, " ").trim().slice(0, 180);
  return snippet
    ? `Não consegui falar com a consultora (${status}). ${snippet}`
    : `Não consegui falar com a consultora (${status}).`;
}

export async function hermesHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch("/hermes/health", { cache: "no-store", signal });
    return response.ok;
  } catch {
    return false;
  }
}

export async function streamTurn(handlers: StreamHandlers): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (handlers.sessionId) headers["X-Hermes-Session-Id"] = handlers.sessionId;

  const history = handlers.sessionId ? handlers.messages.slice(-1) : handlers.messages;
  const cadastro = handlers.cadastroEnviado ? `; salvou ${handlers.cadastroEnviado}` : "";
  const outgoing = history.map((message, index) => index === history.length - 1 && message.role === "user"
    ? { ...message, content: `${message.content}\n\n[painel da tela: cadastro guiado no chat${cadastro}]` }
    : message);
  const response = await fetch("/hermes/v1/chat/completions", {
    method: "POST",
    headers,
    cache: "no-store",
    signal: handlers.signal,
    body: JSON.stringify({
      model: "hermes-agent",
      stream: true,
      messages: outgoing,
    }),
  });

  const sessionId = response.headers.get("X-Hermes-Session-Id");
  if (sessionId) handlers.onSession(sessionId);

  if (!response.ok) {
    throw new Error(explainFailure(response.status, await response.text().catch(() => "")));
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const payload: unknown = await response.json();
    const text = readFinalText(payload);
    if (text) handlers.onDelta(text);
    return;
  }

  if (!response.body) throw new Error("A consultora não devolveu a resposta.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "message";
  let dataLines: string[] = [];
  let toolSeq = 0;

  const release = () => {
    try {
      reader.releaseLock();
    } catch {
      // O leitor já foi cancelado pelo aborto.
    }
  };

  const handle = (name: string, data: string) => {
    if (data === "[DONE]") return;
    let payload: unknown;
    try {
      payload = JSON.parse(data);
    } catch {
      return;
    }
    if (!payload || typeof payload !== "object") return;
    const record = payload as Record<string, unknown>;

    if (name === "hermes.tool.progress" || (!("choices" in record) && hasTool(record))) {
      toolSeq += 1;
      const tool = parseToolEvent(record, toolSeq);
      if (tool) handlers.onTool((current) => applyTool(current, tool));
      return;
    }

    const error = readError(record);
    if (error) throw new Error(error.includes("admission_conflict") ? explainFailure(409, "") : error);

    const delta = readDelta(record);
    if (delta) handlers.onDelta(delta);
  };

  const dispatch = () => {
    if (dataLines.length === 0) {
      eventName = "message";
      return;
    }
    const data = dataLines.join("\n").trim();
    const name = eventName;
    dataLines = [];
    eventName = "message";
    if (data) handle(name, data);
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line === "") dispatch();
        else if (line.startsWith(":")) continue;
        else if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
    }
    if (buffer.startsWith("data:")) dataLines.push(buffer.slice(5).trimStart());
    dispatch();
  } finally {
    release();
  }
}

function hasTool(record: Record<string, unknown>): boolean {
  return "tool" in record || "tool_name" in record || "toolCallId" in record;
}

function readDelta(record: Record<string, unknown>): string {
  const choices = record.choices;
  if (!Array.isArray(choices) || !choices[0] || typeof choices[0] !== "object") return "";
  const choice = choices[0] as Record<string, unknown>;
  const delta = choice.delta;
  if (!delta || typeof delta !== "object") return "";
  const content = (delta as Record<string, unknown>).content;
  return typeof content === "string" ? content : "";
}

function readFinalText(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const choices = (payload as Record<string, unknown>).choices;
  if (!Array.isArray(choices) || !choices[0] || typeof choices[0] !== "object") return "";
  const message = (choices[0] as Record<string, unknown>).message;
  if (!message || typeof message !== "object") return "";
  const content = (message as Record<string, unknown>).content;
  return typeof content === "string" ? content : "";
}

function readError(record: Record<string, unknown>): string | null {
  const choices = record.choices;
  const choice = Array.isArray(choices) && choices[0] && typeof choices[0] === "object" ? (choices[0] as Record<string, unknown>) : null;
  const finish = choice?.finish_reason;
  const top = record.error;
  if (finish !== "error" && !top) return null;
  if (top && typeof top === "object") {
    const message = (top as Record<string, unknown>).message;
    if (typeof message === "string" && message.trim()) return message.trim();
  }
  if (typeof top === "string" && top.trim()) return top.trim();
  return "A consultora interrompeu a resposta.";
}
