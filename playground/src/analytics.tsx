import { useEffect, useState, type FormEvent } from "react";
import { ChartIcon, SearchIcon } from "./icons";
import { getAnalises, saveTaxas } from "./lib/api";
import { brl, quantidade } from "./lib/format";
import { Sheet } from "./sheet";
import type { AnalisePreco, PlanoPrecificacao, RespostaAnalytics, ResultadoPreco } from "./types";

const PERCENTUAL = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 2 });

type AnalyticsSheetProps = {
  versao: string;
  onClose: () => void;
  onPesquisar: () => void;
  onEscolherPreco: (pratoId: number, preco: number, cotacao: string) => void;
  onTaxasSalvas?: () => void;
};

export function AnalyticsSheet({ versao, onClose, onPesquisar, onEscolherPreco, onTaxasSalvas }: AnalyticsSheetProps) {
  const [dados, setDados] = useState<RespostaAnalytics | null>(null);
  const [erro, setErro] = useState(false);
  const [tentativa, setTentativa] = useState(0);

  useEffect(() => {
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
  }, [versao, tentativa]);

  const analises = dados ? [...dados.analises, ...dados.previas] : [];
  return (
    <Sheet titleId="analytics-titulo" title="Analytics" icon={<ChartIcon />} onClose={onClose} wide>
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
          <TaxasDoNegocio dados={dados} onSalvo={(resposta) => { setDados(resposta); onTaxasSalvas?.(); }} />
          {analises.length || dados.pendentes.length ? (
            <ul className="analises" aria-label="Análises dos seus pratos">
              {analises.map((analise) => <AnaliseCard key={analise.prato_id} analise={analise} onEscolherPreco={onEscolherPreco} />)}
              {dados.pendentes.map((prato) => (
                <li key={prato.prato_id} className="analise">
                  <header className="analise-topo"><h3>{prato.prato}</h3><span className="analise-status">Custo pendente</span></header>
                  <p className="sheet-note">{prato.motivo}</p>
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
    </Sheet>
  );
}

function TaxasDoNegocio({ dados, onSalvo }: { dados: RespostaAnalytics; onSalvo: (dados: RespostaAnalytics) => void }) {
  const { perfil_precificacao: atual, planos, referencias } = dados;
  const [plano, setPlano] = useState(atual.plano);
  const [comissao, setComissao] = useState(porcentagem(atual.comissao));
  const [pagamento, setPagamento] = useState(porcentagem(atual.pagamento));
  const [online, setOnline] = useState(atual.pagamento_online);
  const [imposto, setImposto] = useState(atual.imposto === null ? "" : porcentagem(atual.imposto));
  const [salvando, setSalvando] = useState(false);
  const [aviso, setAviso] = useState<{ erro: boolean; texto: string } | null>(null);

  function selecionarPlano(valor: PlanoPrecificacao) {
    setPlano(valor);
    setComissao(porcentagem(planos[valor].comissao));
    setPagamento(porcentagem(planos[valor].pagamento));
    setAviso(null);
  }

  async function salvar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    setSalvando(true);
    setAviso(null);
    try {
      const resposta = await saveTaxas({
        plano, comissao: Number(comissao) / 100, pagamento: Number(pagamento) / 100,
        pagamento_online: online, imposto: imposto === "" ? null : Number(imposto) / 100,
      });
      onSalvo(resposta);
      setAviso({ erro: false, texto: "Taxas salvas. Preços e margens atualizados." });
    } catch (erro) {
      setAviso({ erro: true, texto: erro instanceof Error ? erro.message : "Não consegui salvar as taxas." });
    } finally {
      setSalvando(false);
    }
  }

  return (
    <details className="analytics-taxas">
      <summary>
        <strong>Taxas e impostos</strong>
        <span>{planos[atual.plano].rotulo} · {PERCENTUAL.format(atual.comissao + (atual.pagamento_online ? atual.pagamento : 0))} iFood · {
          atual.imposto === null ? "impostos pendentes" : `${PERCENTUAL.format(atual.imposto)} de imposto`
        }</span>
      </summary>
      <form onSubmit={salvar}>
        <div className="analytics-taxas-campos">
          <label>Plano
            <select value={plano} onChange={(e) => selecionarPlano(e.target.value as PlanoPrecificacao)}>
              {Object.entries(planos).map(([valor, item]) => <option key={valor} value={valor}>{item.rotulo}</option>)}
            </select>
          </label>
          <label>Comissão iFood (%)
            <input type="number" min="0" max="99.99" step="0.01" value={comissao} required readOnly={plano !== "personalizado"} onChange={(e) => setComissao(e.target.value)} />
          </label>
          <label>Taxa de pagamento (%)
            <input type="number" min="0" max="99.99" step="0.01" value={pagamento} required readOnly={plano !== "personalizado"} onChange={(e) => setPagamento(e.target.value)} />
          </label>
          <label>Alíquota efetiva de imposto (%)
            <input type="number" min="0" max="99.99" step="0.01" value={imposto} placeholder="A confirmar" onChange={(e) => setImposto(e.target.value)} />
          </label>
        </div>
        <label className="analytics-taxas-online"><input type="checkbox" checked={online} onChange={(e) => setOnline(e.target.checked)} />Pagamento feito pelo iFood</label>
        <p className="sheet-note">O imposto é simulado sobre o preço da venda. No Simples Nacional, 4% é a alíquota inicial do Anexo I para comércio com receita acumulada de até {brl(180000)} em 12 meses. Informe a alíquota efetiva da sua loja.</p>
        <button type="submit" className="mini-btn forte" disabled={salvando}>{salvando ? "Salvando…" : "Salvar taxas"}</button>
        {aviso ? <p className={aviso.erro ? "sheet-note error" : "sheet-note"} role={aviso.erro ? "alert" : "status"}>{aviso.texto}</p> : null}
        <p className="analytics-taxas-fontes">
          Fontes consultadas em {referencias.consultadas_em.split("-").reverse().join("/")}.{" "}
          <a href={referencias.ifood} target="_blank" rel="noreferrer">Planos iFood</a>{" · "}
          <a href={referencias.simples} target="_blank" rel="noreferrer">LC 123, Anexo I</a>
        </p>
      </form>
    </details>
  );
}

function porcentagem(taxa: number) {
  return String(Math.round(taxa * 10000) / 100);
}

function AnaliseCard({ analise, onEscolherPreco }: {
  analise: AnalisePreco;
  onEscolherPreco: AnalyticsSheetProps["onEscolherPreco"];
}) {
  const {
    prato, status, porcoes, cmv_total, cmv_por_porcao, cmv_estimado, preco_minimo, preco_minimo_com_sobra, taxa_impostos,
    cenarios, cenario_recomendado, recomendacao, preco_escolhido, resultado_escolhido, lucro_alvo, custos_fixos,
  } = analise;

  return (
    <li className="analise">
      <article>
        <header className="analise-topo">
          <h3>{prato}</h3>
          <span className={status === "registrado" ? "analise-status registrado" : "analise-status"}>
            {status === "registrado" ? "No cardápio" : status === "previa" ? "Prévia da ficha" : "Escolha o preço"}
          </span>
        </header>
        <dl className="analise-custos">
          <div><dt>Custo do lote ({quantidade(porcoes, porcoes === 1 ? "porção" : "porções")})</dt><dd>{brl(cmv_total)}</dd></div>
          <div><dt>CMV por porção</dt><dd>{brl(cmv_por_porcao)}</dd></div>
          <div><dt>Mínimo para cobrir custos</dt><dd>{brl(preco_minimo)}</dd></div>
          <div><dt>Mínimo com sobra</dt><dd>{brl(preco_minimo_com_sobra)}</dd></div>
        </dl>
        <p className="sheet-note dica">
          {taxa_impostos === null ? "Projeção provisória antes de impostos. " : ""}
          {cmv_estimado ? "O CMV inclui estimativas. " : ""}
          Os valores por porção consideram ingredientes e descontos da venda. Os custos fixos ficam na meta de volume.
        </p>
        {preco_escolhido !== null && resultado_escolhido ? (
          <section className="analise-escolhido" aria-label="Preço no cardápio">
            <div className="analise-cenario-topo"><h4>Preço no cardápio</h4><b>{brl(preco_escolhido)}</b></div>
            <DetalhesPreco resultado={resultado_escolhido} />
          </section>
        ) : null}
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
                <DetalhesPreco resultado={cenario.com_preco_sugerido} />
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
        {lucro_alvo !== null || custos_fixos !== null ? (
          <p className="sheet-note dica analise-meta">
            {[
              lucro_alvo !== null ? `Meta de lucro de ${brl(lucro_alvo)}` : "",
              custos_fixos !== null ? `Custos fixos de ${brl(custos_fixos)}` : "",
            ].filter(Boolean).join(" · ")}
          </p>
        ) : null}
        {recomendacao ? <p className="analise-nota">{recomendacao}</p> : null}
        {status === "proposta" ? <p className="sheet-note dica">Escolha um preço para revisar a mensagem de registro no chat.</p> : null}
      </article>
    </li>
  );
}

function DetalhesPreco({ resultado }: { resultado: ResultadoPreco }) {
  return (
    <dl className="analise-detalhes">
      <div><dt>Comissão iFood</dt><dd>{brl(resultado.taxa_comissao)}</dd></div>
      <div><dt>Taxa de pagamento</dt><dd>{brl(resultado.taxa_pagamento)}</dd></div>
      <div><dt>Repasse iFood</dt><dd>{brl(resultado.repasse_ifood)}</dd></div>
      <div><dt>Impostos</dt><dd>{resultado.impostos === null ? "A confirmar" : brl(resultado.impostos)}</dd></div>
      <div><dt>{resultado.impostos === null ? "Após taxas iFood" : "Líquido após descontos"}</dt><dd>{brl(resultado.recebe_liquido)}</dd></div>
      <div><dt>Sobra por porção</dt><dd>{brl(resultado.lucro)}</dd></div>
      {resultado.margem_sobre_liquido !== null ? (
        <div><dt>Margem sobre o líquido</dt><dd>{PERCENTUAL.format(resultado.margem_sobre_liquido)}</dd></div>
      ) : null}
    </dl>
  );
}
