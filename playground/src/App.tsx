import { useCallback, useEffect, useRef, useState } from "react";
import { AnalyticsSheet } from "./analytics";
import { BusinessSheet } from "./business";
import { CartSheet } from "./cart";
import { AppBar, ChatPane, Rail, Sidebar } from "./components";
import {
  ApiError,
  addItem,
  addPrato,
  clearCart,
  getEstado,
  removeItem,
  removePrato,
  removeSalvo,
  saveNegocio,
  saveSalvo,
  salvoToCart,
  setQuantidade,
} from "./lib/api";
import { explainFailure, hermesHealth, streamTurn, type TurnMessage } from "./lib/chat";
import { PEDIR_SUGESTOES, montarPrato } from "./lib/prompts";
import { loadConversations, saveConversations } from "./lib/storage";
import { settleTools } from "./lib/tools";
import type { CarrinhoAcoes, ChatMessage, Conversation, Estado, Negocio, Online } from "./types";

function uid(): string {
  return crypto.randomUUID();
}

function titleFrom(text: string): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > 48 ? `${clean.slice(0, 45)}…` : clean;
}

function isAbort(error: unknown): boolean {
  return (error instanceof DOMException || error instanceof Error) && error.name === "AbortError";
}

function describe(error: unknown): string {
  if (error instanceof Error) {
    if (error.message === "Failed to fetch" || error.message.includes("NetworkError")) {
      return "Não consegui chegar na consultora. Confere se o make up subiu o Hermes e o playground.";
    }
    return error.message || explainFailure(0, "");
  }
  return "Não consegui falar com a consultora.";
}

export function App() {
  const initial = useRef(loadConversations()).current;
  const [conversations, setConversations] = useState<Conversation[]>(initial);
  const [activeId, setActiveId] = useState<string | null>(initial[0]?.id ?? null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [online, setOnline] = useState<Online>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [focusTick, setFocusTick] = useState(0);
  const [estado, setEstado] = useState<Estado | null>(null);
  const [painel, setPainel] = useState<"carrinho" | "negocio" | "analytics" | null>(null);
  const busy = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;

  useEffect(() => {
    const id = window.setTimeout(() => saveConversations(conversationsRef.current), 400);
    return () => window.clearTimeout(id);
  }, [conversations]);

  useEffect(() => {
    const flush = () => saveConversations(conversationsRef.current);
    window.addEventListener("pagehide", flush);
    return () => window.removeEventListener("pagehide", flush);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const tick = () => {
      void hermesHealth(controller.signal).then((ok) => {
        if (!controller.signal.aborted) setOnline(ok);
      });
    };
    tick();
    const id = window.setInterval(tick, 8000);
    return () => {
      controller.abort();
      window.clearInterval(id);
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      setEstado(await getEstado());
    } catch {
      // Sem a API do carrinho o chat segue funcionando; a barra de saldo mostra "—".
    }
  }, []);

  const active = conversations.find((conversation) => conversation.id === activeId) ?? null;
  const tools = (active?.messages ?? []).flatMap((message) => message.tools);

  // O saldo acompanha a conversa: recarrega (e carrega na abertura) quando a consultora mexe no carrinho ou no cardápio.
  const cartKey = tools
    .filter(
      (tool) =>
        (tool.name.startsWith("carrinho_") || tool.name.startsWith("cardapio_") || tool.name.startsWith("salvos_")) && tool.status !== "running",
    )
    .map((tool) => tool.id)
    .join(",");
  useEffect(() => {
    void refresh();
  }, [cartKey, refresh]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refresh]);

  function patch(conversationId: string, recipe: (conversation: Conversation) => Conversation) {
    setConversations((current) =>
      current
        .map((conversation) => (conversation.id === conversationId ? recipe(conversation) : conversation))
        .sort((a, b) => b.updatedAt - a.updatedAt),
    );
  }

  function patchMessage(conversationId: string, messageId: string, recipe: (message: ChatMessage) => ChatMessage) {
    patch(conversationId, (conversation) => ({
      ...conversation,
      updatedAt: Date.now(),
      messages: conversation.messages.map((message) => (message.id === messageId ? recipe(message) : message)),
    }));
  }

  function fill(text: string) {
    setDraft(text);
    setFocusTick((value) => value + 1);
    setMenuOpen(false);
  }

  async function send(raw: string) {
    const text = raw.trim();
    if (!text || busy.current) return;
    busy.current = true;
    setSending(true);
    setMenuOpen(false);

    const existing = conversationsRef.current.find((conversation) => conversation.id === activeId) ?? null;
    const conversationId = existing?.id ?? uid();
    const sessionAtStart = existing?.sessionId ?? null;
    const now = Date.now();
    const userMessage: ChatMessage = { id: uid(), role: "user", content: text, tools: [], at: now };
    const assistantId = uid();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      tools: [],
      pending: true,
      startedAt: now,
      at: now,
    };

    setConversations((current) => {
      const base = existing
        ? current
        : [
            {
              id: conversationId,
              title: titleFrom(text),
              sessionId: null,
              messages: [],
              updatedAt: Date.now(),
            },
            ...current,
          ];
      return base.map((conversation) =>
        conversation.id === conversationId
          ? {
              ...conversation,
              title: conversation.messages.some((message) => message.role === "user")
                ? conversation.title
                : titleFrom(text),
              updatedAt: Date.now(),
              messages: [...conversation.messages, userMessage, assistantMessage],
            }
          : conversation,
      );
    });
    setActiveId(conversationId);
    setDraft("");

    const history: TurnMessage[] = [
      ...(existing?.messages ?? [])
        .filter((message) => message.content.trim())
        .map((message) => ({ role: message.role, content: message.content })),
      { role: "user", content: text },
    ];

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamTurn({
        messages: history,
        sessionId: sessionAtStart,
        signal: controller.signal,
        onSession: (sessionId) => {
          patch(conversationId, (conversation) => ({ ...conversation, sessionId, updatedAt: Date.now() }));
        },
        onDelta: (delta) => {
          patchMessage(conversationId, assistantId, (message) => ({
            ...message,
            content: message.content + delta,
          }));
        },
        onTool: (apply) => {
          patchMessage(conversationId, assistantId, (message) => ({ ...message, tools: apply(message.tools) }));
        },
      });
      patchMessage(conversationId, assistantId, (message) => ({
        ...message,
        pending: false,
        tools: settleTools(message.tools, "done"),
        content: message.content.trim()
          ? message.content
          : "Ela mexeu nas ferramentas e não deixou o texto. Manda de novo que a conversa continua.",
      }));
    } catch (error) {
      if (isAbort(error)) {
        setConversations((current) =>
          current.map((conversation) => {
            if (conversation.id !== conversationId) return conversation;
            return {
              ...conversation,
              updatedAt: Date.now(),
              messages: conversation.messages.flatMap((message) => {
                if (message.id !== assistantId) return [message];
                if (!message.content.trim()) return [];
                return [{ ...message, pending: false, tools: settleTools(message.tools, "done") }];
              }),
            };
          }),
        );
      } else {
        const explanation = describe(error);
        patchMessage(conversationId, assistantId, (message) => ({
          ...message,
          pending: false,
          error: true,
          tools: settleTools(message.tools, "error"),
          content: message.content.trim() ? `${message.content}\n\n${explanation}` : explanation,
        }));
      }
    } finally {
      busy.current = false;
      setSending(false);
      abortRef.current = null;
      void refresh();
    }
  }

  // Cada ação da tela devolve o texto do erro (ou null) e já atualiza o saldo com o estado que a API devolveu.
  async function aplicar(acao: () => Promise<Estado>): Promise<string | null> {
    try {
      setEstado(await acao());
      return null;
    } catch (error) {
      void refresh();
      return error instanceof ApiError ? error.message : "Não consegui falar com o carrinho.";
    }
  }

  const acoes: CarrinhoAcoes = {
    addItem: (item) => aplicar(() => addItem(item)),
    addPrato: (prato) => aplicar(() => addPrato(prato)),
    setQuantidade: (id, quantidade) => aplicar(() => setQuantidade(id, quantidade)),
    removeItem: (id) => aplicar(() => removeItem(id)),
    removePrato: (nome) => aplicar(() => removePrato(nome)),
    clear: () => aplicar(() => clearCart()),
    saveSalvo: (salvo) => aplicar(() => saveSalvo(salvo)),
    removeSalvo: (id) => aplicar(() => removeSalvo(id)),
    salvoToCart: (id) => aplicar(() => salvoToCart(id)),
  };

  return (
    <div className="app">
      <AppBar onMenu={() => setMenuOpen(true)} estado={estado} onCart={() => setPainel("carrinho")} />
      <Sidebar
        open={menuOpen}
        conversations={conversations}
        activeId={activeId}
        estado={estado}
        onClose={() => setMenuOpen(false)}
        onNew={() => {
          if (busy.current) return;
          setActiveId(null);
          setDraft("");
          setMenuOpen(false);
        }}
        onSelect={(id) => {
          if (busy.current) return;
          setActiveId(id);
        }}
        onDelete={(id) => {
          if (busy.current) return;
          setConversations((current) => current.filter((conversation) => conversation.id !== id));
          if (activeId === id) setActiveId(null);
        }}
        onFill={fill}
        onCart={() => setPainel("carrinho")}
        onNegocio={() => setPainel("negocio")}
        onAnalytics={() => setPainel("analytics")}
      />
      {menuOpen ? <button className="overlay" type="button" aria-label="Fechar o menu" onClick={() => setMenuOpen(false)} /> : null}
      <ChatPane
        messages={active?.messages ?? []}
        draft={draft}
        sending={sending}
        online={online}
        focusTick={focusTick}
        onDraft={setDraft}
        onSend={(text) => {
          void send(text);
        }}
        onStop={() => abortRef.current?.abort()}
        onFill={fill}
      />
      <Rail tools={tools} pending={(active?.messages ?? []).some((message) => message.pending)} />
      {painel === "carrinho" ? (
        <CartSheet
          estado={estado}
          acoes={acoes}
          onClose={() => setPainel(null)}
          onAsk={() => {
            setPainel(null);
            fill(PEDIR_SUGESTOES);
          }}
        />
      ) : null}
      {painel === "negocio" ? (
        <BusinessSheet estado={estado} onClose={() => setPainel(null)} onSave={(negocio: Negocio) => aplicar(() => saveNegocio(negocio))} />
      ) : null}
      {painel === "analytics" ? (
        <AnalyticsSheet
          onClose={() => setPainel(null)}
          onNegocio={() => setPainel("negocio")}
          onAsk={(prato) => {
            setPainel(null);
            fill(montarPrato(prato));
          }}
        />
      ) : null}
    </div>
  );
}
