import { applyTool, parseToolEvent } from "./tools";
import type { ToolTrace } from "../types";

export type TurnMessage = {
  role: "user" | "assistant";
  content: string;
};

export const MENSAGEM_CONTEXTO_LONGO = "A conversa ficou longa. O histórico e os dados salvos continuam disponíveis. Ao retomar, a consultora usa as informações mais recentes da ficha.";
const LIMITE_HERMES = "this conversation has grown too long for";

export function contextoLongo(texto: string): boolean {
  return texto === MENSAGEM_CONTEXTO_LONGO || texto.trimStart().toLowerCase().startsWith(LIMITE_HERMES)
    || /\n\s*This conversation has grown too long for/i.test(texto);
}

export class ContextoLongoError extends Error {
  constructor() { super(MENSAGEM_CONTEXTO_LONGO); }
}

/** Leva só mensagens visíveis e recentes à sessão nova, sem importar resultados de ferramentas. */
export function historicoParaRetomar(mensagens: TurnMessage[]): TurnMessage[] {
  const anteriores = mensagens.slice(0, -1).filter(({ role, content }) => role !== "assistant" || !contextoLongo(content));
  const selecionadas: TurnMessage[] = [];
  let disponivel = 12_000;
  for (const mensagem of anteriores.slice(-12).reverse()) {
    if (disponivel < 150) break;
    const limite = Math.min(3_000, disponivel);
    const metade = Math.floor((limite - 5) / 2);
    const { content } = mensagem;
    const trecho = content.length > limite ? `${content.slice(0, metade)}\n[…]\n${content.slice(-metade)}` : content;
    selecionadas.unshift({ ...mensagem, content: trecho });
    disponivel -= trecho.length;
  }
  const preferencias = /\b(?:margem|investimento|orçamento para (?:anúncios|divulgação))\b[^.!?\n]{0,80}\d/i;
  const anterioresAoRecorte = anteriores.slice(0, anteriores.length - selecionadas.length);
  const preferenciasSalvas = anterioresAoRecorte.filter(({ role, content }) => role === "user" && content.length <= 500 && preferencias.test(content)).slice(-2);
  return [...preferenciasSalvas, ...selecionadas, ...mensagens.slice(-1)];
}

type StreamHandlers = {
  messages: TurnMessage[];
  cadastroEnviado?: "negocio" | "cozinha";
  sessionId: string | null;
  signal: AbortSignal;
  onSession: (sessionId: string) => void;
  onDelta: (text: string, substituir?: boolean) => void;
  onTool: (apply: (current: ToolTrace[]) => ToolTrace[]) => void;
};

// No Chat Completions, o Hermes manda a resposta final inteira num delta com este aviso.
const AVISO_RESPOSTA_FINAL = /(?:^|\r?\n\r?\n)\[(?:Response transformed after streaming|Resposta transformada após o streaming)\]\r?\n/g;

function respostaReescrita(texto: string, somenteInicio = false): string | null {
  const avisos = texto.matchAll(AVISO_RESPOSTA_FINAL);
  if (somenteInicio) {
    const aviso = avisos.next().value;
    return aviso?.index === 0 ? texto.slice(aviso[0].length) : null;
  }
  let inicio = 0;
  for (const aviso of avisos) inicio = aviso.index + aviso[0].length;
  return inicio ? texto.slice(inicio) : null;
}

export function respostaVisivel(texto: string): string {
  const visivel = respostaReescrita(texto) ?? texto;
  return contextoLongo(visivel) ? MENSAGEM_CONTEXTO_LONGO : visivel;
}

export function explainFailure(status: number, body: string): string {
  if (status === 401) {
    return "A cozinha não aceitou a chave local. Confere o API_SERVER_KEY no .env e sobe de novo.";
  }
  if (status === 403) {
    return "A consultora recusou este acesso. Abre o playground em http://localhost:3000.";
  }
  if (status === 409) {
    return "Há uma resposta em andamento. Aguarde a conclusão antes de enviar outro pedido.";
  }
  if (status >= 500) {
    return "A consultora está indisponível no momento. Seus dados salvos foram mantidos; tente novamente quando a conexão voltar.";
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

  const history = handlers.sessionId ? handlers.messages.slice(-1) : historicoParaRetomar(handlers.messages);
  const cadastro = handlers.cadastroEnviado ? `; salvou ${handlers.cadastroEnviado}` : "";
  const outgoing = history.map((message, index) => index === history.length - 1 && message.role === "user"
    ? { ...message, content: `${message.content}\n\n[painel da tela: cadastro guiado no chat${cadastro}]` }
    : message);
  // O Hermes deriva a sessão sem header da primeira fala e da instrução de sistema.
  // O identificador garante que a retomada textual não recaia numa sessão anterior.
  const messages = handlers.sessionId ? outgoing : [
    { role: "system", content: `Identificador desta retomada: ${crypto.randomUUID()}.` }, ...outgoing,
  ];
  const response = await fetch("/hermes/v1/chat/completions", {
    method: "POST",
    headers,
    cache: "no-store",
    signal: handlers.signal,
    body: JSON.stringify({
      model: "hermes-agent",
      stream: true,
      messages,
    }),
  });

  if (!response.ok) {
    const corpo = await response.text().catch(() => "");
    if (limiteNaFalha(corpo)) throw new ContextoLongoError();
    throw new Error(explainFailure(response.status, corpo));
  }

  const sessionId = response.headers.get("X-Hermes-Session-Id");
  if (sessionId) handlers.onSession(sessionId);
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const payload: unknown = await response.json();
    if (payload && typeof payload === "object") verificarErro(payload as Record<string, unknown>);
    const text = readFinalText(payload);
    if (contextoLongo(text)) throw new ContextoLongoError();
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
  let resposta = "";
  let prefixoPendente = "";

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

    verificarErro(record);

    const delta = readDelta(record);
    if (delta) {
      const final = respostaReescrita(delta, true);
      resposta = final ?? resposta + delta;
      if (contextoLongo(resposta)) throw new ContextoLongoError();
      if (final === null && LIMITE_HERMES.startsWith(resposta.trimStart().toLowerCase())) {
        prefixoPendente += delta;
      } else {
        handlers.onDelta(final ?? prefixoPendente + delta, final !== null);
        prefixoPendente = "";
      }
    }
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
    if (prefixoPendente) handlers.onDelta(prefixoPendente);
  } catch (error) {
    void reader.cancel().catch(() => {});
    throw error;
  } finally {
    release();
  }
}

function limiteNaFalha(texto: string): boolean {
  return texto.toLowerCase().includes(LIMITE_HERMES)
    || /\bcontext_length_exceeded\b|maximum context length|exceeds the available context size/i.test(texto);
}

function verificarErro(record: Record<string, unknown>): void {
  const error = readError(record);
  if (!error) return;
  if (limiteNaFalha(error)) throw new ContextoLongoError();
  throw new Error(error.includes("admission_conflict") ? explainFailure(409, "") : error);
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
    const { code, message } = top as Record<string, unknown>;
    if (code === "context_length_exceeded") return code;
    if (typeof message === "string" && message.trim()) return message.trim();
  }
  if (typeof top === "string" && top.trim()) return top.trim();
  return "A consultora interrompeu a resposta.";
}
