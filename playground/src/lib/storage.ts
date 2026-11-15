import type { ChatMessage, Conversation, ToolTrace } from "../types";

const KEY = "sabor-da-maria.playground.v1";
const MAX_CONVERSATIONS = 30;

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

function asConversation(value: unknown): Conversation | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<Conversation>;
  if (typeof item.id !== "string" || !Array.isArray(item.messages)) return null;
  const messages = item.messages.filter(isMessage).map((message) => ({
    id: message.id,
    role: message.role,
    content: message.content,
    tools: Array.isArray(message.tools) ? message.tools.filter(isTool) : [],
    error: message.error === true,
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

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(KEY);
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

export function saveConversations(conversations: Conversation[]): void {
  try {
    const stored = conversations
      .map((conversation) => ({
        ...conversation,
        messages: conversation.messages.map((message) => ({ ...message, pending: false })),
      }))
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, MAX_CONVERSATIONS);
    localStorage.setItem(KEY, JSON.stringify(stored));
  } catch {
    // A conversa continua na memória mesmo se o navegador recusar o armazenamento.
  }
}
