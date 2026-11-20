import { useState } from "react";
import { brl, lerNumero } from "./lib/format";
import { ACEITAR_ANALISE_DIVULGACAO, PEDIR_ANALISE_DIVULGACAO } from "./lib/prompts";
import type { AcaoJornada, Estado, PlanoDivulgacao, PratoEscolhido } from "./types";
import "./divulgacao.css";

const FONTES = [
  {
    titulo: "Anúncios",
    url: "https://blog-parceiros.ifood.com.br/anuncios/",
  },
  {
    titulo: "Campanha Inteligente",
    url: "https://blog-parceiros.ifood.com.br/campanha-inteligente/",
  },
  {
    titulo: "Central de Crescimento",
    url: "https://blog-parceiros.ifood.com.br/central-de-crescimento/",
  },
];

type Props = {
  estado: Estado;
  bloqueado: boolean;
  onEscolha: (acao: AcaoJornada, visivel: string, mensagem?: string) => void;
  onEnviar: (mensagem: string) => void;
  onAnalytics?: () => void;
};

/** O interesse em divulgação é uma escolha posterior ao cardápio, nunca uma contratação. */
export function DivulgacaoChat({ estado, bloqueado, onEscolha, onEnviar, onAnalytics }: Props) {
  const todosOsPratos = estado.pratos_escolhidos ?? [];
  const idsAtivos = estado.jornada?.pratos_ativos;
  const pratos = idsAtivos === undefined ? todosOsPratos : todosOsPratos.filter(({ id }) => idsAtivos.includes(id));
  const aprovados = pratos.filter((prato) => prato.status === "aceito" && prato.preco_venda !== null && !prato.bloqueios?.length);
  if (!aprovados.length || aprovados.length !== pratos.length) return null;
  const escolha = estado.jornada?.controle?.divulgacao ?? "";
  const plano = estado.jornada?.plano_divulgacao;

  return <section className="divulgacao-chat" aria-labelledby="divulgacao-titulo">
    <h3 id="divulgacao-titulo">{plano ? "Divulgação preparada" : "Divulgação do cardápio"}</h3>
    {!escolha ? <>
      <p>A consultora prepara as mensagens, o plano e o impacto na margem do cardápio.</p>
      <div className="chips" role="group" aria-label="Avaliar publicidade do iFood">
        <button type="button" className="chip" disabled={bloqueado} onClick={() => onEscolha("interesse_divulgacao", ACEITAR_ANALISE_DIVULGACAO, ACEITAR_ANALISE_DIVULGACAO)}>Preparar divulgação</button>
        <button type="button" className="chip" disabled={bloqueado} onClick={() => onEscolha("dispensar_divulgacao", "Agora não quero publicidade.")}>Deixar para depois</button>
      </div>
    </> : escolha === "dispensada" ? <>
      <p>Você pode começar com o cardápio atual e voltar à divulgação quando quiser.</p>
      <button type="button" className="link-btn" disabled={bloqueado} onClick={() => onEscolha("interesse_divulgacao", ACEITAR_ANALISE_DIVULGACAO, ACEITAR_ANALISE_DIVULGACAO)}>Preparar divulgação</button>
    </> : <>
      {plano ? <MateriaisDivulgacao key={plano.criado_em} plano={plano} /> : <p>A consultora pode concluir o plano com os dados que você já informou.</p>}
      <div className="divulgacao-acoes">
        {!plano ? <button type="button" className="chip" disabled={bloqueado} onClick={() => onEnviar(PEDIR_ANALISE_DIVULGACAO)}>Preparar plano no chat</button> : null}
        {onAnalytics ? <button type="button" className="mini-btn" onClick={onAnalytics}>Abrir Analytics</button> : null}
        {!plano ? <button type="button" className="link-btn" disabled={bloqueado} onClick={() => onEscolha("dispensar_divulgacao", "Vou deixar a publicidade para depois.")}>Deixar para depois</button> : null}
      </div>
      <SimulacaoDivulgacao pratos={aprovados} />
      <details className="divulgacao-simulacao">
        <summary>Fontes oficiais do iFood</summary>
        <ul className="divulgacao-fontes">
          {FONTES.map((fonte) => <li key={fonte.titulo}>
            <a href={fonte.url} target="_blank" rel="noreferrer">{fonte.titulo}</a>
          </li>)}
        </ul>
      </details>
    </>}
  </section>;
}

function MateriaisDivulgacao({ plano }: { plano: PlanoDivulgacao }) {
  const [aviso, setAviso] = useState("");
  const { campanha, prato, volume } = plano;
  const margem = campanha.margem_minima == null ? null
    : new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 2 }).format(campanha.margem_minima);

  async function copiarMensagem() {
    try {
      await navigator.clipboard.writeText(campanha.legenda);
      setAviso("Mensagem copiada.");
    } catch {
      setAviso("Selecione a mensagem para copiar.");
    }
  }

  return <>
    <p><strong>{prato.nome}</strong> · {campanha.canal} · {brl(campanha.investimento)} previstos</p>
    <p>{campanha.estrategia}</p>
    <p className="divulgacao-nota">Os materiais estão salvos. A publicação no iFood é feita pelo Portal do Parceiro.</p>
    <details className="divulgacao-simulacao">
      <summary>Materiais e ações</summary>
      <dl className="divulgacao-plano-dados">
        <div><dt>Objetivo</dt><dd>{campanha.objetivo}</dd></div>
        <div><dt>Preço por porção</dt><dd>{brl(prato.preco_venda)}</dd></div>
        <div><dt>Margem mínima {campanha.origem_margem === "cenario_escolhido" ? "do cenário aprovado" : ""}</dt><dd>{margem ?? "Ainda sem meta"}</dd></div>
        <div><dt>Volume de referência</dt><dd>{volume.porcoes} porções em {volume.dias} {volume.dias === 1 ? "dia" : "dias"}{volume.origem === "lote_hipotetico" ? " · hipótese" : ""}</dd></div>
        {campanha.lucro_previsto_por_porcao != null && <div><dt>Lucro estimado por porção</dt><dd>{brl(campanha.lucro_previsto_por_porcao)}</dd></div>}
      </dl>
      <strong>{campanha.titulo}</strong>
      <p>{campanha.descricao}</p>
      <blockquote>{campanha.legenda}</blockquote>
      <button type="button" className="mini-btn" onClick={() => void copiarMensagem()}>Copiar mensagem</button>
      {aviso ? <p role="status">{aviso}</p> : null}
      <ol>{campanha.tarefas.map((tarefa) => <li key={tarefa}>{tarefa}</li>)}</ol>
    </details>
  </>;
}

function SimulacaoDivulgacao({ pratos }: { pratos: PratoEscolhido[] }) {
  const [pratoId, setPratoId] = useState(pratos[0]?.id ?? 0);
  const [investimento, setInvestimento] = useState("");
  const [porcoes, setPorcoes] = useState("");
  const [periodo, setPeriodo] = useState("dia");
  const prato = pratos.find(({ id }) => id === pratoId) ?? pratos[0];
  const valor = lerNumero(investimento);
  const volume = Number(porcoes);
  const custo = prato?.cmv_total != null && prato.porcoes != null && prato.porcoes > 0 ? prato.cmv_total / prato.porcoes : prato?.cmv_porcao;
  const preco = prato?.preco_venda;
  const valido = Number.isFinite(valor) && valor >= 0 && valor <= 1_000_000 && Number.isSafeInteger(volume) && volume > 0 && volume <= 1_000_000
    && custo != null && Number.isFinite(custo) && custo >= 0 && preco != null && Number.isFinite(preco) && preco > 0;
  const informou = investimento.trim() !== "" && porcoes.trim() !== "";
  const lucroBase = valido ? preco! * 0.90 - custo! : 0;
  const rateio = valido ? valor / volume : 0;
  const lucroComDivulgacao = lucroBase - rateio;
  const minimoComDivulgacao = valido ? Math.ceil(((custo! + rateio) / 0.90) * 100 - 1e-8) / 100 : 0;

  return <details className="divulgacao-simulacao">
    <summary>Simular com meus valores</summary>
    <p className="divulgacao-nota">Use um investimento que você está considerando e as porções que espera vender no mesmo período. É uma projeção; vendas reais ficam separadas no Analytics.</p>
    <div className="divulgacao-campos">
      <label className="field divulgacao-prato"><span>Prato para simular</span><select value={prato?.id ?? ""} onChange={(event) => setPratoId(Number(event.target.value))}>
        {pratos.map((item) => <option key={item.id} value={item.id}>{item.nome}</option>)}
      </select></label>
      <label className="field"><span>Período da projeção</span><select value={periodo} onChange={(event) => setPeriodo(event.target.value)}>
        <option value="dia">Um dia</option><option value="semana">Uma semana</option><option value="mes">Um mês</option>
      </select></label>
      <label className="field"><span>Investimento no período (R$)</span><input type="text" inputMode="decimal" value={investimento} onChange={(event) => setInvestimento(event.target.value)} placeholder="ex.: 30,00" /></label>
      <label className="field"><span>Porções esperadas no período</span><input type="number" min="1" max="1000000" step="1" value={porcoes} onChange={(event) => setPorcoes(event.target.value)} placeholder="ex.: 20" /></label>
    </div>
    {informou && !valido ? <p className="divulgacao-nota error" role="alert">Informe um investimento de zero a R$ 1.000.000 e de 1 a 1.000.000 porções inteiras no mesmo período.</p> : null}
    {valido ? <div className="divulgacao-projecao" aria-live="polite">
      <p><strong>Projeção para {periodo === "dia" ? "um dia" : periodo === "semana" ? "uma semana" : "um mês"}</strong> · preço atual {brl(preco!)} · CMV por porção {brl(custo!)}</p>
      <dl>
        <div><dt>Lucro por porção após a taxa de 10% e o CMV</dt><dd>{brl(lucroBase)}</dd></div>
        <div><dt>Divulgação por porção, se vender o volume esperado</dt><dd>{brl(rateio)}</dd></div>
        <div><dt>Sobra por porção após divulgação</dt><dd>{brl(lucroComDivulgacao)}</dd></div>
        <div><dt>Sobra no período após divulgação</dt><dd>{brl(lucroBase * volume - valor)}</dd></div>
        <div><dt>Mínimo por porção para cobrir CMV, divulgação e taxa de 10%</dt><dd>{brl(minimoComDivulgacao)}</dd></div>
      </dl>
      {lucroComDivulgacao <= 0 ? <p className="divulgacao-alerta">Nesse cenário, a divulgação consome a margem do prato. Reveja o investimento, o volume esperado ou o preço antes de contratar.</p> : null}
      <p className="divulgacao-nota">Conta: 90% do preço − CMV − investimento ÷ porções esperadas. A projeção não inclui custos fixos, descontos ou outros gastos da operação.</p>
    </div> : null}
    <p className="divulgacao-nota">A projeção usa um orçamento de divulgação separado dos R$ 80,00 de complementos.</p>
  </details>;
}
