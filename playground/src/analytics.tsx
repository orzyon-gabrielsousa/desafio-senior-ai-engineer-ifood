import { useEffect, useState } from "react";
import { ChartIcon, SearchIcon } from "./icons";
import { getAnalises } from "./lib/api";
import { brl, quantidade } from "./lib/format";
import { pendenciasDoPrato, situacaoDoPrato } from "./lib/pratos";
import { Sheet } from "./sheet";
import { VendasAnalytics } from "./vendas";
import type { AnalisePreco, PeriodoVendas, PratoEscolhido, RespostaAnalytics, ResultadoPreco } from "./types";

const PERCENTUAL = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 2 });

const ABAS_ANALYTICS = [
  { id: "vendas", titulo: "Vendas" },
  { id: "pratos", titulo: "Meus pratos" },
] as const;

type AbaAnalytics = (typeof ABAS_ANALYTICS)[number]["id"];

type AnalyticsSheetProps = {
  versao: string;
  abaInicial?: AbaAnalytics;
  pratos?: PratoEscolhido[];
  onClose: () => void;
  onPesquisar: () => void;
  onEscolherPreco: (pratoId: number, preco: number, cotacao: string) => void;
  onRevisarPrato: (pratoId: number, nome: string) => void;
  onTaxasSalvas?: () => void;
  periodoInicial?: PeriodoVendas;
  onVendasAtualizadas?: () => void;
};

export function AnalyticsSheet({ versao, abaInicial = "pratos", pratos, onClose, onPesquisar, onEscolherPreco, onRevisarPrato, periodoInicial, onVendasAtualizadas }: AnalyticsSheetProps) {
  const [aba, setAba] = useState(abaInicial);
  const [dados, setDados] = useState<RespostaAnalytics | null>(null);
  const [erro, setErro] = useState(false);
  const [tentativa, setTentativa] = useState(0);

  useEffect(() => { setAba(abaInicial); }, [abaInicial]);
  useEffect(() => {
    if (aba !== "pratos") return;
    let atual = true;
    function carregar() {
      getAnalises()
        .then((resposta) => {
          if (atual) {
            setDados(resposta);
            setErro(false);
          }
        })
        .catch(() => {
          if (atual) setErro(true);
        });
    }
    carregar();
    window.addEventListener("focus", carregar);
    return () => {
      atual = false;
      window.removeEventListener("focus", carregar);
    };
  }, [aba, versao, tentativa]);

  const analises = dados ? [...dados.analises, ...dados.previas] : [];
  return (
    <Sheet titleId="analytics-titulo" title="Analytics" icon={<ChartIcon />} onClose={onClose} wide>
      <div className="tabs" role="tablist" aria-label="Analytics">
        {ABAS_ANALYTICS.map(({ id, titulo }) => (
          <button key={id} id={`analytics-aba-${id}`} aria-controls={`analytics-painel-${id}`} type="button" role="tab"
            aria-selected={aba === id} className={aba === id ? "tab on" : "tab"} onClick={() => setAba(id)}>{titulo}</button>
        ))}
      </div>
      <div id="analytics-painel-vendas" role="tabpanel" aria-labelledby="analytics-aba-vendas" hidden={aba !== "vendas"}>
        <VendasAnalytics periodoInicial={periodoInicial} pratos={pratos} versao={versao} onAtualizadas={onVendasAtualizadas} />
      </div>
      <div id="analytics-painel-pratos" role="tabpanel" aria-labelledby="analytics-aba-pratos" hidden={aba !== "pratos"}>
        {erro ? (
          <div className="sheet-note error" role="alert">
            Não consegui carregar as análises.{" "}
            <button type="button" className="link-btn" onClick={() => setTentativa((valor) => valor + 1)}>
              Tentar de novo
            </button>
          </div>
        ) : dados === null ? (
          <p className="sheet-note" role="status">Carregando as análises…</p>
        ) : (
          <>
            <RegrasDoDesafio />
            {analises.length || dados.pendentes.length ? (
              <ul className="analises" aria-label="Análises dos seus pratos">
                {analises.map((analise) => <AnaliseCard key={analise.prato_id} analise={analise} pratoAtual={pratos?.find(({ id }) => id === analise.prato_id)} onEscolherPreco={onEscolherPreco} onRevisarPrato={onRevisarPrato} />)}
                {dados.pendentes.map((prato) => (
                  <li key={prato.prato_id} className="analise">
                    <header className="analise-topo"><h3>{prato.prato}</h3><span className="analise-status">Custo pendente</span></header>
                    <p className="sheet-note">{prato.motivo}</p>
                    <button type="button" className="mini-btn" onClick={() => onRevisarPrato(prato.prato_id, prato.prato)}>Resolver no chat</button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="analytics-vazio">
                <h3>Ainda não há pratos para precificar</h3>
                <p>Monte a ficha da receita para ver o custo e os preços por porção.</p>
                <button type="button" className="mini-btn forte" onClick={onPesquisar}><SearchIcon />Pesquisar receitas</button>
              </div>
            )}
          </>
        )}
      </div>
    </Sheet>
  );
}

function RegrasDoDesafio() {
  return (
    <section className="analytics-taxas" aria-label="Regras de preço do desafio">
      <strong>Taxa fixa do desafio: 10% sobre a venda</strong>
      <p className="sheet-note">Você recebe 90% do preço. O lucro estimado é esse valor menos o custo dos ingredientes (CMV).</p>
      <p className="sheet-note">Preço mínimo = CMV por porção ÷ 0,90, arredondado para cima ao centavo. Compare os cenários e escolha quanto cobrar.</p>
    </section>
  );
}

function AnaliseCard({ analise, pratoAtual, onEscolherPreco, onRevisarPrato }: {
  analise: AnalisePreco;
  pratoAtual?: PratoEscolhido;
  onEscolherPreco: AnalyticsSheetProps["onEscolherPreco"];
  onRevisarPrato: AnalyticsSheetProps["onRevisarPrato"];
}) {
  const {
    prato, status, porcoes, cmv_total, cmv_por_porcao, cmv_estimado, preco_minimo, preco_minimo_com_sobra,
    cenarios, cenario_recomendado, recomendacao, preco_escolhido, resultado_escolhido, lucro_alvo, custos_fixos,
  } = analise;
  const revisao = pratoAtual && situacaoDoPrato(pratoAtual).etapa === "revisao";
  const recomendado = cenario_recomendado === null ? null : cenarios[cenario_recomendado - 1];
  const maiorPrecoSimulado = cenarios.length ? Math.max(...cenarios.map(({ preco_sugerido }) => preco_sugerido)) : null;

  return (
    <li className="analise">
      <article>
        <header className="analise-topo">
          <h3>{prato}</h3>
          <span className={status === "registrado" && !revisao ? "analise-status registrado" : "analise-status"}>
            {revisao ? "Precisa de revisão" : status === "registrado" ? "Aprovado no cardápio" : status === "previa" ? "Aguardando seu aceite" : "Aguardando seu preço"}
          </span>
        </header>
        {revisao ? <div className="analise-aceite">
          <p>O preço está registrado, mas surgiu uma pendência nas condições de produção: {pendenciasDoPrato(pratoAtual)[0] || "precisamos conferir novamente a viabilidade da cozinha."}</p>
          <button type="button" className="mini-btn forte" onClick={() => onRevisarPrato(analise.prato_id, prato)}>Revisar com a consultora</button>
        </div> : null}
        <dl className="analise-custos">
          <div><dt>Custo do lote ({quantidade(porcoes, porcoes === 1 ? "porção" : "porções")})</dt><dd>{brl(cmv_total)}</dd></div>
          <div><dt>CMV por porção</dt><dd>{brl(cmv_por_porcao)}</dd></div>
          <div><dt>Mínimo para cobrir custos</dt><dd>{brl(preco_minimo)}</dd></div>
          <div><dt>Mínimo com sobra</dt><dd>{brl(preco_minimo_com_sobra)}</dd></div>
          {status !== "previa" && recomendado ? <div><dt>Preço recomendado</dt><dd>{brl(recomendado.preco_sugerido)}</dd></div> : null}
          {status !== "previa" && maiorPrecoSimulado !== null ? <div><dt>Maior preço simulado</dt><dd>{brl(maiorPrecoSimulado)}</dd></div> : null}
        </dl>
        <p className="sheet-note dica">
          {cmv_estimado ? "O CMV inclui estimativas. " : ""}
          Os valores por porção consideram os ingredientes e a taxa de 10% do desafio. São exibidos com arredondamento ao centavo; o cálculo usa o CMV do lote sem arredondamento por porção. Os custos fixos ficam na meta de volume.
        </p>
        {preco_escolhido !== null && resultado_escolhido ? (
          <section className="analise-escolhido" aria-label="Preço no cardápio">
            <div className="analise-cenario-topo"><h4>Preço no cardápio</h4><b>{brl(preco_escolhido)}</b></div>
            <DetalhesPreco resultado={resultado_escolhido} preco={preco_escolhido} cmv={cmv_por_porcao} />
          </section>
        ) : null}
        {status === "previa" ? (
          <div className="analise-aceite">
            <p>Este custo é uma prévia. Revise e aceite a versão do prato no chat para comparar os cenários de preço.</p>
            <button type="button" className="mini-btn forte" onClick={() => onRevisarPrato(analise.prato_id, prato)}>Revisar a versão no chat</button>
          </div>
        ) : <details className="analise-comparacao" open={status === "proposta"}>
          <summary>{status === "registrado" ? "Ver os cenários de referência" : "Compare os cenários e escolha o preço"}</summary>
          <ul className="analise-cenarios" aria-label="Cenários de preço">
          {cenarios.map((cenario, indice) => {
            const recomendado = indice + 1 === cenario_recomendado;
            return (
              <li
                key={cenario.nome}
                className={recomendado ? "analise-cenario recomendado" : "analise-cenario"}
              >
                <div className="analise-cenario-topo">
                  <h4>{cenario.nome}</h4>
                  <b>{brl(cenario.preco_sugerido)}</b>
                </div>
                {recomendado ? <p className="analise-recomendado">Recomendado pela consultora</p> : null}
                <DetalhesPreco resultado={cenario.com_preco_sugerido} preco={cenario.preco_sugerido} cmv={cmv_por_porcao} />
                {cenario.porcoes_para_meta != null ? (
                  <p className="analise-volume">
                    {quantidade(cenario.porcoes_para_meta, cenario.porcoes_para_meta === 1 ? "porção" : "porções")}
                    {lucro_alvo !== null ? " para atingir a meta" : " para cobrir os custos fixos"}
                  </p>
                ) : null}
                {status === "proposta" ? (
                  <button
                    type="button"
                    className="mini-btn forte analise-escolher"
                    aria-label={`Escolher ${brl(cenario.preco_sugerido)} por porção para ${prato}`}
                    onClick={() => onEscolherPreco(analise.prato_id, cenario.preco_sugerido, analise.cotacao)}
                  >
                    Escolher este preço
                  </button>
                ) : null}
              </li>
            );
          })}
          </ul>
        </details>}
        {lucro_alvo !== null || custos_fixos !== null ? (
          <p className="sheet-note dica analise-meta">
            {[
              lucro_alvo !== null ? `Meta de lucro de ${brl(lucro_alvo)}` : "",
              custos_fixos !== null ? `Custos fixos de ${brl(custos_fixos)}` : "",
            ].filter(Boolean).join(" · ")}
          </p>
        ) : null}
        {status === "proposta" && recomendacao ? <p className="analise-nota">{recomendacao}</p> : null}
        {status === "proposta" ? <p className="sheet-note dica">Ao escolher, a consultora confere os dados e registra esse preço pelo chat.</p> : null}
      </article>
    </li>
  );
}

function DetalhesPreco({ resultado, preco, cmv }: { resultado: ResultadoPreco; preco: number; cmv: number }) {
  return (
    <>
      <dl className="analise-detalhes">
        <div><dt>Taxa de 10%</dt><dd>{brl(resultado.taxa_comissao)}</dd></div>
        <div><dt>Você recebe (90%)</dt><dd>{brl(resultado.recebe_liquido)}</dd></div>
        <div><dt>CMV por porção</dt><dd>{brl(cmv)}</dd></div>
        <div><dt>Lucro por porção</dt><dd>{brl(resultado.lucro)}</dd></div>
        {resultado.margem_sobre_liquido !== null ? (
          <div><dt>Margem sobre o repasse</dt><dd>{PERCENTUAL.format(resultado.margem_sobre_liquido)}</dd></div>
        ) : null}
      </dl>
      <p className="sheet-note">90% × {brl(preco)} − {brl(cmv)} ≈ {brl(resultado.lucro)} de lucro estimado por porção.</p>
    </>
  );
}
