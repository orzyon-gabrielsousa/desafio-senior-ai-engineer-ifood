import { useId } from "react";
import { CheckIcon, ChevronIcon } from "./icons";
import { brl } from "./lib/format";
import type { Estado } from "./types";

const ETAPAS = [
  { id: "negocio", titulo: "Negócio", acao: "Conhecer meu negócio", descricao: "Vamos conhecer seu tipo de negócio, sua região e o que você quer vender. Isso orienta as receitas e o uso dos R$ 80 para complementos.", decisao: "Conte como é seu negócio. A consultora pergunta só o que falta." },
  { id: "estoque", titulo: "Seu estoque", acao: "Ver meu estoque no chat", descricao: "Veja os ingredientes que você já comprou, as quantidades e os custos. Esses valores entram no custo dos pratos; o orçamento fica para o que faltar comprar.", decisao: "Confira a lista e diga quando estiver pronta para começar." },
  { id: "ajuda", titulo: "Escolher caminho", acao: "Escolher como montar", descricao: "Você pode pedir sugestões da IA com receitas reais da internet ou trazer uma ideia de prato. As opções levam em conta seu negócio, sua região e o estoque disponível.", decisao: "Escolha se quer ajuda da IA ou prefere descrever seu próprio prato." },
  { id: "montagem", titulo: "Montar pratos", acao: "Continuar meu prato", descricao: "Montamos uma ficha por vez: ingredientes, quantidades e preparo. A consultora confere sua cozinha, as habilidades e o que falta comprar antes do seu aceite.", decisao: "Confira a proposta. Você pode aceitar, adaptar ou escolher outro prato." },
  { id: "divulgacao", titulo: "Preço e divulgação", acao: "Ver preço e divulgação", descricao: "Compare os custos e os cenários de preço com a taxa de 10% do desafio. Depois de escolher quanto cobrar, você pode pedir à consultora que prepare a divulgação.", decisao: "Você escolhe o preço e decide se quer preparar a divulgação agora." },
] as const;

export type EtapaDoPrato = typeof ETAPAS[number]["id"];

export function etapasConcluidas(estado: Estado | null): Record<EtapaDoPrato, boolean> {
  const salvas = estado?.jornada?.concluidas;
  if (salvas) return Object.fromEntries(ETAPAS.map(({ id }) => [id, salvas[id] === true])) as Record<EtapaDoPrato, boolean>;
  // Compatibilidade de leitura: navegar nunca aceita uma receita nem confirma estoque.
  const pratos = estado?.pratos_escolhidos ?? [];
  return {
    negocio: estado?.cadastro?.negocio_pendente === false,
    estoque: estado?.despensa_consultada === true,
    ajuda: pratos.length > 0,
    montagem: pratos.some((prato) => (prato.status === "cmv_calculado" || prato.status === "aceito") && !prato.bloqueios?.length),
    divulgacao: pratos.some((prato) => prato.status === "aceito" && !prato.bloqueios?.length),
  };
}

export function proximaEtapa(concluidas: Record<EtapaDoPrato, boolean>): EtapaDoPrato {
  return ETAPAS.find((etapa) => !concluidas[etapa.id])?.id ?? "divulgacao";
}

function resumoDaEtapa(etapa: EtapaDoPrato, estado?: Estado | null): string | null {
  if (!estado) return null;
  const negocio = estado.negocio;
  const pratos = estado.pratos_escolhidos ?? [];
  switch (etapa) {
    case "negocio": {
      if (!negocio) return "Seu perfil ainda está começando.";
      const tipo = estado.opcoes.tipos.find(({ id }) => id === negocio.tipo)?.label;
      const local = [negocio.cidade, negocio.estado].filter(Boolean).join("/");
      return [tipo, local].filter(Boolean).join(" · ") || "Seu perfil ainda está começando.";
    }
    case "estoque": {
      const total = estado.jornada?.estoque?.length;
      return `${total === undefined ? "O estoque é consultado sem gerar compras." : `${total} ingredientes na sua despensa.`} Saldo para complementos: ${brl(estado.orcamento.saldo)}.`;
    }
    case "ajuda":
      return estado.jornada?.controle?.ajuda === "ia" ? "Você escolheu receber sugestões da IA."
        : estado.jornada?.controle?.ajuda === "proprio" ? "Você escolheu trazer sua própria ideia."
        : "A escolha do caminho ainda é sua.";
    case "montagem": {
      const aprovados = pratos.filter(({ status }) => status === "aceito").length;
      return pratos.length ? `${pratos.length} ${pratos.length === 1 ? "ficha salva" : "fichas salvas"} · ${aprovados} ${aprovados === 1 ? "prato no cardápio" : "pratos no cardápio"}. No aceite, o lote entra no histórico e atualiza o estoque disponível e as despesas.`
        : "Ainda não há uma ficha de prato salva. Cozinha, ingredientes e compras serão conferidos aqui.";
    }
    case "divulgacao": {
      if (estado.jornada?.plano_divulgacao) return "Plano de divulgação salvo para o cardápio atual.";
      const aprovados = pratos.filter(({ status }) => status === "aceito").length;
      return aprovados ? `${aprovados} ${aprovados === 1 ? "prato com preço escolhido" : "pratos com preço escolhido"}. Você recebe 90% do preço; o lucro estimado desconta o custo dos ingredientes.`
        : "A consultora apresenta 2–3 cenários. O preço só fica registrado depois da sua escolha.";
    }
  }
}

type Props = {
  etapa: EtapaDoPrato;
  etapaAtual?: EtapaDoPrato;
  estado?: Estado | null;
  concluidas: Record<EtapaDoPrato, boolean>;
  /** A consultora já mostrou receitas nesta conversa e ela ainda não escolheu. */
  escolhaPendente?: boolean;
  onEtapa: (etapa: EtapaDoPrato) => void;
  onAcao: (etapa: EtapaDoPrato) => void;
};

export function CaminhoDoPrato({ etapa, etapaAtual, estado, concluidas, escolhaPendente = false, onEtapa, onAcao }: Props) {
  const tituloId = useId();
  const detalheId = useId();
  const index = Math.max(0, ETAPAS.findIndex((item) => item.id === etapa));
  const atual = ETAPAS[index];
  const anterior = ETAPAS[index - 1];
  const seguinte = ETAPAS[index + 1];
  const emCurso = etapaAtual ?? estado?.jornada?.passo ?? proximaEtapa(concluidas);
  const indexEmCurso = Math.max(0, ETAPAS.findIndex(({ id }) => id === emCurso));
  const futura = index > indexEmCurso && !concluidas[atual.id];
  const naEtapaAtual = atual.id === emCurso;
  const resumo = resumoDaEtapa(atual.id, estado);
  // Com opções na tela a próxima decisão é escolher; "vou pesquisar" mandaria ela de volta para a busca.
  const escolhendo = escolhaPendente && naEtapaAtual && atual.id === "montagem";
  const decisao = escolhendo ? "A consultora já mostrou opções. Escolha uma no chat, peça outra busca ou traga o seu prato."
    : naEtapaAtual ? estado?.jornada?.descricao || atual.decisao : atual.decisao;
  const acao = atual.id === "negocio" && concluidas.negocio ? "Revisar meu negócio"
    : escolhendo ? "Pesquisar outras opções"
    : naEtapaAtual && estado?.jornada?.acao ? estado.jornada.acao : atual.acao;

  return (
    <section className="dish-path" aria-labelledby={tituloId}>
      <div className="dish-path-head">
        <h2 id={tituloId}>Caminho do prato</h2>
        <span>{index + 1} de {ETAPAS.length}</span>
      </div>
      <div className="dish-path-body">
        <button type="button" className="dish-path-arrow" aria-label="Etapa anterior" disabled={!anterior} onClick={() => onEtapa(anterior.id)}><ChevronIcon direction="left" /></button>
        <div className="dish-path-current">
          <p aria-live="polite">
            <strong>{atual.titulo}</strong>
            {concluidas[atual.id] ? <span className="dish-path-check"><CheckIcon /><span className="sr-only">Concluída</span></span> : null}
          </p>
          <span className="dish-path-status">{concluidas[atual.id] ? "Concluída" : futura ? "Próxima etapa" : naEtapaAtual ? "Etapa atual" : "Para revisar"}</span>
        </div>
        <button type="button" className="dish-path-arrow" aria-label="Próxima etapa" disabled={!seguinte} onClick={() => onEtapa(seguinte.id)}><ChevronIcon direction="right" /></button>
      </div>
      <nav className="dish-path-stops" aria-label="Etapas do prato">
        {ETAPAS.map((item, indice) => (
          <button key={item.id} type="button" className={concluidas[item.id] ? "done" : undefined}
            aria-current={item.id === atual.id ? "step" : undefined}
            aria-label={`${indice + 1}. ${item.titulo}${concluidas[item.id] ? ", concluída" : item.id === emCurso ? ", etapa atual" : ""}`}
            title={item.titulo} onClick={() => onEtapa(item.id)}><span>{indice + 1}</span></button>
        ))}
      </nav>
      <div className="dish-path-detail" aria-labelledby={detalheId}>
        <h3 id={detalheId}>{futura ? "O que vem a seguir" : "Sobre esta etapa"}</h3>
        <p>{atual.descricao}</p>
        {resumo ? <p className="dish-path-summary">{resumo}</p> : null}
        <p className="dish-path-decision">{decisao}</p>
        {futura ? <p className="dish-path-wait">Seguimos para cá depois de concluir {ETAPAS[indexEmCurso].titulo.toLocaleLowerCase("pt-BR")}.</p>
          : <button type="button" className="link-btn" onClick={() => onAcao(atual.id)}>{acao}</button>}
      </div>
      {seguinte ? <p className="dish-path-next">A seguir, {seguinte.titulo.toLocaleLowerCase("pt-BR")}</p> : null}
    </section>
  );
}
