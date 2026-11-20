import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Avatar, IfoodLogo, Mark } from "./brand";
import { esconderPedidoCadastro } from "./cadastro-chat";
import { AgentIcon, AlertIcon, CartIcon, ChartIcon, ChatIcon, CheckIcon, CloseIcon, GlobeIcon, KitchenIcon, MenuIcon, PlusIcon, SendIcon, StopIcon, StoreIcon } from "./icons";
import { brl, duracao } from "./lib/format";
import { contarItens, localCurto } from "./lib/loja";
import { Markdown } from "./lib/markdown";
import { WELCOME } from "./lib/prompts";
import { describeStep } from "./lib/tools";
import type { StepView } from "./lib/tools";
import type { EstadoHistorico } from "./lib/useHistorico";
import type { ChatMessage, Conversation, Estado, Online, PeriodoVendas, ResumoVendas, ToolTrace } from "./types";

const CLOCK = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" });

function textoDecorrido(since: number, now: number): string {
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

function ferramentaEmAndamento(tools: ToolTrace[]): ToolTrace | undefined {
  for (let index = tools.length - 1; index >= 0; index -= 1) if (tools[index].status === "running") return tools[index];
  return undefined;
}

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return <span className="elapsed">{textoDecorrido(since, now)}</span>;
}

function BudgetBar({ estado, onOpen }: { estado: Estado | null; onOpen: () => void }) {
  const o = estado?.orcamento;
  const despesas = o?.despesas ?? (o ? o.comprometido + o.carrinho : 0);
  const saldo = o?.saldo_gerencial ?? o?.saldo ?? 0;
  const tom = !o ? "idle" : saldo <= 0 ? "none" : saldo / (o.inicial || 1) < 0.25 ? "low" : "ok";
  return (
    <button
      type="button"
      className={`budget ${tom}`}
      onClick={onOpen}
      aria-label={o ? `Abrir o carrinho. Orçamento ${brl(o.inicial)}, despesas ${brl(despesas)} e saldo ${brl(saldo)}.` : "Abrir o carrinho"}
    >
      <span className="budget-stat">
        <small>Orçamento</small>
        <strong>{o ? brl(o.inicial) : "—"}</strong>
      </span>
      <span className="budget-stat">
        <small>Despesas</small>
        <strong>{o ? brl(despesas) : "—"}</strong>
      </span>
      <span className="budget-stat saldo">
        <small>Saldo</small>
        <strong>{o ? brl(saldo) : "—"}</strong>
      </span>
      <span className="budget-cart">
        <CartIcon />
        <b>{estado ? contarItens(estado.carrinho) : 0}</b>
      </span>
    </button>
  );
}

const PERIODOS_VENDAS: { id: PeriodoVendas; rotulo: string; descricao: string }[] = [
  { id: "dia", rotulo: "Vendas diárias", descricao: "hoje" },
  { id: "semana", rotulo: "Vendas semanais", descricao: "esta semana" },
  { id: "mes", rotulo: "Vendas mensais", descricao: "este mês" },
];

function SalesBar({ resumo, onOpen }: { resumo?: ResumoVendas | null; onOpen: (periodo: PeriodoVendas) => void }) {
  return <nav className="appbar-sales" aria-label="Vendas por período">
    {PERIODOS_VENDAS.map(({ id, rotulo, descricao }) => {
      const periodo = resumo?.periodos[id];
      const temVendas = Boolean(periodo && periodo.registros > 0);
      const valor = temVendas && periodo ? brl(periodo.faturamento_bruto) : "—";
      const detalhe = !resumo ? "Carregando vendas" : temVendas ? `Faturamento bruto de ${descricao}: ${valor}` : `Sem vendas registradas ${descricao}`;
      return <button type="button" className="appbar-stat" key={id} onClick={() => onOpen(id)}
        title={detalhe} aria-label={`${rotulo}. ${detalhe}. Ver no Analytics`}>
        <small>{rotulo}</small><strong>{valor}</strong>
      </button>;
    })}
  </nav>;
}

type AppBarProps = {
  onMenu: () => void;
  estado: Estado | null;
  onCart: () => void;
  onVendas: (periodo: PeriodoVendas) => void;
  onPratos: () => void;
  onNegocio: () => void;
};

export function AppBar({ onMenu, estado, onCart, onVendas, onPratos, onNegocio }: AppBarProps) {
  const pratos = estado?.pratos_escolhidos?.length;
  return (
    <header className="appbar">
      <button className="menu-btn" type="button" aria-label="Abrir o menu" onClick={onMenu}>
        <MenuIcon />
      </button>
      <div className="appbar-brand">
        <IfoodLogo height={34} />
        <span className="appbar-sep" aria-hidden="true" />
        <div className="appbar-product">
          <span>consultoria local</span>
          <strong>Cardápio e preço</strong>
        </div>
      </div>
      <div className="appbar-metrics">
        <BudgetBar estado={estado} onOpen={onCart} />
        <SalesBar resumo={estado?.vendas_resumo} onOpen={onVendas} />
        <button type="button" className="appbar-stat appbar-dishes" onClick={onPratos}
          title="Pratos salvos, incluindo rascunhos" aria-label={`Abrir Minha cozinha na aba Pratos${pratos === undefined ? "" : `. ${pratos} ${pratos === 1 ? "prato salvo" : "pratos salvos"}`}`}>
          <small>Pratos</small><strong>{pratos ?? "—"}</strong>
        </button>
      </div>
      <button type="button" className="appbar-store" onClick={onNegocio} aria-label="Sabor da Maria. Abrir Meu negócio" title="Meu negócio">
        <Mark size={32} />
        <div className="appbar-store-copy">
          <span>delivery</span>
          <strong>Sabor da Maria</strong>
        </div>
      </button>
    </header>
  );
}

type SidebarProps = {
  open: boolean;
  conversations: Conversation[];
  estadoHistorico: EstadoHistorico;
  activeId: string | null;
  estado: Estado | null;
  onClose: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onAgent: () => void;
  onCart: () => void;
  onCozinha: () => void;
  onNegocio: () => void;
  onAnalytics: () => void;
};

export function Sidebar({ open, conversations, estadoHistorico, activeId, estado, onClose, onNew, onSelect, onDelete, onAgent, onCart, onCozinha, onNegocio, onAnalytics }: SidebarProps) {
  const itens = estado ? contarItens(estado.carrinho) : 0;
  const resumoCozinha = estado?.cozinha.resumo;
  const escolhidos = estado?.pratos_escolhidos?.length ?? 0;
  const tipo = estado?.negocio ? estado.opcoes.tipos.find((opcao) => opcao.id === estado.negocio?.tipo)?.label : undefined;
  const local = localCurto(estado);
  return (
    <aside className={open ? "sidebar open" : "sidebar"} aria-label="Menu e conversas">
      <button className="new-chat" type="button" onClick={onNew}>
        <PlusIcon />
        Nova conversa
      </button>
      <nav className="menu" aria-label="Atalhos">
        <button type="button" className="menu-item" onClick={() => { onAgent(); onClose(); }}>
          <AgentIcon />
          <span>
            <strong>Agente de IA</strong>
            <small>Capacidades, atividade e fontes</small>
          </span>
        </button>
        <button
          type="button"
          className="menu-item"
          onClick={() => {
            onCart();
            onClose();
          }}
        >
          <CartIcon />
          <span>
            <strong>Carrinho</strong>
            <small>{estado ? `${itens} ${itens === 1 ? "item" : "itens"} · saldo ${brl(estado.orcamento.saldo_gerencial ?? estado.orcamento.saldo)}` : "indisponível"}</small>
          </span>
        </button>
        <button
          type="button"
          className="menu-item"
          onClick={() => {
            onCozinha();
            onClose();
          }}
        >
          <KitchenIcon />
          <span>
            <strong>Minha cozinha</strong>
            <small>
              {escolhidos ? `${escolhidos} ${escolhidos === 1 ? "prato escolhido" : "pratos escolhidos"}` : !resumoCozinha
                ? "indisponível"
                : resumoCozinha.respondidos === 0
                  ? "Marque o que você tem e sabe fazer"
                  : `${resumoCozinha.respondidos} de ${resumoCozinha.total} itens respondidos`}
            </small>
          </span>
        </button>
        <button
          type="button"
          className="menu-item"
          onClick={() => {
            onNegocio();
            onClose();
          }}
        >
          <StoreIcon />
          <span>
            <strong>Meu negócio</strong>
            <small>{[tipo, local].filter(Boolean).join(" · ") || (estado ? "Preencher para receber sugestões" : "indisponível")}</small>
          </span>
        </button>
        <button
          type="button"
          className="menu-item"
          onClick={() => {
            onAnalytics();
            onClose();
          }}
        >
          <ChartIcon />
          <span>
            <strong>Analytics</strong>
            <small>Custos, preços e lucro dos seus pratos</small>
          </span>
        </button>
      </nav>
      <p className="nav-label">Conversas</p>
      <ul className="conv-list">
        {estadoHistorico === "local" ? <li className="conv-empty" role="status">Histórico no navegador. Aguardando conexão com o banco.</li> : null}
        {estadoHistorico === "parcial" ? <li className="conv-empty" role="status">Algumas alterações ficaram só neste navegador.</li> : null}
        {conversations.length === 0 && estadoHistorico !== "local" && estadoHistorico !== "parcial" ? (
          <li className="conv-empty">{estadoHistorico === "carregando" ? "Carregando conversas…" : "Nenhuma ainda. A primeira começa aqui do lado."}</li>
        ) : null}
        {conversations.map((conversation) => (
          <li key={conversation.id}>
            <button
              type="button"
              className={conversation.id === activeId ? "conv active" : "conv"}
              onClick={() => {
                onSelect(conversation.id);
                onClose();
              }}
            >
              <ChatIcon />
              <span className="conv-title">{conversation.title}</span>
            </button>
            <button
              type="button"
              className="icon-btn"
              aria-label={`Apagar ${conversation.title}`}
              onClick={() => onDelete(conversation.id)}
            >
              <CloseIcon />
            </button>
          </li>
        ))}
      </ul>
      <div className="side-foot">
        <p className="legal">
          <strong>Protótipo de um desafio técnico do iFood.</strong> A logo, o nome e as cores do iFood aparecem aqui só para
          ambientar esta demonstração. Não é um produto oficial e o uso para fins comerciais é proibido.
        </p>
        <p className="copyright">Marca e logotipo © iFood. Todos os direitos reservados ao iFood.</p>
      </div>
    </aside>
  );
}

type ChatPaneProps = {
  messages: ChatMessage[];
  onboarding?: ReactNode;
  conducao?: ReactNode;
  draft: string;
  sending: boolean;
  online: Online;
  modoLivre?: boolean;
  onDraft: (value: string) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  onFill: (text: string) => void;
  focusTick: number;
  webSearch: boolean;
  onToggleWebSearch: () => void;
  compactProgress: ReactNode;
};

export function ChatPane({ messages, onboarding, conducao, draft, sending, online, modoLivre, onDraft, onSend, onStop, focusTick, webSearch, onToggleWebSearch, compactProgress }: ChatPaneProps) {
  const threadRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const acompanharFim = useRef(true);
  const ultimaMensagem = useRef<string | undefined>(undefined);
  const cadastroVisivel = Boolean(onboarding);

  useEffect(() => {
    const thread = threadRef.current;
    const ultimaId = messages.at(-1)?.id;
    const novaMensagem = ultimaId !== ultimaMensagem.current;
    ultimaMensagem.current = ultimaId;
    if (thread && !cadastroVisivel && (acompanharFim.current || novaMensagem)) {
      acompanharFim.current = true;
      thread.scrollTop = thread.scrollHeight;
    }
  }, [messages, cadastroVisivel]);

  useEffect(() => {
    const thread = threadRef.current;
    const conteudo = thread?.querySelector(".thread-inner");
    if (!thread || !conteudo) return;
    const aoRolar = () => { acompanharFim.current = thread.scrollHeight - thread.clientHeight - thread.scrollTop < 100; };
    // Cenários de preço chegam depois da resposta. Acompanhe esse crescimento só
    // quando a pessoa já estava no fim; não interrompa a leitura de mensagens antigas.
    const observer = new ResizeObserver(() => {
      if (!cadastroVisivel && acompanharFim.current) thread.scrollTop = thread.scrollHeight;
    });
    observer.observe(conteudo);
    thread.addEventListener("scroll", aoRolar, { passive: true });
    return () => {
      observer.disconnect();
      thread.removeEventListener("scroll", aoRolar);
    };
  }, [cadastroVisivel]);

  useEffect(() => {
    if (!cadastroVisivel) return;
    threadRef.current?.querySelector(".cadastro-chat")?.scrollIntoView({ block: "start" });
  }, [cadastroVisivel]);

  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    box.style.height = "0px";
    box.style.height = `${Math.min(box.scrollHeight, 160)}px`;
  }, [draft]);

  useEffect(() => {
    if (focusTick === 0) return;
    boxRef.current?.focus();
  }, [focusTick]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSend(draft);
  };

  const status = online === null ? "conectando…" : online ? "online" : "acordando a cozinha…";
  const nomeAssistente = modoLivre ? "Mestre Cuca" : "Consultora";

  return (
    <main className="chat">
      <header className="chat-head">
        <Avatar size={44} status={online ? "on" : "wait"} />
        <div>
          <h1>{modoLivre ? nomeAssistente : "Consultora de cardápio"}</h1>
          <p className={online ? "chat-status on" : "chat-status"}>
            <span className="dot" />
            {status}
            <span className="chat-role">assistente com IA</span>
          </p>
        </div>
      </header>
      <div className="compact-progress">{compactProgress}</div>
      <div className="thread" ref={threadRef}>
        <div className="thread-inner">
          <p className="day">Hoje</p>
          {messages.length === 0 && !modoLivre ? <Welcome /> : null}
          {messages.map((message) => (
            <MessageView key={message.id} message={message} nomeAssistente={nomeAssistente} onInspecionar={() => { acompanharFim.current = false; }} />
          ))}
          {onboarding}
          {conducao}
        </div>
      </div>
      {online === false ? (
        <p className="banner" role="status">
          {modoLivre ? "A conexão com Mestre Cuca está indisponível. Tentando reconectar…" : "A conexão com a consultora está indisponível. Tentando reconectar…"}
        </p>
      ) : null}
      <form className="composer" onSubmit={submit}>
        <div className="composer-tools">
          <button type="button" className="web-search-toggle" aria-pressed={webSearch} disabled={sending} onClick={onToggleWebSearch}>
            <GlobeIcon />
            Busca na web
          </button>
        </div>
        <div className="composer-box">
          <label className="sr-only" htmlFor="fala">
            {modoLivre ? "Mensagem para Mestre Cuca" : "Mensagem para a consultora"}
          </label>
          <textarea
            id="fala"
            ref={boxRef}
            rows={1}
            value={draft}
            placeholder={webSearch ? (modoLivre ? "Digite o que você quer pesquisar…" : "Digite uma receita ou envie para usar sua despensa")
              : modoLivre ? "Converse com Mestre Cuca…" : "Escreve do jeito que você falaria com ela…"}
            disabled={sending}
            onChange={(event) => onDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                onSend(draft);
              }
            }}
          />
          {sending ? (
            <button className="send stop" type="button" aria-label="Parar a resposta" title="Parar" onClick={onStop}>
              <StopIcon />
            </button>
          ) : (
            <button className="send" type="submit" aria-label={webSearch && !modoLivre && !draft.trim() ? "Pesquisar receitas para minha despensa" : "Enviar mensagem"} title="Enviar" disabled={!draft.trim() && (!webSearch || Boolean(modoLivre))}>
              <SendIcon />
            </button>
          )}
        </div>
        <p className="hint">{draft.trim() ? "Revise a mensagem · " : ""}Enter envia · Shift+Enter quebra a linha</p>
      </form>
    </main>
  );
}

function Welcome() {
  return (
    <article className="msg assistant">
      <Avatar size={34} />
      <div className="msg-col">
        <div className="bubble">
          <span className="sr-only">Consultora: </span>
          <Markdown text={WELCOME} />
        </div>
      </div>
    </article>
  );
}

function StepMark({ tone }: { tone: StepView["tone"] }) {
  if (tone === "done") return <CheckIcon />;
  if (tone === "error" || tone === "locked") return <AlertIcon />;
  return null; // rodando: o anel girando é o próprio marcador (CSS)
}

function StepRow({ step, since }: { step: StepView; since?: number }) {
  return (
    <li className={`step ${step.tone}`}>
      <span className="step-mark">
        <StepMark tone={step.tone} />
      </span>
      <div className="step-body">
        <p className="step-head">
          <strong className="step-title">{step.title}</strong>
          {step.detail ? <q className="step-detail">{step.detail}</q> : null}
          {step.status === "running" && since ? <Elapsed since={since} /> : step.ms !== undefined ? <span className="step-ms">{duracao(step.ms)}</span> : null}
        </p>
        {step.summary && step.items.length > 0 ? (
          <details className="step-more">
            <summary>{step.summary}</summary>
            <ul>
              {step.items.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </details>
        ) : step.summary ? (
          <p className="step-sum">{step.summary}</p>
        ) : null}
      </div>
    </li>
  );
}

// Cada ação da consultora aparece aqui enquanto acontece (a busca digitada, a página lida, a despensa, a conta do orçamento)
// e fica no balão depois, com o que ela achou. Entre uma ação e outra, "Pensando": é o modelo decidindo o próximo passo.
function Steps({ message }: { message: ChatMessage }) {
  const steps = message.tools.map(describeStep);
  const running = message.tools.some((tool) => tool.status === "running");
  const waitingSince = message.tools.reduce((latest, tool) => Math.max(latest, tool.endedAt ?? 0), message.startedAt ?? 0) || undefined;
  return (
    <details className="steps">
      <summary>
        Atividade · {steps.length} {steps.length === 1 ? "ação" : "ações"}
        {message.pending ? <span className="steps-live"> · ao vivo</span> : null}
      </summary>
      <ol className="steps-list">
        {message.tools.map((tool, index) => (
          <StepRow key={tool.id} step={steps[index]} since={tool.startedAt} />
        ))}
        {message.pending && !running ? (
          <li className="step thinking">
            <span className="step-mark">
              <span className="typing" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
            </span>
            <div className="step-body">
              <p className="step-head">
                <strong className="step-title">{message.content ? "Escrevendo a resposta" : "Pensando no próximo passo"}</strong>
                {waitingSince ? <Elapsed since={waitingSince} /> : null}
              </p>
            </div>
          </li>
        ) : null}
      </ol>
    </details>
  );
}

function TurnProgress({ message }: { message: ChatMessage }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const current = ferramentaEmAndamento(message.tools);
  const label = current ? describeStep(current).title
    : message.content ? "Preparando a resposta" : message.tools.length ? "Organizando os resultados" : "Aguardando a consultora";
  const prolonged = message.startedAt != null && now - message.startedAt >= 30_000;
  return (
    <div className="turn-progress">
      <p className="turn-progress-title">
        <span className="progress-ring" aria-hidden="true" />
        <span role="status" aria-live="polite">{label}</span>
        {message.startedAt ? <span className="elapsed" title="Tempo desde o envio">{textoDecorrido(message.startedAt, now)}</span> : null}
      </p>
      {current?.detail ? <p className="turn-progress-detail">{current.detail}</p> : null}
      {prolonged ? <p className="turn-progress-note">Ainda aguardando a conclusão. Você pode interromper pelo botão Parar, sem reenviar o pedido.</p> : null}
    </div>
  );
}

function ConteudoDaMensagem({ texto, recolherFicha, onInspecionar }: { texto: string; recolherFicha: boolean; onInspecionar?: () => void }) {
  const paragrafos = texto.split(/\n[\t ]*\n+/);
  const ingredientes = paragrafos.findIndex((paragrafo) => /^Ingredientes do lote:/i.test(paragrafo));
  const preparo = paragrafos.findIndex((paragrafo) => /^Preparo\s*(?:\([^\n]*\))?:/i.test(paragrafo));
  // Somente fichas canônicas completas. Perguntas, pendências e cenários de
  // preço continuam visíveis; o texto salvo e enviado à IA não é alterado.
  const inicioDetalhes = Math.min(ingredientes, 2);
  const trechoDaFicha = paragrafos.slice(inicioDetalhes, preparo + 1);
  const pendenciaOuPergunta = /\?|\b(?:pend[eê]ncias?|bloqueios?|bloquead[oa]|falta(?:m)? confirmar|antes de aprovar)\b/i;
  const cenariosDePreco = /cen[aá]rios? de pre[cç]o|pre[cç]o m[ií]nimo|escolh[ae][^\n]*pre[cç]o|quanto cobrar/i;
  const fichaCompleta = ingredientes > 0 && preparo > ingredientes
    && paragrafos.filter((paragrafo) => /^Ingredientes do lote:/i.test(paragrafo)).length === 1
    && paragrafos.filter((paragrafo) => /^Preparo\s*(?:\([^\n]*\))?:/i.test(paragrafo)).length === 1;
  if (!recolherFicha || texto.length <= 1400 || paragrafos.length < 5 || !fichaCompleta
      || trechoDaFicha.some((paragrafo) => pendenciaOuPergunta.test(paragrafo)) || cenariosDePreco.test(texto)) {
    return <Markdown text={texto} />;
  }
  const metadados = trechoDaFicha.filter((paragrafo) => /^(?:Fonte(?: original)?|Rendimento(?: proposto)?):/i.test(paragrafo));
  return <div className="receipt-message">
    <Markdown text={[...paragrafos.slice(0, inicioDetalhes), ...metadados].join("\n\n")} />
    <details className="receipt-details">
      <summary onClick={onInspecionar}>Ver detalhes da ficha</summary>
      <div className="receipt-details-body"><Markdown text={trechoDaFicha.join("\n\n")} /></div>
    </details>
    {preparo < paragrafos.length - 1 ? <Markdown text={paragrafos.slice(preparo + 1).join("\n\n")} /> : null}
  </div>;
}

function MessageView({ message, nomeAssistente, onInspecionar }: { message: ChatMessage; nomeAssistente: string; onInspecionar?: () => void }) {
  const mine = message.role === "user";
  // Só mensagem recém-criada entra animada; ao recarregar a conversa nada pisca.
  const fresh = typeof message.at === "number" && Date.now() - message.at < 2000;
  const classes = ["msg", mine ? "user" : "assistant", fresh ? "fresh" : ""].filter(Boolean).join(" ");
  return (
    <article className={classes}>
      {mine ? null : <Avatar size={34} />}
      <div className="msg-col">
        <div className={message.error ? "bubble error" : "bubble"}>
          <span className="sr-only">{mine ? "Dona Maria: " : `${nomeAssistente}: `}</span>
          {mine && message.webSearch ? <p className="message-search"><GlobeIcon /> Busca na web</p> : null}
          {message.pending ? <TurnProgress message={message} /> : null}
          {message.tools.length > 0 ? <Steps message={message} /> : null}
          {message.content ? <ConteudoDaMensagem texto={mine ? message.content : esconderPedidoCadastro(message.content)}
            recolherFicha={!mine && !message.pending && !message.error} onInspecionar={onInspecionar} /> : null}
        </div>
        {typeof message.at === "number" && !message.pending ? (
          <p className="stamp">{message.cadastroEnviado ? "Dados salvos · " : ""}{CLOCK.format(message.at)}</p>
        ) : null}
      </div>
    </article>
  );
}

type RailProps = {
  tools: ToolTrace[];
  pending: boolean;
  caminho: ReactNode;
};

export function Rail({ caminho }: RailProps) {
  return (
    <aside className="rail" aria-label="Andamento do cardápio">
      {caminho}
    </aside>
  );
}
