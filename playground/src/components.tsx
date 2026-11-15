import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Avatar, IfoodLogo, Mark } from "./brand";
import { CartIcon, ChartIcon, ChatIcon, CheckIcon, CloseIcon, KitchenIcon, MenuIcon, PlusIcon, SendIcon, StopIcon, StoreIcon } from "./icons";
import { brl } from "./lib/format";
import { contarItens, localCurto } from "./lib/loja";
import { Markdown } from "./lib/markdown";
import { STARTERS, WELCOME } from "./lib/prompts";
import { STAGES, stageStatus } from "./lib/tools";
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
        <small>No carrinho</small>
        <strong>{o ? brl(o.carrinho) : "—"}</strong>
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
  activeId: string | null;
  estado: Estado | null;
  onClose: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onCart: () => void;
  onCozinha: () => void;
  onNegocio: () => void;
  onAnalytics: () => void;
};

export function Sidebar({ open, conversations, activeId, estado, onClose, onNew, onSelect, onDelete, onCart, onCozinha, onNegocio, onAnalytics }: SidebarProps) {
  const itens = estado ? contarItens(estado.carrinho) : 0;
  const resumoCozinha = estado?.cozinha.resumo;
  const tipo = estado?.negocio ? estado.opcoes.tipos.find((opcao) => opcao.id === estado.negocio?.tipo)?.label : undefined;
  const local = localCurto(estado);
  return (
    <aside className={open ? "sidebar open" : "sidebar"} aria-label="Menu e conversas">
      <button className="new-chat" type="button" onClick={onNew}>
        <PlusIcon />
        Nova conversa
      </button>
      <nav className="menu" aria-label="Atalhos">
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
              {!resumoCozinha
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
            <small>{tipo ?? (estado ? "Preencher para receber sugestões" : "indisponível")}</small>
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
            <small>{local ? `Mais vendidos · ${local}` : "Pratos mais vendidos por região"}</small>
          </span>
        </button>
      </nav>
      <p className="nav-label">Conversas</p>
      <ul className="conv-list">
        {conversations.length === 0 ? <li className="conv-empty">Nenhuma ainda. A primeira começa aqui do lado.</li> : null}
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
  draft: string;
  sending: boolean;
  online: Online;
  onDraft: (value: string) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  onFill: (text: string) => void;
  focusTick: number;
};

export function ChatPane({ messages, draft, sending, online, onDraft, onSend, onStop, onFill, focusTick }: ChatPaneProps) {
  const threadRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const thread = threadRef.current;
    if (thread) thread.scrollTop = thread.scrollHeight;
  }, [messages]);

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
      <div className="thread" ref={threadRef}>
        <div className="thread-inner">
          <p className="day">Hoje</p>
          <Welcome showChips={messages.length === 0} onFill={onFill} />
          {messages.map((message) => (
            <MessageView key={message.id} message={message} />
          ))}
        </div>
      </div>
      {online === false ? (
        <p className="banner" role="status">
          O modelo local ainda está carregando. A primeira subida leva cerca de 30 segundos.
        </p>
      ) : null}
      <form className="composer" onSubmit={submit}>
        <div className="composer-box">
          <label className="sr-only" htmlFor="fala">
            Mensagem para a consultora
          </label>
          <textarea
            id="fala"
            ref={boxRef}
            rows={1}
            value={draft}
            placeholder="Escreve do jeito que você falaria com ela…"
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
            <button className="send" type="submit" aria-label="Enviar mensagem" title="Enviar" disabled={!draft.trim()}>
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

function MessageView({ message }: { message: ChatMessage }) {
  const mine = message.role === "user";
  const running = message.tools.find((tool) => tool.status === "running");
  // Só mensagem recém-criada entra animada; ao recarregar a conversa nada pisca.
  const fresh = typeof message.at === "number" && Date.now() - message.at < 2000;
  const classes = ["msg", mine ? "user" : "assistant", fresh ? "fresh" : ""].filter(Boolean).join(" ");
  return (
    <article className={classes}>
      {mine ? null : <Avatar size={34} />}
      <div className="msg-col">
        <div className={message.error ? "bubble error" : "bubble"}>
          <span className="sr-only">{mine ? "Dona Maria: " : "Consultora: "}</span>
          {message.tools.length > 0 ? (
            <ul className="tools">
              {message.tools.map((tool) => (
                <li key={tool.id} className={`tool ${tool.status}`} title={tool.preview}>
                  <span className="tool-dot" />
                  {tool.label}
                  {tool.preview ? <em>{tool.preview}</em> : null}
                </li>
              ))}
            </ul>
          ) : null}
          {message.content ? <Markdown text={message.content} /> : null}
          {message.pending && !message.content ? (
            <p className="waiting">
              <span className="typing" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              <span>
                {running ? running.label : "Na cozinha"}
                {message.startedAt ? <Elapsed since={message.startedAt} /> : null}
              </span>
            </p>
          ) : null}
        </div>
        {typeof message.at === "number" && !message.pending ? <p className="stamp">{CLOCK.format(message.at)}</p> : null}
      </div>
    </article>
  );
}

type RailProps = {
  tools: ToolTrace[];
  pending: boolean;
};

export function Rail({ tools, pending }: RailProps) {
  const names = tools.map((tool) => tool.name);
  const running = tools.filter((tool) => tool.status === "running").map((tool) => tool.name);
  const current = tools.find((tool) => tool.status === "running");
  return (
    <aside className="rail" aria-label="Andamento e regras da casa">
      <p className="nav-label">Agora</p>
      <p className="now">{current ? current.label : pending ? "Na cozinha" : "Esperando a Dona Maria"}</p>
      <p className="nav-label">Regras da casa</p>
      <dl className="receipt">
        <div>
          <dt>Complementos</dt>
          <dd>R$ 80,00</dd>
        </div>
        <div>
          <dt>Taxa do delivery</dt>
          <dd>10%</dd>
        </div>
        <div>
          <dt>Ela recebe</dt>
          <dd>0,90 × P</dd>
        </div>
        <div>
          <dt>Preço mínimo</dt>
          <dd>CMV ÷ 0,90</dd>
        </div>
      </dl>
      <p className="rail-note">Os números vivos — estoque, CMV, saldo — aparecem na conversa. Aqui é a regra fixa do desafio.</p>
      <p className="nav-label">Caminho do prato</p>
      <ol className="stages">
        {STAGES.map((stage, index) => {
          const status = stageStatus(names, running, stage);
          return (
            <li key={stage.id} className={`stage ${status}`}>
              <span className="stage-index">{status === "done" ? <CheckIcon /> : index + 1}</span>
              <span>
                <strong>{stage.title}</strong>
                <small>{stage.hint}</small>
              </span>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}
