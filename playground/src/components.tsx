import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Avatar, IfoodLogo, Mark } from "./brand";
import { esconderPedidoCadastro } from "./cadastro-chat";
import { AgentIcon, AlertIcon, CartIcon, ChartIcon, ChatIcon, CheckIcon, CloseIcon, GlobeIcon, KitchenIcon, MenuIcon, PlusIcon, SendIcon, StopIcon, StoreIcon } from "./icons";
import { brl, duracao } from "./lib/format";
import { contarItens, localCurto } from "./lib/loja";
import { Markdown } from "./lib/markdown";
import { STARTERS, WELCOME } from "./lib/prompts";
import { describeStep } from "./lib/tools";
import type { StepView } from "./lib/tools";
import type { EstadoHistorico } from "./lib/useHistorico";
import type { ChatMessage, Conversation, Estado, Online, ToolTrace } from "./types";

const CLOCK = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" });

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const seconds = Math.max(0, Math.round((now - since) / 1000));
  return <span className="elapsed">{seconds}s</span>;
}

// Quanto ela tem, quanto já escolheu e quanto sobra: sempre à vista, e um toque abre o carrinho.
function BudgetBar({ estado, onOpen }: { estado: Estado | null; onOpen: () => void }) {
  const o = estado?.orcamento;
  const tom = !o ? "idle" : o.saldo <= 0 ? "none" : o.saldo / (o.inicial || 1) < 0.25 ? "low" : "ok";
  return (
    <button
      type="button"
      className={`budget ${tom}`}
      onClick={onOpen}
      aria-label={o ? `Abrir o carrinho. Saldo ${brl(o.saldo)}` : "Abrir o carrinho"}
    >
      <span className="budget-stat hide-sm">
        <small>Orçamento</small>
        <strong>{o ? brl(o.inicial) : "—"}</strong>
      </span>
      <span className="budget-stat hide-sm">
        <small>Compras e reservas</small>
        <strong>{o ? brl(o.comprometido + o.carrinho) : "—"}</strong>
      </span>
      <span className="budget-stat saldo">
        <small>Saldo</small>
        <strong>{o ? brl(o.saldo) : "—"}</strong>
      </span>
      <span className="budget-cart">
        <CartIcon />
        <b>{estado ? contarItens(estado.carrinho) : 0}</b>
      </span>
    </button>
  );
}

export function AppBar({ onMenu, estado, onCart }: { onMenu: () => void; estado: Estado | null; onCart: () => void }) {
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
      <BudgetBar estado={estado} onOpen={onCart} />
      <div className="appbar-store">
        <Mark size={32} />
        <div className="appbar-store-copy">
          <span>delivery</span>
          <strong>Sabor da Maria</strong>
        </div>
      </div>
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
            <small>{estado ? `${itens} ${itens === 1 ? "item" : "itens"} · saldo ${brl(estado.orcamento.saldo)}` : "indisponível"}</small>
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
  draft: string;
  sending: boolean;
  online: Online;
  onDraft: (value: string) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  onFill: (text: string) => void;
  focusTick: number;
  webSearch: boolean;
  onToggleWebSearch: () => void;
  compactProgress: ReactNode;
};

export function ChatPane({ messages, onboarding, draft, sending, online, onDraft, onSend, onStop, onFill, focusTick, webSearch, onToggleWebSearch, compactProgress }: ChatPaneProps) {
  const threadRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const cadastroVisivel = Boolean(onboarding);

  useEffect(() => {
    const thread = threadRef.current;
    if (thread && !cadastroVisivel) thread.scrollTop = thread.scrollHeight;
  }, [messages, cadastroVisivel]);

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

  return (
    <main className="chat">
      <header className="chat-head">
        <Avatar size={44} status={online ? "on" : "wait"} />
        <div>
          <h1>Consultora de cardápio</h1>
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
          <Welcome showChips={messages.length === 0 && !cadastroVisivel} onFill={onFill} />
          {messages.map((message, index) => (
            <MessageView key={message.id} message={message} latest={index === messages.length - 1} />
          ))}
          {onboarding}
        </div>
      </div>
      {online === false ? (
        <p className="banner" role="status">
          O modelo local ainda está carregando. A primeira subida leva cerca de 30 segundos.
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
            Mensagem para a consultora
          </label>
          <textarea
            id="fala"
            ref={boxRef}
            rows={1}
            value={draft}
            placeholder={webSearch ? "Digite uma receita ou envie para usar sua despensa" : "Escreve do jeito que você falaria com ela…"}
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
            <button className="send" type="submit" aria-label={webSearch && !draft.trim() ? "Pesquisar receitas para minha despensa" : "Enviar mensagem"} title="Enviar" disabled={!draft.trim() && !webSearch}>
              <SendIcon />
            </button>
          )}
        </div>
        <p className="hint">Enter envia · Shift+Enter quebra a linha</p>
      </form>
    </main>
  );
}

function Welcome({ showChips, onFill }: { showChips: boolean; onFill: (text: string) => void }) {
  return (
    <article className="msg assistant">
      <Avatar size={34} />
      <div className="msg-col">
        <div className="bubble">
          <span className="sr-only">Consultora: </span>
          <Markdown text={WELCOME} />
        </div>
        {showChips ? (
          <>
            <div className="chips" role="group" aria-label="Sugestões para começar">
              {STARTERS.map((prompt) => (
                <button
                  key={prompt.title}
                  type="button"
                  className="chip"
                  title={prompt.text}
                  onClick={() => onFill(prompt.text)}
                >
                  {prompt.title}
                </button>
              ))}
            </div>
            <p className="chips-hint">Escolha uma sugestão para preencher a mensagem, ou escreva do seu jeito.</p>
          </>
        ) : null}
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
function Steps({ message, open }: { message: ChatMessage; open: boolean }) {
  const steps = message.tools.map(describeStep);
  const running = message.tools.some((tool) => tool.status === "running");
  const waitingSince = message.tools.reduce((latest, tool) => Math.max(latest, tool.endedAt ?? 0), message.startedAt ?? 0) || undefined;
  return (
    <details className="steps" open={open}>
      <summary>
        {steps.length} {steps.length === 1 ? "passo" : "passos"} da consultora
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

function MessageView({ message, latest }: { message: ChatMessage; latest: boolean }) {
  const mine = message.role === "user";
  // Só mensagem recém-criada entra animada; ao recarregar a conversa nada pisca.
  const fresh = typeof message.at === "number" && Date.now() - message.at < 2000;
  const classes = ["msg", mine ? "user" : "assistant", fresh ? "fresh" : ""].filter(Boolean).join(" ");
  return (
    <article className={classes}>
      {mine ? null : <Avatar size={34} />}
      <div className="msg-col">
        <div className={message.error ? "bubble error" : "bubble"}>
          <span className="sr-only">{mine ? "Dona Maria: " : "Consultora: "}</span>
          {mine && message.webSearch ? <p className="message-search"><GlobeIcon /> Busca na web</p> : null}
          {message.tools.length > 0 ? <Steps message={message} open={message.pending === true || latest} /> : null}
          {message.content ? <Markdown text={mine ? message.content : esconderPedidoCadastro(message.content)} /> : null}
          {message.pending && !message.content && message.tools.length === 0 ? (
            <p className="waiting">
              <span className="typing" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              <span>
                Pensando
                {message.startedAt ? <Elapsed since={message.startedAt} /> : null}
              </span>
            </p>
          ) : null}
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

export function Rail({ tools, pending, caminho }: RailProps) {
  const current = tools.find((tool) => tool.status === "running");
  const agora = current ? describeStep(current) : null;
  const recentes = tools
    .filter((tool) => tool.status !== "running")
    .slice(-3)
    .reverse()
    .map(describeStep);
  return (
    <aside className="rail" aria-label="Andamento do cardápio">
      {caminho}
      <p className="nav-label">Agora</p>
      <p className="now">{agora ? agora.title : pending ? "Pensando…" : "Esperando a Dona Maria"}</p>
      {agora?.detail ? <p className="now-detail">{agora.detail}</p> : null}
      {recentes.length > 0 ? (
        <ol className="recent" aria-label="Últimas ações">
          {recentes.map((step) => (
            <li key={step.id} className={step.tone}>
              <span className="recent-mark">{step.tone === "error" || step.tone === "locked" ? <AlertIcon /> : <CheckIcon />}</span>
              <span>
                {step.title}
                {step.detail ? <small>{step.detail}</small> : null}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </aside>
  );
}
