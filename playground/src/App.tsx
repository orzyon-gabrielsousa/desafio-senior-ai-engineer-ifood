import { useCallback, useEffect, useRef, useState } from "react";
import { AgentSheet } from "./agente";
import { AnalyticsSheet } from "./analytics";
import { BusinessSheet } from "./business";
import { CadastroChat, cadastroPedido, esconderPedidoCadastro, type TipoCadastro } from "./cadastro-chat";
import { CaminhoDoPrato, etapasConcluidas, proximaEtapa, type EtapaDoPrato } from "./caminho";
import { CartSheet } from "./cart";
import { AppBar, ChatPane, Rail, Sidebar } from "./components";
import { KitchenSheet } from "./cozinha";
import { DespensaSheet } from "./despensa";
import {
  ApiError,
  addItem,
  addPrato,
  clearCart,
  getEstado,
  removeItem,
  removePrato,
  removeSalvo,
  saveCozinha,
  saveNegocio,
  saveSalvo,
  salvoToCart,
  setQuantidade,
  updateNegocio,
} from "./lib/api";
import { explainFailure, hermesHealth, streamTurn, type TurnMessage } from "./lib/chat";
import { brl } from "./lib/format";
import { PEDIR_SUGESTOES } from "./lib/prompts";
import { loadConversations } from "./lib/storage";
import { applyResults, settleTools } from "./lib/tools";
import { useAtividade } from "./lib/useAtividade";
import { useHistorico } from "./lib/useHistorico";
import type { CarrinhoAcoes, ChatMessage, Conversation, Estado, Negocio, NovaCozinha, Online, PratoEscolhido } from "./types";

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
      return "Não consegui chegar na consultora. Confere se o make run está rodando (Hermes e playground no ar).";
    }
    return error.message || explainFailure(0, "");
  }
  return "Não consegui falar com a consultora.";
}

export function App() {
  const [initial] = useState(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(initial[0]?.id ?? null);
  const [draft, setDraft] = useState("");
  const [buscaWeb, setBuscaWeb] = useState(false);
  const [etapaVisitada, setEtapaVisitada] = useState<{ etapa: EtapaDoPrato; referencia: EtapaDoPrato } | null>(null);
  const [sending, setSending] = useState(false);
  const [online, setOnline] = useState<Online>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [focusTick, setFocusTick] = useState(0);
  const [estado, setEstado] = useState<Estado | null>(null);
  const [cadastroAberto, setCadastroAberto] = useState<TipoCadastro | null>(null);
  const [cadastroDispensado, setCadastroDispensado] = useState<string | null>(null);
  const [painel, setPainel] = useState<"agente" | "despensa" | "carrinho" | "cozinha" | "pratos" | "negocio" | "analytics" | null>(null);
  const busy = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const abrirUltima = useRef(true);

  const limparTela = useCallback(() => {
    abortRef.current?.abort();
    abrirUltima.current = true;
    setActiveId(null);
    setDraft("");
    setCadastroAberto(null);
    setCadastroDispensado(null);
    setBuscaWeb(false);
    setEtapaVisitada(null);
  }, []);

  const abrirHistorico = useCallback((lista: Conversation[]) => {
    if (abrirUltima.current) setActiveId(lista[0]?.id ?? null);
    abrirUltima.current = false;
  }, []);

  const {
    conversas: conversations, atuais: conversationsRef, geracao: historyGeneration,
    status: estadoHistorico, adotarGeracao, editar: editarConversas, apagar: apagarDoHistorico,
  } = useHistorico(initial, limparTela, abrirHistorico);

  // O streaming diz que a ferramenta rodou; o plugin guarda o que ela fez.
  useAtividade(conversations, (achados) => {
    const antes = conversationsRef.current;
    const depois = applyResults(antes, achados);
    const concluidas = depois.filter((conversa, i) => conversa !== antes[i] && !conversa.messages.some((message) => message.pending));
    editarConversas(() => depois, concluidas.map(({ id }) => id));
  });

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
    const antes = historyGeneration.current;
    try {
      const atual = await getEstado();
      if (antes !== historyGeneration.current && atual.geracao_dados !== historyGeneration.current) return;
      if (atual.geracao_dados !== undefined) adotarGeracao(atual.geracao_dados);
      setEstado(atual);
    } catch {
      // Sem a API do carrinho o chat segue funcionando; a barra de saldo mostra "—".
    }
  }, [adotarGeracao, historyGeneration]);

  const active = conversations.find((conversation) => conversation.id === activeId) ?? null;
  useEffect(() => {
    if (activeId && !active) setActiveId(null);
  }, [activeId, active]);
  const ultima = active?.messages.at(-1);
  const proximoCadastro = estado?.cadastro?.negocio_pendente ? "negocio" : estado?.cadastro?.cozinha_pendente ? "cozinha" : null;
  const envio = ultima?.role === "assistant" ? active?.messages.at(-2) : ultima;
  const continuarCozinha = envio?.cadastroEnviado === "negocio" && proximoCadastro === "cozinha";
  const convite = ultima?.role === "assistant" && !ultima.pending ? cadastroPedido(ultima.content) : null;
  const cadastroPendente = !ultima || continuarCozinha ? proximoCadastro
    : convite && convite !== envio?.cadastroEnviado && estado?.cadastro?.[`${convite}_pendente`] ? convite : null;
  const chaveCadastro = `${activeId ?? "nova"}:${(continuarCozinha ? envio : ultima)?.id ?? "inicio"}:${cadastroPendente ?? ""}`;
  const tipoCadastro = cadastroAberto ?? (cadastroDispensado === chaveCadastro ? null : cadastroPendente);
  const tools = (active?.messages ?? []).flatMap((message) => message.tools);
  const pending = (active?.messages ?? []).some((message) => message.pending);
  const concluidas = etapasConcluidas(estado);
  const etapaSugerida = proximaEtapa(concluidas);
  const etapa = etapaVisitada?.referencia === etapaSugerida ? etapaVisitada.etapa : etapaSugerida;
  useEffect(() => setEtapaVisitada(null), [etapaSugerida]);
  const versaoAnalises = `${activeId ?? ""}:${tools
    .filter((tool) =>
      tool.status !== "running" &&
      (tool.name === "preco_sugerir" || tool.name === "cardapio_registrar" || tool.name === "cardapio_remover"),
    )
    .map((tool) => tool.id)
    .join(",")}:${sending}`;

  // Atualiza o estado assim que uma ferramenta grava escolhas, custos ou compras.
  const estadoKey = tools
    .filter(
      (tool) =>
        tool.status !== "running" &&
        (tool.name.startsWith("carrinho_") || tool.name.startsWith("cardapio_") || tool.name.startsWith("salvos_") ||
          ["negocio_salvar", "perfil_atualizar", "despensa_listar", "despensa_registrar_compras", "pratos_selecionar", "receita_conferir_ingredientes", "prato_calcular_cmv"].includes(tool.name)),
    )
    .map((tool) => tool.id)
    .join(",");
  useEffect(() => {
    void refresh();
  }, [estadoKey, refresh]);

  useEffect(() => {
    if (painel === "carrinho" || painel === "cozinha" || painel === "pratos" || painel === "negocio") void refresh();
  }, [painel, refresh]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refresh]);

  function patch(conversationId: string, recipe: (conversation: Conversation) => Conversation, gravar = false) {
    editarConversas((current) =>
      current
        .map((conversation) => (conversation.id === conversationId ? recipe(conversation) : conversation))
        .sort((a, b) => b.updatedAt - a.updatedAt), gravar ? conversationId : undefined,
    );
  }

  function patchMessage(conversationId: string, messageId: string, recipe: (message: ChatMessage) => ChatMessage, gravar = false) {
    patch(conversationId, (conversation) => ({
      ...conversation,
      updatedAt: Date.now(),
      messages: conversation.messages.map((message) => (message.id === messageId ? recipe(message) : message)),
    }), gravar);
  }

  function fill(text: string) {
    setDraft(text);
    setBuscaWeb(false);
    setFocusTick((value) => value + 1);
    setMenuOpen(false);
  }

  function pesquisarReceitas() {
    setPainel(null);
    setBuscaWeb(true);
    setFocusTick((value) => value + 1);
  }

  function continuarPrato(prato: PratoEscolhido) {
    setPainel(null);
    fill(`Quero continuar o prato escolhido #${prato.id} (${prato.nome}).`);
  }

  function escolherPreco(pratoId: number, preco: number, cotacao: string) {
    setPainel(null);
    const valor = brl(preco).replace(/\s+/g, " ");
    fill(`Escolho vender o prato #${pratoId} por ${valor} por porção. Registre no cardápio. Cotação ${cotacao}.`);
  }

  async function send(raw: string, webSearch = false, cadastroEnviado?: TipoCadastro) {
    const text = raw.trim() || (webSearch ? PEDIR_SUGESTOES : "");
    if (!text || busy.current) return;
    abrirUltima.current = false;
    busy.current = true;
    setSending(true);
    setMenuOpen(false);

    const existing = conversationsRef.current.find((conversation) => conversation.id === activeId) ?? null;
    const conversationId = existing?.id ?? uid();
    const sessionAtStart = existing ? existing.sessionId : `sabor-${conversationId}`;
    const now = Date.now();
    const userMessage: ChatMessage = { id: uid(), role: "user", content: text, webSearch, cadastroEnviado, tools: [], at: now };
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

    editarConversas((current) => {
      const base = existing
        ? current
        : [
            {
              id: conversationId,
              title: titleFrom(text),
              sessionId: sessionAtStart,
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
    }, conversationId);
    setActiveId(conversationId);
    if (!cadastroEnviado) {
      setDraft("");
      setBuscaWeb(false);
    }

    const history: TurnMessage[] = [...(existing?.messages ?? []), userMessage]
      .filter((message) => message.content.trim())
      .map(({ role, content, webSearch }) => ({
        role,
        content: role === "assistant" ? esconderPedidoCadastro(content)
          : webSearch && content !== PEDIR_SUGESTOES ? `Pesquise na internet receitas para este pedido:\n\n${content}` : content,
      }))
      .filter((message) => message.content.trim());

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamTurn({
        messages: history,
        cadastroEnviado,
        sessionId: sessionAtStart,
        signal: controller.signal,
        onSession: (sessionId) => {
          if (sessionId !== sessionAtStart) {
            patch(conversationId, (conversation) => ({ ...conversation, sessionId, updatedAt: Date.now() }), true);
          }
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
      }), true);
    } catch (error) {
      if (isAbort(error)) {
        editarConversas((current) =>
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
          }), conversationId,
        );
      } else {
        const explanation = describe(error);
        patchMessage(conversationId, assistantId, (message) => ({
          ...message,
          pending: false,
          error: true,
          tools: settleTools(message.tools, "error"),
          content: message.content.trim() ? `${message.content}\n\n${explanation}` : explanation,
        }), true);
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

  function abrirOnboarding(tipo: TipoCadastro) {
    setPainel(null);
    setMenuOpen(false);
    setCadastroAberto(tipo);
  }

  function fecharCadastro() {
    setCadastroAberto(null);
    setCadastroDispensado(chaveCadastro);
  }

  async function enviarCadastro(tipo: TipoCadastro, salvar: () => Promise<Estado>): Promise<string | null> {
    if (busy.current) return "Aguarde a resposta da consultora para enviar suas escolhas.";
    busy.current = true;
    const geracao = historyGeneration.current;
    let atualizado: Estado;
    try {
      atualizado = await salvar();
    } catch (error) {
      return error instanceof ApiError && error.status > 0 ? error.message : "Não consegui salvar suas respostas. Tente enviar de novo.";
    } finally {
      busy.current = false;
    }
    if (geracao !== historyGeneration.current) return null;
    setEstado(atualizado);
    fecharCadastro();
    const texto = tipo === "negocio" ? "Enviei meu cadastro do negócio pelo chat." : "Enviei meu cadastro da cozinha pelo chat.";
    void send(texto, false, tipo);
    return null;
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

  function abrirEtapa(id: EtapaDoPrato) {
    switch (id) {
      case "negocio":
        if (concluidas.negocio) setPainel("negocio");
        else abrirOnboarding("negocio");
        break;
      case "despensa":
        setPainel("despensa");
        break;
      case "receitas":
        pesquisarReceitas();
        break;
      case "cozinha":
        if (concluidas.cozinha) setPainel("cozinha");
        else abrirOnboarding("cozinha");
        break;
      case "ingredientes":
      case "cardapio":
        setPainel("pratos");
        break;
      case "carrinho":
        setPainel("carrinho");
        break;
      case "preco":
        setPainel("analytics");
        break;
    }
  }

  const caminho = (
    <CaminhoDoPrato
      etapa={etapa}
      concluidas={concluidas}
      onEtapa={(id) => setEtapaVisitada({ etapa: id, referencia: etapaSugerida })}
      onAcao={abrirEtapa}
    />
  );

  return (
    <div className="app">
      <AppBar onMenu={() => setMenuOpen(true)} estado={estado} onCart={() => setPainel("carrinho")} />
      <Sidebar
        open={menuOpen}
        conversations={conversations}
        estadoHistorico={estadoHistorico}
        activeId={activeId}
        estado={estado}
        onClose={() => setMenuOpen(false)}
        onNew={() => {
          if (busy.current) return;
          abrirUltima.current = false;
          setActiveId(null);
          setDraft("");
          setCadastroAberto(null);
          setCadastroDispensado(null);
          setBuscaWeb(false);
          setMenuOpen(false);
        }}
        onSelect={(id) => {
          if (busy.current) return;
          abrirUltima.current = false;
          setActiveId(id);
          setCadastroAberto(null);
          setBuscaWeb(false);
        }}
        onDelete={(id) => {
          if (busy.current) return;
          abrirUltima.current = false;
          apagarDoHistorico(id);
          if (activeId === id) {
            setActiveId(null);
            setCadastroAberto(null);
            setBuscaWeb(false);
          }
        }}
        onAgent={() => setPainel("agente")}
        onCart={() => setPainel("carrinho")}
        onCozinha={() => {
          if (estado?.pratos_escolhidos?.length) setPainel("pratos");
          else if (estado?.cadastro?.cozinha_pendente) abrirOnboarding("cozinha");
          else setPainel("cozinha");
        }}
        onNegocio={() => {
          if (estado?.cadastro?.negocio_pendente) abrirOnboarding("negocio");
          else setPainel("negocio");
        }}
        onAnalytics={() => setPainel("analytics")}
      />
      {menuOpen ? <button className="overlay" type="button" aria-label="Fechar o menu" onClick={() => setMenuOpen(false)} /> : null}
      <ChatPane
        messages={active?.messages ?? []}
        draft={draft}
        sending={sending}
        online={online}
        onboarding={estado && tipoCadastro ? (
          <CadastroChat key={`${activeId ?? "nova"}:${tipoCadastro}`} tipo={tipoCadastro} estado={estado} bloqueado={sending}
            onClose={fecharCadastro}
            onCozinha={(cozinha) => enviarCadastro("cozinha", () => saveCozinha(cozinha))}
            onNegocio={(campos) => enviarCadastro("negocio", () => updateNegocio(campos))} />
        ) : null}
        focusTick={focusTick}
        webSearch={buscaWeb}
        onToggleWebSearch={() => setBuscaWeb((value) => !value)}
        compactProgress={caminho}
        onDraft={setDraft}
        onSend={(text) => {
          void send(text, buscaWeb);
        }}
        onStop={() => abortRef.current?.abort()}
        onFill={fill}
      />
      <Rail tools={tools} pending={pending} caminho={caminho} />
      {painel === "agente" ? <AgentSheet online={online} tools={tools} pending={pending}
        estadoHistorico={estadoHistorico} conversaAberta={!!active} onClose={() => setPainel(null)} /> : null}
      {painel === "despensa" ? <DespensaSheet onEstado={setEstado} onClose={() => setPainel(null)} /> : null}
      {painel === "carrinho" ? (
        <CartSheet
          estado={estado}
          acoes={acoes}
          onCozinha={() => setPainel("cozinha")}
          onPratos={() => setPainel("pratos")}
          onClose={() => setPainel(null)}
          onAsk={pesquisarReceitas}
        />
      ) : null}
      {painel === "cozinha" || painel === "pratos" ? (
        <KitchenSheet
          estado={estado}
          abaInicial={painel === "pratos" ? "pratos" : "cozinha"}
          onClose={() => setPainel(null)}
          onSave={(cozinha: NovaCozinha) => aplicar(() => saveCozinha(cozinha))}
          onContinuar={continuarPrato}
          onPesquisar={pesquisarReceitas}
        />
      ) : null}
      {painel === "negocio" ? (
        <BusinessSheet estado={estado} onClose={() => setPainel(null)} onSave={(negocio: Negocio) => aplicar(() => saveNegocio(negocio))} />
      ) : null}
      {painel === "analytics" ? (
        <AnalyticsSheet
          versao={versaoAnalises}
          onClose={() => setPainel(null)}
          onPesquisar={pesquisarReceitas}
          onEscolherPreco={escolherPreco}
          onTaxasSalvas={refresh}
        />
      ) : null}
    </div>
  );
}
