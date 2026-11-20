import type { ChatMessage, Conversation, ToolTrace } from "../types";
import { contextoLongo, respostaVisivel } from "./chat";

const KEY = "sabor-da-maria.playground.v1";
const QUEUE_PREFIX = `${KEY}.pendente.`;
const MIGRATION_KEY = `${KEY}.migrado`;
export const HISTORY_GENERATION_KEY = "sabor-da-maria.playground.geracao";
export const MAX_CONVERSATIONS = 30;

export type HistoryChange = { kind: "put"; conversa: Conversation } | { kind: "delete"; id: string; checkpoint?: Conversation };

function historyKey(generation: string | null): string {
  return generation ? `${KEY}.${generation}` : KEY;
}

export function loadHistoryGeneration(): string | null {
  try {
    return localStorage.getItem(HISTORY_GENERATION_KEY);
  } catch {
    return null;
  }
}

export function setHistoryGeneration(generation: string | null): void {
  try {
    const old = loadHistoryGeneration();
    if (old === generation) return;
    localStorage.removeItem(historyKey(old));
    localStorage.removeItem(KEY);
    for (const key of pendingKeys()) localStorage.removeItem(key);
    localStorage.removeItem(MIGRATION_KEY);
    if (generation === null) localStorage.removeItem(HISTORY_GENERATION_KEY);
    else localStorage.setItem(HISTORY_GENERATION_KEY, generation);
  } catch {
    // A conversa atual continua disponível em memória.
  }
}

function isTool(value: unknown): value is ToolTrace {
  if (!value || typeof value !== "object") return false;
  const tool = value as Partial<ToolTrace>;
  return typeof tool.id === "string" && typeof tool.name === "string" && typeof tool.label === "string";
}

function isMessage(value: unknown): value is ChatMessage {
  if (!value || typeof value !== "object") return false;
  const message = value as Partial<ChatMessage>;
  return (
    typeof message.id === "string" &&
    (message.role === "user" || message.role === "assistant") &&
    typeof message.content === "string"
  );
}

export function asConversation(value: unknown): Conversation | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<Conversation>;
  if (typeof item.id !== "string" || !Array.isArray(item.messages)) return null;
  const messages = item.messages.filter(isMessage).map((message) => ({
    id: message.id,
    role: message.role,
    content: message.role === "assistant" ? respostaVisivel(message.content) : message.content,
    webSearch: message.webSearch === true,
    cadastroEnviado: message.cadastroEnviado === "negocio" || message.cadastroEnviado === "cozinha"
      ? message.cadastroEnviado : undefined,
    descartaSugestoes: message.descartaSugestoes === true,
    // Passo que estava rodando quando a página fechou foi interrompido: não fica girando para sempre.
    tools: Array.isArray(message.tools)
      ? message.tools.filter(isTool).map((tool) => (tool.status === "running" ? { ...tool, status: "error" as const } : tool))
      : [],
    error: message.error === true || (message.role === "assistant" && contextoLongo(message.content)),
    pending: false,
    at: typeof message.at === "number" ? message.at : undefined,
  }));
  return {
    id: item.id,
    title: typeof item.title === "string" && item.title.trim() ? item.title : "Conversa",
    sessionId: typeof item.sessionId === "string" ? item.sessionId : null,
    messages,
    updatedAt: typeof item.updatedAt === "number" ? item.updatedAt : Date.now(),
  };
}

export function historyChangedId(change: HistoryChange): string {
  return change.kind === "put" ? change.conversa.id : change.id;
}

function pendingKeys(): string[] {
  const keys: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith(QUEUE_PREFIX)) keys.push(key);
  }
  return keys;
}

export function loadHistoryQueue(generation: string | null): HistoryChange[] {
  try {
    return pendingKeys().flatMap((key) => {
      const pending = JSON.parse(localStorage.getItem(key) ?? "null");
      if (pending?.generation !== generation) return [];
      const change: Partial<HistoryChange> | null = pending.change;
      if (change?.kind === "delete" && typeof change.id === "string") return [change] as HistoryChange[];
      if (change?.kind !== "put") return [];
      return asConversation(change.conversa) ? [change] as HistoryChange[] : [];
    });
  } catch {
    return [];
  }
}

export function saveHistoryChange(generation: string | null, change: HistoryChange): void {
  try {
    if (loadHistoryGeneration() !== generation) return;
    localStorage.setItem(QUEUE_PREFIX + historyChangedId(change), JSON.stringify({ generation, change }));
  } catch {
    // A fila ainda pode ser enviada enquanto esta aba estiver aberta.
  }
}

export function clearHistoryChange(generation: string | null, change: HistoryChange): void {
  try {
    if (loadHistoryGeneration() !== generation) return;
    const key = QUEUE_PREFIX + historyChangedId(change);
    if (localStorage.getItem(key) === JSON.stringify({ generation, change })) localStorage.removeItem(key);
  } catch {
    // A repetição é idempotente quando o armazenamento voltar.
  }
}

export function historyMigrated(generation: string | null): boolean {
  try {
    return localStorage.getItem(MIGRATION_KEY) === JSON.stringify(generation);
  } catch {
    return false;
  }
}

export function markHistoryMigrated(generation: string | null): void {
  try {
    if (loadHistoryGeneration() === generation) localStorage.setItem(MIGRATION_KEY, JSON.stringify(generation));
  } catch {
    // Uma nova tentativa de migração usa os mesmos IDs.
  }
}

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(historyKey(loadHistoryGeneration()));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(asConversation)
      .filter((item): item is Conversation => item !== null)
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, MAX_CONVERSATIONS);
  } catch {
    return [];
  }
}

export function saveConversations(conversations: Conversation[], generation: string | null = loadHistoryGeneration()): void {
  try {
    if (loadHistoryGeneration() !== generation) return;
    const stored = conversations
      .map((conversation) => ({
        ...conversation,
        messages: conversation.messages.filter((message) => !message.pending),
      }))
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, MAX_CONVERSATIONS);
    localStorage.setItem(historyKey(generation), JSON.stringify(stored));
  } catch {
    // A conversa continua na memória mesmo se o navegador recusar o armazenamento.
  }
}
