import { useEffect, useState } from "react";
import { AgentIcon, ChartIcon, GlobeIcon, KitchenIcon, StoreIcon } from "./icons";
import { getMemoria, type ResumoMemoria } from "./lib/api";
import { describeStep } from "./lib/tools";
import type { EstadoHistorico } from "./lib/useHistorico";
import { Sheet } from "./sheet";
import type { Online, ToolTrace } from "./types";

const CAPACIDADES = [
  { icon: StoreIcon, titulo: "Negócio e despensa", texto: "Usa o perfil do seu negócio, os ingredientes e os preços salvos para orientar o cardápio." },
  { icon: GlobeIcon, titulo: "Receitas na web", texto: "Pesquisa receitas, lê as páginas e mostra as fontes para você comparar as opções." },
  { icon: KitchenIcon, titulo: "Cozinha e compras", texto: "Confere equipamentos, técnicas e ingredientes. Aponta as compras que cabem no saldo." },
  { icon: ChartIcon, titulo: "Custos e cardápio", texto: "Calcula o custo por porção, compara cenários de preço e registra a sua escolha no cardápio." },
];

type Props = {
  online: Online;
  tools: ToolTrace[];
  pending: boolean;
  estadoHistorico: EstadoHistorico;
  conversaAberta: boolean;
  onClose: () => void;
};

function descricaoMemoria(status: EstadoHistorico) {
  if (status === "local") return "O histórico está disponível neste navegador. A gravação no banco será retomada quando a conexão voltar.";
  if (status === "parcial") return "O banco recusou parte do histórico. Essas alterações continuam neste navegador.";
  if (status === "salvando") return "Salvando o histórico no banco local…";
  if (status === "salvo") return "Histórico sincronizado com o banco local.";
  return "Consultando o histórico no banco local…";
}

export function AgentSheet({ online, tools, pending, estadoHistorico, conversaAberta, onClose }: Props) {
  const [memoria, setMemoria] = useState<ResumoMemoria | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void getMemoria(controller.signal).then((resumo) => {
      if (!controller.signal.aborted && Number.isFinite(resumo.conversas_salvas)) setMemoria(resumo);
    }).catch(() => {
      if (!controller.signal.aborted) setMemoria(null);
    });
    return () => controller.abort();
  }, [estadoHistorico, pending]);
  const atual = tools.find((tool) => tool.status === "running");
  const recentes = tools.filter((tool) => tool.status !== "running").slice(-3).reverse().map(describeStep);
  const disponibilidade = online === null ? "Conectando" : online ? "Online" : "Indisponível";
  return (
    <Sheet titleId="agente-titulo" title="Agente de IA" icon={<AgentIcon />} onClose={onClose}>
      <div className="agent-overview">
        <p className="sheet-note">Consultora de receitas e cardápio do Sabor da Maria</p>
        <span className={`agent-status${online ? " online" : ""}`}><span className="dot" />{disponibilidade}</span>
      </div>
      <h3 className="sheet-label">Agora</h3>
      <div className="agent-now">
        <p role="status">{atual ? describeStep(atual).title : pending ? "Preparando uma resposta" : "Aguardando uma mensagem"}</p>
        {recentes.length > 0 ? (
          <ol aria-label="Últimas ações do agente">
            {recentes.map((acao) => <li key={acao.id}>{acao.title}</li>)}
          </ol>
        ) : null}
      </div>
      <h3 className="sheet-label">Memória de conversas</h3>
      <div className="agent-memory">
        <p role="status">{descricaoMemoria(estadoHistorico)}</p>
        <p>{conversaAberta ? "Ao reabrir esta conversa, a consultora retoma o histórico da sessão." : "Abra uma conversa salva para retomar o histórico da sessão."}</p>
        <p>Em uma nova conversa, a consultora pode recuperar trechos relevantes de outros chats desta aplicação e de arquivos locais preparados para ela.</p>
        <p>O histórico do Codex e de outros aplicativos fica fora desse acesso.</p>
        {memoria ? (
          <dl className="agent-memory-stats">
            <div><dt>Conversas salvas</dt><dd>{memoria.conversas_salvas}</dd></div>
            <div><dt>Sessões indexadas</dt><dd>{memoria.sessoes_indexadas}</dd></div>
            <div><dt>Arquivos de contexto</dt><dd>{memoria.arquivos_contexto}</dd></div>
            <div><dt>Vetores de busca</dt><dd>{memoria.vetores_indexados}</dd></div>
          </dl>
        ) : null}
      </div>
      <h3 className="sheet-label">Capacidades</h3>
      <ul className="agent-capabilities">
        {CAPACIDADES.map(({ icon: Icon, titulo, texto }) => (
          <li key={titulo}>
            <Icon />
            <div><strong>{titulo}</strong><p>{texto}</p></div>
          </li>
        ))}
      </ul>
      <h3 className="sheet-label">Base de conhecimento</h3>
      <ul className="agent-sources">
        <li>Planilha da despensa e registros salvos de ingredientes, preços e compras.</li>
        <li>Fichas de equipamentos e técnicas e referências de pratos por região.</li>
        <li>Receitas de páginas públicas consultadas durante a busca na web.</li>
        <li>Regras de custo e cenários de preço usados no Analytics.</li>
      </ul>
    </Sheet>
  );
}
