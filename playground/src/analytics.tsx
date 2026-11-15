import { useEffect, useMemo, useState } from "react";
import { ChartIcon, SparkIcon, StoreIcon } from "./icons";
import { getAnalytics } from "./lib/api";
import { brl } from "./lib/format";
import { Sheet } from "./sheet";
import type { Analytics, EscopoId, Local, MaisVendido, Visao } from "./types";

const ABAS: { id: Visao; rotulo: string }[] = [
  { id: "prioridade", rotulo: "Para a sua loja" },
  { id: "zona", rotulo: "Zona" },
  { id: "cidade", rotulo: "Cidade" },
  { id: "estado", rotulo: "Estado" },
  { id: "regiao", rotulo: "Região" },
  { id: "pais", rotulo: "País" },
];

const ROTULO: Record<EscopoId, string> = { zona: "Zona", cidade: "Cidade", estado: "Estado", regiao: "Região", pais: "País" };

function textoDoLocal(local: Local): string {
  const lugar = local.cidade && local.estado ? `${local.cidade}/${local.estado}` : local.estado_nome;
  return [[local.zona, lugar].filter(Boolean).join(", "), local.regiao ? `região ${local.regiao}` : ""].filter(Boolean).join(" · ");
}

type AnalyticsSheetProps = {
  onClose: () => void;
  onNegocio: () => void;
  /** Preenche a conversa para a consultora detalhar os ingredientes do prato escolhido. */
  onAsk: (prato: string) => void;
};

export function AnalyticsSheet({ onClose, onNegocio, onAsk }: AnalyticsSheetProps) {
  const [visao, setVisao] = useState<Visao>("prioridade");
  const [categoria, setCategoria] = useState<string | null>(null);
  const [dados, setDados] = useState<Analytics | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    let atual = true;
    setCarregando(true);
    getAnalytics(visao)
      .then((resposta) => {
        if (!atual) return;
        setDados(resposta);
        setErro(null);
      })
      .catch((falha: unknown) => {
        if (atual) setErro(falha instanceof Error ? falha.message : "Não consegui ler o Analytics.");
      })
      .finally(() => {
        if (atual) setCarregando(false);
      });
    return () => {
      atual = false;
    };
  }, [visao]);

  const disponiveis = useMemo(() => new Set(dados?.escopos_disponiveis.map((escopo) => escopo.escopo)), [dados]);
  const categorias = useMemo(() => [...new Set((dados?.ranking ?? []).map((prato) => prato.categoria))], [dados]);
  const lista = (dados?.ranking ?? []).filter((prato) => !categoria || prato.categoria === categoria);
  const onde = dados?.escopos_disponiveis.find((escopo) => escopo.escopo === visao)?.onde;

  const escolher = (proxima: Visao) => {
    setVisao(proxima);
    setCategoria(null);
  };

  return (
    <Sheet titleId="analytics-titulo" title="Analytics" icon={<ChartIcon />} onClose={onClose} wide>
      <p className="sheet-note">Os pratos que mais vendem, com o preço médio, priorizados pelo lugar da sua loja: zona, cidade, estado, região e país.</p>
      <p className="nota-exemplo">{dados?.aviso ?? "Base de exemplo criada para a demonstração do desafio: dados ilustrativos, não são vendas reais."}</p>

      {dados ? (
        dados.local_definido ? (
          <div className="local-card">
            <StoreIcon />
            <div>
              <strong>Sua loja</strong>
              <small>{textoDoLocal(dados.local)}</small>
            </div>
            <button type="button" className="link-btn" onClick={onNegocio}>
              Alterar
            </button>
          </div>
        ) : (
          <div className="local-card falta">
            <StoreIcon />
            <div>
              <strong>A loja ainda não tem localização</strong>
              <small>Informe estado, cidade e zona em Meu negócio para priorizar pelo lugar. Por enquanto vale só o ranking do país.</small>
            </div>
            <button type="button" className="link-btn" onClick={onNegocio}>
              Informar
            </button>
          </div>
        )
      ) : null}

      <div className="escopos" role="tablist" aria-label="Nível do ranking">
        {ABAS.map((aba) => {
          const semDados = aba.id !== "prioridade" && dados !== null && !disponiveis.has(aba.id);
          return (
            <button
              key={aba.id}
              type="button"
              role="tab"
              aria-selected={visao === aba.id}
              className={visao === aba.id ? "escopo on" : "escopo"}
              disabled={semDados}
              title={semDados ? "A base não tem dados deste nível para o lugar da loja" : undefined}
              onClick={() => escolher(aba.id)}
            >
              {aba.rotulo}
            </button>
          );
        })}
      </div>
      <p className="escopo-onde">
        {visao === "prioridade"
          ? dados && !dados.local_definido
            ? "Ranking do país: sem a localização, não há como priorizar pelo lugar da loja."
            : "A zona pesa mais que a cidade, que pesa mais que o estado, a região e o país. Quem lidera perto da loja sobe."
          : onde
            ? `${ROTULO[visao]}: ${onde}.`
            : ""}
      </p>
      {visao !== "prioridade" && dados?.leitura ? <p className="leitura">{dados.leitura}</p> : null}
      {dados?.mensagem && dados.local_definido ? <p className="sheet-note dica">{dados.mensagem}</p> : null}

      {categorias.length > 1 ? (
        <div className="toggle-chips" role="group" aria-label="Filtrar por categoria">
          <button type="button" className={categoria === null ? "toggle on" : "toggle"} aria-pressed={categoria === null} onClick={() => setCategoria(null)}>
            Todas
          </button>
          {categorias.map((nome) => (
            <button key={nome} type="button" className={categoria === nome ? "toggle on" : "toggle"} aria-pressed={categoria === nome} onClick={() => setCategoria(nome)}>
              {nome}
            </button>
          ))}
        </div>
      ) : null}

      {erro ? (
        <p className="sheet-note error" role="alert">
          {erro}
        </p>
      ) : null}
      {carregando && !dados ? <p className="sheet-note">Carregando os mais vendidos…</p> : null}
      {dados && lista.length === 0 && !carregando ? <p className="sheet-note vazio">Nenhum prato neste filtro.</p> : null}

      <ol className="rank" aria-busy={carregando}>
        {lista.map((prato) => (
          <Posicao key={prato.prato} prato={prato} daLoja={dados?.local_definido ?? false} onAsk={onAsk} />
        ))}
      </ol>
      {dados ? <p className="sheet-note dica fonte">Fonte: {dados.fonte}</p> : null}
    </Sheet>
  );
}

function Posicao({ prato, daLoja, onAsk }: { prato: MaisVendido; daLoja: boolean; onAsk: (nome: string) => void }) {
  const nota = prato.prioridade ?? prato.indice ?? 0;
  const rotuloNota = prato.prioridade === undefined ? "Índice de vendas" : daLoja ? "Prioridade para a sua loja" : "Índice de vendas no país";
  return (
    <li className="rank-item">
      <span className="rank-pos">{prato.posicao}</span>
      <div className="rank-main">
        <div className="rank-title">
          <strong>{prato.prato}</strong>
          <span className="tag">{prato.categoria}</span>
        </div>
        <p className="rank-preco">
          <b>{brl(prato.preco_medio)}</b> <small>preço médio · faixa {prato.faixa}</small>
        </p>
        <div className="rank-barra" role="img" aria-label={`${rotuloNota} ${nota} de 100`}>
          <span style={{ width: `${Math.min(100, Math.max(0, nota))}%` }} />
        </div>
        <small className="rank-meta">
          {rotuloNota}: {nota}
        </small>
        <small className="rank-ing">Ingredientes típicos: {prato.ingredientes}</small>
        {prato.onde && prato.onde.length > 0 ? (
          <div className="rank-onde" aria-label="Posição em cada nível">
            {prato.onde.map((nivel) => (
              <span key={nivel.escopo}>
                {ROTULO[nivel.escopo]} #{nivel.posicao}
              </span>
            ))}
          </div>
        ) : null}
        <button type="button" className="mini-btn forte" onClick={() => onAsk(prato.prato)}>
          <SparkIcon />
          Montar com a consultora
        </button>
      </div>
    </li>
  );
}
