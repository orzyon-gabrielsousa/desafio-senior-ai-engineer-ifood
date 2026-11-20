import { useCallback, useEffect, useRef, useState } from "react";
import { AgentSheet } from "./agente";
import { AnalyticsSheet } from "./analytics";
import { BusinessSheet } from "./business";
import { CadastroChat, cadastroPedido, esconderPedidoCadastro, type TipoCadastro } from "./cadastro-chat";
import { CaminhoDoPrato, etapasConcluidas, proximaEtapa, type EtapaDoPrato } from "./caminho";
import { CartSheet } from "./cart";
import { AppBar, ChatPane, Rail, Sidebar } from "./components";
import { KitchenSheet } from "./cozinha";
import { ConducaoChat } from "./conducao-chat";
import { DespensaSheet } from "./despensa";
import {
  ApiError,
  addItem,
  addPrato,
  clearCart,
  getEstado,
  escolherJornada,
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
import { ContextoLongoError, contextoLongo, explainFailure, hermesHealth, streamTurn, type TurnMessage } from "./lib/chat";
import { brl } from "./lib/format";
import { mensagemParaContinuarPrato } from "./lib/pratos";
import { PEDIR_ANALISE_DIVULGACAO, PEDIR_OUTRAS_OPCOES, PEDIR_SUGESTOES } from "./lib/prompts";
import { loadConversations } from "./lib/storage";
import { sugestoesPendentes } from "./lib/sugestoes";
import { applyResults, settleTools } from "./lib/tools";
import { useAtividade } from "./lib/useAtividade";
import { useHistorico } from "./lib/useHistorico";
import type { AcaoJornada, CarrinhoAcoes, ChatMessage, Conversation, Estado, Negocio, NovaCozinha, Online, PeriodoVendas, PratoEscolhido } from "./types";

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

const LEITURAS_DA_FICHA = new Set(["web_extract", "web_search", "despensa_buscar", "despensa_listar", "perfil_cozinha_consultar"]);

export function App() {
  const [initial] = useState(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [buscaWeb, setBuscaWeb] = useState(false);
  const [etapaVisitada, setEtapaVisitada] = useState<{ etapa: EtapaDoPrato; referencia: EtapaDoPrato } | null>(null);
  const [sending, setSending] = useState(false);
  const [online, setOnline] = useState<Online>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [focusTick, setFocusTick] = useState(0);
  const [estado, setEstado] = useState<Estado | null>(null);
  const [periodoAnalytics, setPeriodoAnalytics] = useState<PeriodoVendas>("dia");
  const [abaAnalytics, setAbaAnalytics] = useState<"vendas" | "pratos">("pratos");
  const [cadastroAberto, setCadastroAberto] = useState<TipoCadastro | null>(null);
  const [cadastroDispensado, setCadastroDispensado] = useState<string | null>(null);
  const [painel, setPainel] = useState<"agente" | "despensa" | "carrinho" | "cozinha" | "pratos" | "negocio" | "analytics" | null>(null);
  const busy = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const envioGuiado = useRef<{ visivel: string; paraAgente: string } | null>(null);
  const abrirUltima = useRef(false);

  const limparTela = useCallback(() => {
    abortRef.current?.abort();
    abrirUltima.current = false;
    setActiveId(null);
    setDraft("");
    envioGuiado.current = null;
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
      return atual;
    } catch {
      // Sem a API do carrinho o chat segue funcionando; a barra de saldo mostra "—".
    }
  }, [adotarGeracao, historyGeneration]);

  const active = conversations.find((conversation) => conversation.id === activeId) ?? null;
  useEffect(() => { envioGuiado.current = null; }, [activeId]);
  useEffect(() => {
    if (activeId && !active) setActiveId(null);
  }, [activeId, active]);
  const ultima = active?.messages.at(-1);
  const envio = ultima?.role === "assistant" ? active?.messages.at(-2) : ultima;
  const convite = ultima?.role === "assistant" && !ultima.pending ? cadastroPedido(ultima.content) : null;
  // O agente oferece as perguntas no contexto da conversa. Cadastro incompleto não abre
  // formulários nem impede pesquisar: ele confere o necessário para a receita escolhida.
  const conviteNaEtapa = convite === "negocio" ? estado?.jornada?.passo === "negocio"
    : convite === "cozinha" ? estado?.jornada?.passo === "montagem" : false;
  const cadastroPendente = conviteNaEtapa && convite && convite !== envio?.cadastroEnviado && estado?.cadastro?.[`${convite}_pendente`] ? convite : null;
  const chaveCadastro = `${activeId ?? "nova"}:${ultima?.id ?? "inicio"}:${cadastroPendente ?? ""}`;
  const tipoCadastro = cadastroAberto ?? (cadastroDispensado === chaveCadastro ? null : cadastroPendente);
  const tools = (active?.messages ?? []).flatMap((message) => message.tools);
  const pending = (active?.messages ?? []).some((message) => message.pending);
  // Sugestões não gravam nada: o que ainda espera a escolha dela está na própria conversa.
  const sugestoes = pending ? [] : sugestoesPendentes(active?.messages ?? []);
  const escolhaPendente = sugestoes.length > 0 && estado?.jornada?.etapa === "pesquisa";
  const concluidas = etapasConcluidas(estado);
  const etapaSugerida = estado?.jornada?.passo ?? proximaEtapa(concluidas);
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
          ["jornada_escolher", "vendas_registrar", "negocio_salvar", "perfil_atualizar", "perfil_cozinha_salvar", "despensa_listar", "despensa_registrar_compras", "pratos_selecionar", "receita_conferir_ingredientes", "prato_calcular_cmv", "preco_sugerir"].includes(tool.name)),
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

  function fill(text: string, paraAgente?: string) {
    setDraft(text);
    envioGuiado.current = paraAgente ? { visivel: text, paraAgente } : null;
    setBuscaWeb(false);
    setFocusTick((value) => value + 1);
    setMenuOpen(false);
  }

  function pesquisarReceitas() {
    setPainel(null);
    conduzir(PEDIR_SUGESTOES, undefined, true);
  }

  function conduzir(texto: string, contexto?: string, webSearch = false, descartaSugestoes = false) {
    // Um clique é uma nova escolha explícita. O texto que a pessoa estava escrevendo
    // continua no campo para ela terminar depois da resposta da consultora.
    void send(texto, webSearch, undefined, contexto, true, descartaSugestoes);
  }

  function continuarPrato(prato: PratoEscolhido) {
    setPainel(null);
    // A pergunta já aparece no chat. Abrir a resposta não deve enviar um aceite
    // genérico nem provocar outra rodada da mesma pergunta à consultora.
    if (estado?.jornada?.prato_id === prato.id && estado.jornada.pergunta?.texto.trim()) {
      setFocusTick((value) => value + 1);
      return;
    }
    const mensagem = mensagemParaContinuarPrato(prato);
    conduzir(mensagem.visivel, mensagem.paraAgente);
  }

  function escolherPreco(pratoId: number, preco: number, cotacao: string) {
    setPainel(null);
    const valor = brl(preco).replace(/\s+/g, " ");
    const nome = estado?.pratos_escolhidos?.find((prato) => prato.id === pratoId)?.nome ?? "este prato";
    conduzir(`Quero vender «${nome}» por ${valor} por porção. Registre no cardápio.`,
      `Escolho vender o prato #${pratoId} por ${valor} por porção. Registre no cardápio. Cotação ${cotacao}.`);
  }

  async function send(raw: string, webSearch = false, cadastroEnviado?: TipoCadastro, contextoDaEscolha?: string, preservarRascunho = false,
    descartaSugestoes = false) {
    const text = raw.trim() || (webSearch ? PEDIR_SUGESTOES : "");
    if (!text || busy.current) return;
    const textoParaAgente = contextoDaEscolha ?? (envioGuiado.current?.visivel.trim() === text ? envioGuiado.current.paraAgente : text);
    envioGuiado.current = null;
    abrirUltima.current = false;
    busy.current = true;
    setSending(true);
    setMenuOpen(false);

    const existing = conversationsRef.current.find((conversation) => conversation.id === activeId) ?? null;
    const conversationId = existing?.id ?? uid();
    const respostaAnterior = existing?.messages.filter((message) => message.role === "assistant").at(-1);
    const sessionAtStart = respostaAnterior && contextoLongo(respostaAnterior.content)
      ? null : existing ? existing.sessionId : `sabor-${conversationId}`;
    const now = Date.now();
    const userMessage: ChatMessage = { id: uid(), role: "user", content: text, webSearch, cadastroEnviado, descartaSugestoes, tools: [], at: now };
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
    if (!cadastroEnviado && !preservarRascunho) {
      setDraft("");
      setBuscaWeb(false);
    }

    const history: TurnMessage[] = [...(existing?.messages ?? []), userMessage]
      .filter((message) => message.content.trim() && !message.pending && !message.error)
      .map(({ id, role, content, webSearch }) => ({
        role,
        content: role === "assistant" ? esconderPedidoCadastro(content)
          : id === userMessage.id && textoParaAgente !== text ? textoParaAgente
          : webSearch && content !== PEDIR_SUGESTOES ? `Pesquise na internet receitas para este pedido:\n\n${content}` : content,
      }))
      .filter((message) => message.content.trim());

    const controller = new AbortController();
    abortRef.current = controller;

    function enviarSessao(sessionId: string | null) {
      return streamTurn({
        messages: history,
        cadastroEnviado,
        sessionId,
        signal: controller.signal,
        onSession: (id) => {
          const conversa = conversationsRef.current.find(({ id }) => id === conversationId);
          if (conversa && conversa.sessionId !== id) {
            patch(conversationId, (conversation) => ({ ...conversation, sessionId: id, updatedAt: Date.now() }), true);
          }
        },
        onDelta: (delta, substituir) => {
          patchMessage(conversationId, assistantId, (message) => ({
            ...message,
            content: substituir ? delta : message.content + delta,
          }));
        },
        onTool: (apply) => {
          patchMessage(conversationId, assistantId, (message) => ({ ...message, tools: apply(message.tools) }));
        },
      });
    }

    try {
      try {
        await enviarSessao(sessionAtStart);
      } catch (error) {
        const alvo = contextoDaEscolha && estado?.pratos_escolhidos?.find((prato) =>
          prato.status === "selecionado" && prato.receita_id === null
          && mensagemParaContinuarPrato(prato).paraAgente === textoParaAgente);
        const tentativa = conversationsRef.current.find(({ id }) => id === conversationId)?.messages.find(({ id }) => id === assistantId);
        // A retomada de um rascunho ainda sem ficha pode repetir leituras; decisões de cadastro, preço ou compra precisam da pessoa.
        if (!(error instanceof ContextoLongoError) || !sessionAtStart || !alvo || !tentativa
          || tentativa.tools.some(({ name }) => !LEITURAS_DA_FICHA.has(name))) throw error;
        const atual = await refresh();
        controller.signal.throwIfAborted();
        const mesmoRascunho = atual?.pratos_escolhidos?.find(({ id }) => id === alvo.id);
        if (mesmoRascunho?.status !== "selecionado" || mesmoRascunho.receita_id !== null) throw error;
        patchMessage(conversationId, assistantId, (message) => ({ ...message, content: "", tools: settleTools(message.tools, "error") }));
        await enviarSessao(null);
      }
      patchMessage(conversationId, assistantId, (message) => ({
        ...message,
        pending: false,
        tools: settleTools(message.tools, "done"),
        content: message.content.trim()
          ? message.content
          : "Não recebi o texto final. Confira seus pratos para continuar de onde parou; os dados já salvos foram mantidos.",
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
                return [{ ...message, pending: false, tools: settleTools(message.tools, "done"),
                  content: message.content.trim() ? message.content : "Interrompi a espera pela resposta. Confira seus pratos salvos para continuar de onde parou." }];
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
          content: message.content.trim() && !(error instanceof ContextoLongoError) ? `${message.content}\n\n${explanation}` : explanation,
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
    void send(texto, false, tipo,
      `${texto} As respostas enviadas já foram salvas. Continue conduzindo minha jornada a partir dos dados atuais. ` +
      "Apresente só a próxima decisão da etapa atual. Depois do negócio, mostre o estoque e espere a confirmação de que estou pronta. Não pesquise receitas antes de eu escolher sua ajuda. Na montagem, pergunte apenas o requisito necessário à ficha, sem reabrir todo o catálogo da cozinha.");
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

  async function escolherEtapa(acao: AcaoJornada, visivel: string, mensagem?: string, descartaSugestoes = false): Promise<string | null> {
    if (busy.current) return "Aguarde a resposta da consultora para continuar.";
    const geracao = historyGeneration.current;
    busy.current = true;
    try {
      const atualizado = await escolherJornada(acao);
      if (geracao !== historyGeneration.current) return null;
      setEstado(atualizado);
      setEtapaVisitada(null);
    } catch (error) {
      void refresh();
      return error instanceof ApiError ? error.message : "Não consegui salvar sua escolha. Tente de novo.";
    } finally {
      busy.current = false;
    }
    // Pausar e retomar são controles da pessoa. Não geram pesquisa nem uma
    // resposta da LLM: a pergunta atual permanece disponível até ela decidir.
    if (acao !== "pausar" && acao !== "retomar" && mensagem?.trim()) {
      void send(visivel, false, undefined, mensagem, true, descartaSugestoes).then(() => {
        // Quem traz o próprio prato responde escrevendo a ideia ou os ingredientes: o campo já fica pronto.
        if (acao === "prato_proprio") setFocusTick((value) => value + 1);
      });
    }
    return null;
  }

  function abrirEtapa(id: EtapaDoPrato) {
    setPainel(null);
    if (id === "divulgacao" && estado?.jornada?.plano_divulgacao) {
      requestAnimationFrame(() => document.getElementById("divulgacao-titulo")?.scrollIntoView({ block: "nearest" }));
      return;
    }
    if (id === "montagem" && !escolhaPendente && estado?.jornada?.etapa === "pendencias") {
      const ficha = estado.pratos_escolhidos?.find(({ id: pratoId }) => pratoId === estado.jornada?.prato_id);
      if (ficha && (estado.jornada.pergunta_esgotada || ficha.bloqueios?.[0]?.tipo === "tempo_insuficiente")) {
        continuarPrato(ficha);
        return;
      }
    }
    const idsAtivos = estado?.jornada?.pratos_ativos;
    const temPratoAtual = !estado?.jornada?.controle?.proprio_pendente && estado?.pratos_escolhidos?.some(({ id: pratoId }) => idsAtivos === undefined || idsAtivos.includes(pratoId));
    const montagem = temPratoAtual
      ? "Vamos continuar a montagem do prato atual. Consulte as fichas salvas e conduza somente a próxima decisão necessária, sem aprovar nem escolher o preço por mim."
      : estado?.jornada?.controle?.ajuda === "ia"
      ? "Quero sua ajuda. Pesquise receitas reais para o meu negócio com o estoque disponível. Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha."
      : estado?.jornada?.controle?.ajuda === "proprio"
      ? "Quero montar meu próprio prato. Pergunte qual prato ou ingrediente tenho em mente antes de criar a ficha."
      : "Apresente só as opções de ajuda para montar o prato e espere minha escolha antes de pesquisar ou criar uma ficha.";
    const pedidos: Record<EtapaDoPrato, string> = {
      negocio: "Vamos revisar meu negócio. Use os dados salvos e pergunte só a próxima informação necessária sobre tipo de negócio, local e objetivo.",
      estoque: "Mostre os ingredientes e custos que já tenho no estoque, sem lançar nova compra. Espere eu confirmar que estou pronta antes de oferecer a próxima etapa.",
      ajuda: "Apresente só as opções de ajuda para montar o prato: ajuda da IA com receitas reais ou montar minha própria ideia. Espere minha escolha antes de pesquisar.",
      montagem,
      divulgacao: estado?.jornada?.etapa === "divulgacao" && estado.jornada.controle?.divulgacao === "interesse"
        ? PEDIR_ANALISE_DIVULGACAO
        : "Vamos revisar os pratos montados, seus custos e cenários de preço com a taxa fixa de 10%. Depois que eu escolher os preços, ofereça a opção de conhecer a publicidade, sem contratar nem gastar nada.",
    };
    // Navegar por uma etapa futura não confirma as decisões que faltam.
    // Com opções já na tela, "pesquisar" é pedir outras: as mostradas ficam de fora da busca e dos botões.
    if (id === "montagem" && escolhaPendente) conduzir(PEDIR_OUTRAS_OPCOES, undefined, false, true);
    else conduzir(pedidos[id]);
  }

  const caminho = (
    <CaminhoDoPrato
      etapa={etapa}
      concluidas={concluidas}
      onEtapa={(id) => setEtapaVisitada({ etapa: id, referencia: etapaSugerida })}
      onAcao={abrirEtapa}
      estado={estado}
      etapaAtual={etapaSugerida}
      escolhaPendente={escolhaPendente}
    />
  );

  function abrirAnalytics(periodo?: PeriodoVendas) {
    setAbaAnalytics(periodo ? "vendas" : "pratos");
    if (periodo) setPeriodoAnalytics(periodo);
    setPainel("analytics");
  }

  return (
    <div className="app">
      <AppBar onMenu={() => setMenuOpen(true)} estado={estado} onCart={() => setPainel("carrinho")}
        onVendas={abrirAnalytics}
        onPratos={() => setPainel("pratos")}
        onNegocio={() => setPainel("negocio")} />
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
        onAnalytics={() => abrirAnalytics()}
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
        conducao={!tipoCadastro ? <ConducaoChat estado={estado} bloqueado={sending || pending} versao={versaoAnalises}
          sugestoes={sugestoes}
          onCadastro={abrirOnboarding}
          onAcao={escolherEtapa}
          onAnalytics={() => abrirAnalytics()}
          onEnviar={(visivel, paraAgente, descartaSugestoes) => conduzir(visivel, paraAgente, false, descartaSugestoes)} /> : null}
        focusTick={focusTick}
        webSearch={buscaWeb}
        onToggleWebSearch={() => setBuscaWeb((value) => !value)}
        compactProgress={caminho}
        onDraft={(text) => { setDraft(text); envioGuiado.current = null; }}
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
          abaInicial={abaAnalytics}
          periodoInicial={periodoAnalytics}
          onVendasAtualizadas={refresh}
          pratos={estado?.pratos_escolhidos}
          onRevisarPrato={(pratoId, nome) => {
            setPainel(null);
            const texto = `Quero revisar a ficha de «${nome}». Confira o que já está salvo e me mostre só o próximo passo necessário.`;
            conduzir(texto, `Quero continuar o prato escolhido #${pratoId} (${nome}). ${texto}`);
          }}
          onClose={() => setPainel(null)}
          onPesquisar={pesquisarReceitas}
          onEscolherPreco={escolherPreco}
          onTaxasSalvas={refresh}
        />
      ) : null}
    </div>
  );
}
